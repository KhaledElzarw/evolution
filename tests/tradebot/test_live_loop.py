"""LiveLoop: continuous trading on newly closed candles via the TickEngine.

All I/O is injected: a fake fetcher serves canned candles, a fake clock pins
time, so every scenario (new bar, gap catch-up, fetch failure) is deterministic.
"""

import datetime as dt
from decimal import Decimal

from tradebot.api.devserver import _candle, _permanent_runners, build_market
from tradebot.api.views import InMemoryPortfolioView
from tradebot.application.execution import ExecutionService
from tradebot.application.live_loop import WINDOW, LiveLoop
from tradebot.application.order_book import RestingBook
from tradebot.application.portfolio import seed_portfolio
from tradebot.application.tick_engine import FIVE_MIN_MS, TickEngine
from tradebot.strategies.builtin import BUILTIN_STRATEGIES

NOW = dt.datetime(2026, 7, 20, 12, 0, 0)


def _engine():
    names = [c().metadata().name for c in BUILTIN_STRATEGIES]
    portfolio = seed_portfolio(names, now=NOW, id_factory=lambda h: f"w-{h}")
    by_name = {c().metadata().name: c for c in BUILTIN_STRATEGIES}
    runners = []
    for slot in portfolio.active + portfolio.shadow:
        strategy = by_name[slot.strategy_name]()
        runners.append((slot, strategy, strategy.initialize()))
    return portfolio, TickEngine(
        runners=runners, permanents=_permanent_runners(portfolio),
        execution=ExecutionService(), book=RestingBook())


class FakeFeed:
    """Serves a growing candle tape like Binance klines (newest `limit` bars)."""

    def __init__(self, tape):
        self.tape = list(tape)
        self.visible = len(self.tape)
        self.calls = []
        self.fail = False

    def fetch(self, limit):
        self.calls.append(limit)
        if self.fail:
            raise ConnectionError("boom")
        return tuple(self.tape[: self.visible][-limit:])


def _loop(engine, feed, view, seed, now_ms):
    return LiveLoop(engine=engine, view=view, fetch_closed=feed.fetch,
                    seed_window=seed, clock=lambda: now_ms / 1000,
                    sleep=lambda s: None)


def _view(portfolio, market):
    return InMemoryPortfolioView(
        portfolio=portfolio, mark_price=market[-1].mark_price, now=NOW,
        candles=tuple(market),
        source_status=[{"source_id": "binance_public", "status": "ok",
                        "note": "seed"}])


def _setup(extra=0):
    """Replay 200 bars, return everything needed to poll for `extra` new bars."""

    market = build_market()
    portfolio, engine = _engine()
    warm = market[:200]
    for i in range(1, len(warm) + 1):
        engine.process(warm[i - 1], warm[max(0, i - WINDOW):i])
    tape = list(market[:200 + extra])
    feed = FakeFeed(tape)
    view = _view(portfolio, warm)
    now_ms = tape[-1].close_time_ms
    return engine, feed, view, _loop(engine, feed, view,
                                     tuple(warm[-WINDOW:]), now_ms)


def test_new_closed_candle_runs_strategies_and_publishes():
    engine, feed, view, loop = _setup(extra=1)
    before = engine.last_processed_open_ms
    processed = loop.poll_once()
    assert processed == 1
    assert engine.last_processed_open_ms == before + FIVE_MIN_MS
    assert view.live_status["bars_this_poll"] == 1
    assert view.live_status["last_tick_ms"] == engine.last_processed_open_ms + FIVE_MIN_MS
    assert view.live_status["total_fills"] == engine.fills
    # Re-polling with no new bar is a no-op for trading state.
    assert loop.poll_once() == 0


def test_gap_of_missed_candles_is_replayed_in_order():
    engine, feed, view, loop = _setup(extra=12)
    before = engine.last_processed_open_ms
    processed = loop.poll_once()
    assert processed == 12
    assert engine.last_processed_open_ms == before + 12 * FIVE_MIN_MS
    # The fetch was sized to bridge the whole gap, not a blind 5.
    assert feed.calls[-1] >= 12


def test_fetch_failure_leaves_state_intact_and_flags_degraded():
    engine, feed, view, loop = _setup(extra=1)
    feed.fail = True
    before_fills = engine.fills
    before_wm = engine.last_processed_open_ms
    assert loop.poll_once() == 0
    assert engine.fills == before_fills
    assert engine.last_processed_open_ms == before_wm
    src = {s["source_id"]: s for s in view.source_status}
    assert src["binance_public"]["status"] == "degraded"
    # Recovery: next successful poll trades the missed bar and clears status.
    feed.fail = False
    assert loop.poll_once() == 1
    src = {s["source_id"]: s for s in view.source_status}
    assert src["binance_public"]["status"] == "ok"


def test_live_matches_uninterrupted_replay():
    """Parity: warm replay + live polling == one continuous replay."""

    market = build_market()
    # Continuous reference.
    _, ref = _engine()
    for i in range(1, len(market) + 1):
        ref.process(market[i - 1], market[max(0, i - WINDOW):i])
    # Warm to 200 then live-poll the remaining bars a few at a time.
    engine, feed, view, loop = _setup(extra=0)
    for upto in range(201, len(market) + 1, 7):
        feed.tape = list(market[:upto])
        feed.visible = upto
        loop.clock = lambda u=upto: market[u - 1].close_time_ms / 1000
        loop.poll_once()
    feed.tape = list(market)
    feed.visible = len(market)
    loop.clock = lambda: market[-1].close_time_ms / 1000
    loop.poll_once()
    assert engine.fills == ref.fills
    assert engine.trades == ref.trades
    assert {w: (x.quote_cash, x.base_qty) for w, x in engine.wallet_by_id.items()} \
        == {w: (x.quote_cash, x.base_qty) for w, x in ref.wallet_by_id.items()}


def test_hooks_run_and_never_break_trading():
    engine, feed, view, loop = _setup(extra=2)
    seen = []
    loop.candle_hooks = (lambda snap: seen.append(snap.open_time_ms),
                         lambda snap: 1 / 0)  # a failing hook is contained
    loop.poll_hooks = (lambda: seen.append("poll"), lambda: 1 / 0)
    assert loop.poll_once() == 2
    assert seen[0] == "poll" and len(seen) == 3
