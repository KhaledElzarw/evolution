"""Continuous live trading: drive the TickEngine on every newly closed candle.

The loop polls for closed candles (default every 15s), feeds each NEW bar
through ``TickEngine.process`` — the exact code path used by the startup
replay — and publishes fresh read-model data for the API. Missed bars (laptop
sleep, network outage) are simply replayed in order on the next successful
poll: catch-up IS replay, so there is no separate recovery mode.

Threading model: single writer. Only this loop's thread touches the engine;
API request threads read the view, which is updated by whole-object
reassignment (atomic under the GIL — the same pattern the market refresher
used). Trade/order collections are published as fresh copies so a reader never
iterates a list the engine is appending to.

Everything I/O-ish is injected (``fetch_closed``, ``clock``, ``sleep``) so the
loop is testable without network or real time.
"""

from __future__ import annotations

import threading
import time as _time
from collections import deque
from typing import Callable, Protocol

from ..domain.market import MarketSnapshot
from .tick_engine import TickEngine

#: Window of candles each strategy sees (matches the devserver replay WINDOW).
WINDOW = 150

#: Binance klines hard limit — also the deepest gap we can bridge in one fetch.
MAX_FETCH = 1000


class _ViewLike(Protocol):
    """The slice of InMemoryPortfolioView the loop publishes into."""

    mark_price: object
    now: object
    candles: tuple[MarketSnapshot, ...]
    trades_by_wallet: dict
    open_orders_by_wallet: dict
    live_status: dict
    source_status: list


def _set_source(view, source_id: str, status: str, note: str) -> None:
    for entry in view.source_status:
        if entry.get("source_id") == source_id:
            entry["status"] = status
            entry["note"] = note
            return
    view.source_status.append(
        {"source_id": source_id, "status": status, "note": note})


class LiveLoop:
    """Poll for closed candles and trade them through the shared TickEngine."""

    def __init__(
        self,
        *,
        engine: TickEngine,
        view: _ViewLike,
        fetch_closed: Callable[[int], tuple[MarketSnapshot, ...]],
        seed_window: tuple[MarketSnapshot, ...],
        interval_ms: int = 300_000,
        period_seconds: float = 15.0,
        merge_candles: Callable | None = None,
        candle_hooks: tuple[Callable[[MarketSnapshot], None], ...] = (),
        poll_hooks: tuple[Callable[[], None], ...] = (),
        clock: Callable[[], float] = _time.time,
        sleep: Callable[[float], None] = _time.sleep,
    ) -> None:
        """``fetch_closed(limit)`` returns the newest ``limit`` CLOSED candles,
        oldest first. ``seed_window`` is the startup backfill (already replayed
        through the engine). ``candle_hooks`` run after each processed bar (e.g.
        the state snapshotter); ``poll_hooks`` run once per poll (e.g. the
        hourly awareness refresh) — hook failures never stop trading."""

        self.engine = engine
        self.view = view
        self.fetch_closed = fetch_closed
        self.interval_ms = interval_ms
        self.period_seconds = period_seconds
        self.merge_candles = merge_candles
        self.candle_hooks = candle_hooks
        self.poll_hooks = poll_hooks
        self.clock = clock
        self.sleep = sleep
        self._window: deque[MarketSnapshot] = deque(seed_window, maxlen=WINDOW)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> threading.Thread:
        self._thread = threading.Thread(target=self.run, name="live-loop",
                                        daemon=True)
        self._thread.start()
        return self._thread

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    def run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self.sleep(self.period_seconds)

    # -- one poll ------------------------------------------------------------

    def _gap_limit(self) -> int:
        """How many candles to request: enough to bridge the current gap."""

        last = self.engine.last_processed_open_ms
        if last < 0:
            return 5
        now_ms = int(self.clock() * 1000)
        behind = max(0, (now_ms - last) // self.interval_ms)
        return max(5, min(MAX_FETCH, int(behind) + 2))

    def poll_once(self) -> int:
        """Fetch, trade every new closed candle, publish. Returns bars traded."""

        for hook in self.poll_hooks:
            try:
                hook()
            except Exception:  # awareness et al. must never stop trading
                pass

        try:
            closed = self.fetch_closed(self._gap_limit())
        except Exception as exc:
            _set_source(self.view, "binance_public", "degraded",
                        f"live fetch failed: {type(exc).__name__}")
            return 0
        if not closed:
            return 0

        processed = 0
        for snapshot in closed:
            if snapshot.open_time_ms <= self.engine.last_processed_open_ms:
                continue
            self._window.append(snapshot)
            result = self.engine.process(snapshot, tuple(self._window))
            if not result.skipped:
                processed += 1
                for hook in self.candle_hooks:
                    try:
                        hook(snapshot)
                    except Exception:
                        pass

        self._publish(closed, processed)
        return processed

    # -- read-model publication ---------------------------------------------

    def _publish(self, closed: tuple[MarketSnapshot, ...], processed: int) -> None:
        import datetime as dt

        view = self.view
        newest = closed[-1]
        view.mark_price = newest.close
        view.now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        if self.merge_candles is not None:
            view.candles = self.merge_candles(view.candles, closed)
        if processed:
            # Fresh copies: request threads never iterate a list the engine
            # appends to on the next tick.
            view.trades_by_wallet = {k: list(v)
                                     for k, v in self.engine.trades.items()}
            view.open_orders_by_wallet = self.engine.book.snapshot_open()
        now_ms = int(self.clock() * 1000)
        last = self.engine.last_processed_open_ms
        next_close = (last + 2 * self.interval_ms) if last >= 0 else None
        view.live_status = {
            "mode": "live",
            "last_tick_ms": last + self.interval_ms if last >= 0 else None,
            "next_candle_close_ms": next_close,
            "bars_this_poll": processed,
            "caught_up": (now_ms - (last + self.interval_ms)
                          < 2 * self.interval_ms) if last >= 0 else False,
            "total_fills": self.engine.fills,
        }
        _set_source(view, "binance_public", "ok",
                    f"live trading; last closed candle {newest.close_time_ms}")
