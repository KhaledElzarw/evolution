"""Evolution server: live paper trading + dashboard/API.

Seeds a 26-wallet portfolio (12 active + 12 shadow + Dark Horse + Darkhorse -
Daily), replays the backfilled market through the real ExecutionService so the
wallets hold genuine balances, then serves the API + dashboard. The two
permanent wallets trade through the REAL five-domain committee
(`tradebot.application.dark_horse.synthesize`); technical and liquidity are
derived from the candle window, while macro/fundamental/onchain come from the
hourly awareness service (external stats + news + local-LLM brief) when fresh,
degrading honestly to labelled candle-drift placeholders.

Market data is either:

* ``--live``  — real BTCUSDT candles from Binance's public endpoint. **No API
  key**: public market data needs none, and requiring exchange credentials for a
  paper platform was audit finding A10. Real exchange filters (tick/lot/notional)
  are fetched too, and the in-progress candle is excluded. If live data cannot be
  obtained the server **fails loudly** rather than silently serving fake prices.
  After the startup replay a LiveLoop keeps trading every newly CLOSED candle
  (missed bars are gap-replayed through the same TickEngine), and wallet state
  is snapshotted to ``--state`` so restarts resume instead of resetting.
* default — a seeded synthetic walk, touching no network; static replay only.

Paper trading only — no real orders are ever placed. Binds loopback only.

Run:  python -m tradebot.api.devserver --port 5555 --live
"""

from __future__ import annotations

import argparse
import datetime as dt
import random
import threading
import time
from decimal import Decimal

from ..application.dark_horse import DEFAULT_CADENCE_SECONDS
from ..application.execution import ExecutionModel, ExecutionService
from ..application.portfolio import seed_portfolio
from ..application.order_book import RestingBook
from ..application.tick_engine import (
    FIVE_MIN_MS,
    PermanentRunner,
    TickEngine,
    apply_daily_params,
    committee_evidence,
    permanent_committee_intent,
)
from ..domain.dark_horse_daily import default_params
from ..domain.market import MarketSnapshot
from ..strategies.builtin import BUILTIN_STRATEGIES
from .app import create_app
from .security import ApiSettings
from .views import InMemoryPortfolioView

# Backwards-compatible aliases: these lived here before the tick engine was
# extracted to application/tick_engine.py; tests and older callers import them
# under the underscore names.
_committee_evidence = committee_evidence
_permanent_committee_intent = permanent_committee_intent
_PermanentRunner = PermanentRunner
_apply_daily_params = apply_daily_params

# nosec B105 - not a credential: a fixed, published, loopback-only dev token so
# the operator can exercise the guarded mutation routes. Real deployments read
# TRADEBOT_API_TOKEN from the environment, and a non-loopback bind refuses to
# start without a strong one (see api/security.py::validate_startup).
DEV_TOKEN = "dev-local-token-not-a-secret-0123456789"  # nosec B105
N_CANDLES = 400
WINDOW = 150


def _candle(i: int, close: float, hi: float, lo: float, vol: float,
            open_ms: int | None = None) -> MarketSnapshot:
    # ``open_ms`` anchors the candle in real time. When omitted it falls back to
    # the legacy epoch-relative spacing (i * 5m), which is fine for unit tests
    # that only care about candle ordering, not wall-clock timestamps.
    open_ms = i * FIVE_MIN_MS if open_ms is None else open_ms
    close_ms = open_ms + FIVE_MIN_MS
    c = Decimal(f"{close:.2f}")
    return MarketSnapshot(
        snapshot_id=f"dev-c{i}", source="synthetic-dev", symbol="BTCUSDT",
        interval="5m", open_time_ms=open_ms, close_time_ms=close_ms,
        is_closed=True, open=c, high=c + Decimal(f"{hi:.2f}"),
        low=c - Decimal(f"{lo:.2f}"), close=c, volume=Decimal(f"{vol:.2f}"),
        retrieved_at_ms=close_ms, source_time_ms=close_ms,
    )


def build_market(seed: int = 7, *,
                 end_ms: int | None = None) -> tuple[MarketSnapshot, ...]:
    """Deterministic synthetic BTCUSDT walk.

    ``end_ms`` is the close time of the LAST candle. Pass ``now`` (aligned to a
    5-minute boundary) so the synthetic history reads with real recent
    timestamps offline, matching how live mode looks. Omit it for the legacy
    epoch-relative timeline used by tests.
    """

    # nosec B311 - deterministic REPRODUCIBILITY is the point here; this seeds a
    # synthetic demo market, never a security or trading decision.
    rng = random.Random(seed)  # nosec B311
    if end_ms is None:
        start_open_ms = 0
    else:
        # end_ms is the last candle's CLOSE; walk back N candles to candle 0's open.
        start_open_ms = end_ms - N_CANDLES * FIVE_MIN_MS
    px = 60_000.0
    out = []
    for i in range(N_CANDLES):
        px *= 1 + rng.uniform(-0.004, 0.0043)
        out.append(_candle(i, px, rng.uniform(5, 60), rng.uniform(5, 60),
                           rng.uniform(5, 30),
                           open_ms=start_open_ms + i * FIVE_MIN_MS))
    return tuple(out)


def build_live_market(interval: str = "5m", limit: int = 1000):
    """Real BTCUSDT candles from Binance's public endpoint (no credentials).

    Returns (closed_snapshots, filters, source_note). Raises on failure so the
    caller can decide whether to fall back — we never silently pretend live data
    was obtained.
    """

    from ..infrastructure.market_data.binance_public import (
        closed_only,
        fetch_exchange_filters,
        fetch_klines,
    )

    filters = fetch_exchange_filters()
    snapshots = fetch_klines(interval=interval, limit=limit)
    closed = closed_only(snapshots)
    if not closed:
        raise MarketDataUnavailable("no closed candles returned")
    dropped = len(snapshots) - len(closed)
    note = (f"binance public {interval}, {len(closed)} closed candles "
            f"({dropped} in-progress excluded)")
    return closed, filters, note


class MarketDataUnavailable(RuntimeError):
    pass


def _strategy_description(strategy_obj) -> str:
    """A short human blurb from the strategy module's docstring."""

    import inspect

    module = inspect.getmodule(type(strategy_obj))
    doc = (module.__doc__ or "").strip() if module is not None else ""
    paras = [" ".join(p.split()) for p in doc.split("\n\n") if p.strip()]
    if not paras:
        return ""
    # Paragraph 0 is the "Strategy N — Title" line; paragraph 1 is the summary.
    return paras[1] if len(paras) > 1 else paras[0]


_PERMANENT_DESCRIPTIONS = {
    "dark-horse-v1": (
        "Permanent wallet trading the real five-domain committee (technical, "
        "liquidity, macro, fundamental, on-chain). It accumulates, reduces, or "
        "exits to cash on a 4-hour cadence and is never reset."),
    "dark-horse-daily-v1": (
        "Permanent wallet that re-tunes its committee parameters every 24 hours "
        "from every wallet's daily lessons, within engine guardrails. Like Dark "
        "Horse it is never reset — only its strategy version advances."),
}


def _permanent_runners(portfolio,
                       daily_params: dict | None = None) -> list[PermanentRunner]:
    """Dark Horse on its 4h cadence; Darkhorse - Daily on its tuned cadence.

    ``daily_params`` overrides Darkhorse - Daily's tunables (fractions, cadence,
    limit offsets); when omitted it uses ``default_params()``. The harness passes
    freshly-adapted params here as the simulated days advance (Part E).

    Dark Horse trades MARKET: a high-conviction 4h committee decision should act
    now, not sit on the book (resting limits are natural for the mean-reversion
    builtins and the adaptive daily wallet, not for a conviction accumulator).
    """

    runners = []
    if portfolio.dark_horse is not None:
        runners.append(_PermanentRunner(
            slot=portfolio.dark_horse,
            cadence_seconds=DEFAULT_CADENCE_SECONDS,
            accumulate_fraction=Decimal("0.25"),
            reduce_fraction=Decimal("0.50"),
        ))
    if portfolio.dark_horse_daily is not None:
        params = daily_params or default_params()
        runners.append(_PermanentRunner(
            slot=portfolio.dark_horse_daily,
            cadence_seconds=int(params["signal_cadence_hours"] * 3600),
            accumulate_fraction=params["accumulate_fraction"],
            reduce_fraction=params["reduce_fraction"],
            entry_limit_bps=params.get("entry_limit_bps", Decimal("0")),
            exit_limit_bps=params.get("exit_limit_bps", Decimal("0")),
        ))
    return runners


def build_view(now: dt.datetime, live: bool = False,
               interval: str = "5m",
               llm_adapt: bool = False,
               restore_state: dict | None = None) -> InMemoryPortfolioView:
    filters = None
    market_note = "synthetic seeded walk (no network)"
    market_status = "synthetic"

    if live:
        try:
            market, filters, market_note = build_live_market(interval=interval)
            market_status = "ok"
            print(f"[evolution] LIVE market data: {market_note}")
            print(f"[evolution] real exchange filters: tick={filters.tick_size} "
                  f"step={filters.step_size} minNotional={filters.min_notional}")
        except Exception as exc:
            # Be loud and honest rather than silently serving fake prices.
            print(f"[evolution] LIVE market data FAILED ({type(exc).__name__}: "
                  f"{exc}) -> refusing to fall back silently")
            raise
    else:
        # Anchor the synthetic history so its timestamps end at ``now`` (aligned
        # to a 5-minute boundary), so the order history reads with real recent
        # times offline instead of 1970.
        now_ms = int(now.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        end_ms = now_ms - (now_ms % FIVE_MIN_MS)
        market = build_market(end_ms=end_ms)

    names = [c().metadata().name for c in BUILTIN_STRATEGIES]
    # Assignments are backdated so the display-name day counter is non-zero.
    portfolio = seed_portfolio(names, now=now - dt.timedelta(days=3),
                               id_factory=lambda h: f"w-{h}")
    by_name = {c().metadata().name: c for c in BUILTIN_STRATEGIES}
    runners = []
    for slot in portfolio.active + portfolio.shadow:
        strategy = by_name[slot.strategy_name]()
        runners.append((slot, strategy, strategy.initialize()))
    permanents = _permanent_runners(portfolio)

    # Live mode uses the REAL exchange filters (Binance's actual LOT_SIZE step is
    # 0.00001, not the 1-satoshi default), so fills obey the true venue rules.
    execution = (ExecutionService(model=ExecutionModel(filters=filters))
                 if filters else ExecutionService())
    book = RestingBook()

    # Opt-in: the daily LLM re-tune loop. None => no adaptation (current
    # behaviour, and the graceful fallback when the local model is down).
    retuner = _build_retuner(portfolio) if llm_adapt else None

    engine = TickEngine(runners=runners, permanents=permanents,
                        execution=execution, book=book, retuner=retuner)
    n = len(market)
    restored = False
    if restore_state is not None:
        try:
            engine.restore_state(restore_state)
            restored = True
            print(f"[evolution] state RESTORED: {engine.fills} lifetime fills, "
                  f"watermark {engine.last_processed_open_ms} — the live loop "
                  f"will gap-replay up to now")
        except Exception as exc:
            print(f"[evolution] state restore REJECTED "
                  f"({type(exc).__name__}: {exc}) -> fresh backfill replay")
    if not restored:
        for tick in range(1, n + 1):
            engine.process(market[tick - 1], market[max(0, tick - WINDOW):tick])
    trades = engine.trades
    fills = engine.fills

    if retuner is not None and not restored:
        _print_adaptation_summary(retuner)
    open_orders = book.snapshot_open()
    mark = market[-1].mark_price
    resting = sum(len(v) for v in open_orders.values())
    print(f"[evolution] {'restored' if restored else f'replayed {n} candles'} "
          f"across "
          f"{len(portfolio.active) + len(portfolio.shadow) + len(permanents)} "
          f"wallets -> {fills} fills, {resting} orders still resting")
    print(f"[evolution] mark price {mark}  active equity "
          f"{portfolio.active_equity(mark)}  shadow {portfolio.shadow_equity(mark)}")
    for pr in permanents:
        w = pr.slot.wallet
        print(f"[evolution] {pr.slot.strategy_name}: equity {w.equity(mark)}  "
              f"btc {w.base_qty}  usdt {w.quote_cash}")

    llm_healthy, llm_model = probe_local_llm()

    descriptions = {c().metadata().strategy_version_id: _strategy_description(c())
                    for c in BUILTIN_STRATEGIES}
    descriptions.update(_PERMANENT_DESCRIPTIONS)

    view = InMemoryPortfolioView(
        portfolio=portfolio, mark_price=mark, now=now,
        candles=tuple(market),
        archived_lifetime_pnl=Decimal("0.00"),
        trades_by_wallet=trades,
        open_orders_by_wallet=open_orders,
        strategy_descriptions=descriptions,
        llm_healthy=llm_healthy,
        llm_model_id=llm_model,
        source_status=[
            {"source_id": "binance_public", "status": market_status,
             "note": market_note},
            {"source_id": "llama_cpp", "status": "ok" if llm_healthy else "degraded",
             "note": f"local model: {llm_model}"},
        ],
    )
    # Runtime handles for main(): the live loop keeps trading through the SAME
    # engine the replay just warmed up.
    view.runtime = {"engine": engine, "market": market, "interval": interval}
    return view


#: How many candles the view retains for charting. At least the ~1000-bar live
#: startup backfill, so appending new bars never shrinks the visible history.
CANDLE_RETENTION = 1000


def merge_closed_candles(existing: tuple[MarketSnapshot, ...],
                         closed: tuple[MarketSnapshot, ...],
                         retention: int = CANDLE_RETENTION,
                         ) -> tuple[MarketSnapshot, ...]:
    """Append candles newer than the last retained bar, capped to ``retention``.

    Pure so the refresher loop's only untested part is the fetch/sleep plumbing.
    """

    last_open = existing[-1].open_time_ms if existing else -1
    fresh = tuple(c for c in closed if c.open_time_ms > last_open)
    return (existing + fresh)[-retention:] if fresh else existing


def start_market_refresher(view: InMemoryPortfolioView, interval: str = "5m",
                           period_seconds: float = 15.0) -> threading.Thread:
    """Keep the mark price AND the candle window current from live Binance data.

    Without this the mark is frozen at whatever the startup backfill saw, so the
    dashboard would show live-sourced but stale prices. Re-marks every wallet's
    equity against the newest CLOSED candle (the in-progress bar is still never
    used for marking, matching the evaluation rules), and appends newly closed
    candles to ``view.candles`` so the chart keeps advancing instead of ending
    at the startup snapshot. Strategy decisions are still NOT re-run on new
    bars — this only keeps the read model fresh.

    Failures leave the last good mark in place and flag the source as degraded —
    we never invent a price.
    """

    from ..infrastructure.market_data.binance_public import closed_only, fetch_klines

    def _loop() -> None:
        while True:
            try:
                # limit=5 gives headroom to bridge a few missed periods (e.g. a
                # laptop sleep shorter than 5 bars); longer gaps simply resume
                # from the newest bars, leaving a visible gap in the chart.
                closed = closed_only(fetch_klines(interval=interval, limit=5))
                if closed:
                    newest = closed[-1]
                    view.mark_price = newest.close
                    view.now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
                    # Single tuple assignment: atomic under the GIL, so
                    # request threads never see a half-updated window.
                    view.candles = merge_closed_candles(view.candles, closed)
                    _set_source(view, "binance_public", "ok",
                                f"live {interval}; marked at closed candle "
                                f"{newest.close_time_ms}")
            except Exception as exc:  # keep serving the last good mark
                _set_source(view, "binance_public", "degraded",
                            f"refresh failed: {type(exc).__name__}")
            time.sleep(period_seconds)

    thread = threading.Thread(target=_loop, name="market-refresher", daemon=True)
    thread.start()
    return thread


def _set_source(view: InMemoryPortfolioView, source_id: str, status: str,
                note: str) -> None:
    for entry in view.source_status:
        if entry.get("source_id") == source_id:
            entry["status"] = status
            entry["note"] = note
            return


def _build_llm_client():
    """A LlamaCppClient over the allowlist-guarded httpx transport, or None.

    Shared by the health probe and the daily re-tune loop. Returns
    ``(client, model_id)`` on success or ``(None, "unavailable")`` when the model
    is unreachable — callers then degrade rather than pretend.
    """

    import httpx

    from ..infrastructure.data_broker.policy import PolicyViolation, validate_request
    from ..infrastructure.llm.llama_cpp_client import LlamaCppClient, LlmConfig

    class HttpxTransport:
        """Every URL is revalidated against the allowlist before it is sent."""

        def get(self, url: str) -> tuple[int, dict]:
            validate_request(url, "GET", resolver=lambda h: [h])
            r = httpx.get(url, timeout=5.0)
            return r.status_code, (r.json() if r.content else {})

        def post(self, url: str, payload: dict) -> tuple[int, dict]:
            validate_request(url, "POST", resolver=lambda h: [h])
            r = httpx.post(url, json=payload, timeout=60.0)
            return r.status_code, (r.json() if r.content else {})

    client = LlamaCppClient(HttpxTransport(), LlmConfig())
    try:
        if not client.health():
            return None, "unavailable"
        return client, client.discover_model()
    except (httpx.HTTPError, PolicyViolation, Exception):
        return None, "unavailable"


def probe_local_llm() -> tuple[bool, str]:
    """Live health + model discovery against the configured llama.cpp host."""

    client, model_id = _build_llm_client()
    if client is None:
        print("[evolution] local model unreachable -> degraded")
        return False, "unavailable"
    print(f"[evolution] local model OK: served id '{model_id}'")
    return True, model_id


def _build_retuner(portfolio):
    """Assemble the daily LLM re-tuner, or None if the model is unavailable."""

    from .harness_adaptation import DailyReTuner, LlmAnalyst, LlmProposer

    if portfolio.dark_horse_daily is None:
        return None
    client, model_id = _build_llm_client()
    if client is None:
        print("[evolution] --llm-adapt requested but local model is down "
              "-> daily wallet runs on default params (no adaptation)")
        return None
    print(f"[evolution] --llm-adapt ON: Darkhorse - Daily re-tunes each "
          f"simulated day via '{model_id}'")
    slot = portfolio.dark_horse_daily
    return DailyReTuner(
        daily_wallet_id=slot.wallet.wallet_id,
        daily_version_id=slot.strategy_version_id,
        params=default_params(),
        analyst=LlmAnalyst(client),
        proposer=LlmProposer(client),
    )


def _build_awareness():
    """Assemble the hourly awareness service over the guarded broker + LLM.

    Always returns a service: with the local model down every refresh records
    an honest degraded status and the committee keeps its labelled candle-drift
    placeholders — trading never depends on awareness being up.
    """

    import httpx

    from ..application.awareness import AwarenessService
    from ..infrastructure.awareness.brief import synthesize_brief
    from ..infrastructure.awareness.sources import gather_inputs
    from ..infrastructure.data_broker.client import DataBroker, RawResponse

    class _HttpxBrokerTransport:
        def request(self, method: str, url: str, headers: dict) -> RawResponse:
            r = httpx.request(method, url, headers=headers, timeout=15.0,
                              follow_redirects=False)
            return RawResponse(
                status=r.status_code,
                headers={k.title(): v for k, v in r.headers.items()},
                body=r.content,
                location=r.headers.get("location"),
            )

    broker = DataBroker(transport=_HttpxBrokerTransport())

    def _synthesize(inputs):
        client, _model = _build_llm_client()
        if client is None:
            class _Down:
                status = "llm_unreachable"
                model_id = "unavailable"
            return None, _Down()
        return synthesize_brief(client, inputs)

    return AwarenessService(gather=lambda: gather_inputs(broker),
                            synthesize=_synthesize)


def _print_adaptation_summary(retuner) -> None:
    changed = [a for a in retuner.history if not a.degraded and a.changed]
    print(f"[evolution] daily adaptation: {len(retuner.history)} cycles, "
          f"{len(changed)} changed the strategy")
    for a in changed:
        moves = ", ".join(f"{adj.parameter} {adj.previous_value}->{adj.new_value}"
                          for adj in a.adjustments)
        print(f"[evolution]   {a.date}: {moves}")
    daily = retuner.params
    print(f"[evolution] final daily limits: entry_bps={daily['entry_limit_bps']} "
          f"exit_bps={daily['exit_limit_bps']}")


def _port_in_use(host: str, port: int) -> bool:
    """True if something is already listening on host:port.

    Guards against the confusing failure mode where a stale devserver keeps
    serving OLD in-memory code on the port while a fresh start silently loses
    the bind race — the exact trap behind the earlier 'Dark Horse frozen' report.
    """

    import socket

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False  # unresolvable host: let uvicorn surface the real error
    for family, socktype, proto, _canon, sockaddr in infos:
        with socket.socket(family, socktype, proto) as probe:
            try:
                probe.bind(sockaddr)
            except OSError:
                return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evolution-devserver")
    parser.add_argument("--port", type=int, default=5555)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--live", action="store_true",
                        help="fetch real BTCUSDT candles from Binance public "
                             "(no credentials required)")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--llm-adapt", action="store_true",
                        help="re-tune Darkhorse - Daily's parameters (including "
                             "resting-limit offsets) each simulated day via the "
                             "local model's daily lessons; degrades to no-op if "
                             "the model is down. Off by default (deterministic).")
    parser.add_argument("--state", default="evolution_state.json",
                        help="live-mode snapshot file: wallet state survives "
                             "restarts (missed candles are gap-replayed). "
                             "Pass an empty string to disable persistence.")
    args = parser.parse_args(argv)

    # Fail fast (before the expensive market replay) if the port is taken, so a
    # stale process can't keep serving old code under a "started fine" illusion.
    if _port_in_use(args.host, args.port):
        print(f"[evolution] ERROR: {args.host}:{args.port} is already in use.")
        print("[evolution] A stale devserver may still be serving OLD code there.")
        print("[evolution] Stop that process (or pass a different --port), then retry.")
        return 1

    import uvicorn

    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)

    # Live mode: try to resume from the last snapshot (wrong-version/corrupt/
    # too-old snapshots are rejected loudly and a fresh replay runs instead).
    restore_payload = None
    state_path = args.state if args.live else ""
    if state_path:
        from ..infrastructure import state_store

        now_ms = int(now.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        payload, reason = state_store.load(state_path, now_ms=now_ms)
        if payload is not None:
            restore_payload = payload["engine"]
            print(f"[evolution] snapshot found ({state_path}, saved "
                  f"{payload['saved_at_utc']}) -> resuming")
        elif reason != "missing":
            print(f"[evolution] snapshot unusable ({reason}) -> fresh replay")

    view = build_view(now, live=args.live, interval=args.interval,
                      llm_adapt=args.llm_adapt, restore_state=restore_payload)
    loop = None
    if args.live:
        from ..application.live_loop import LiveLoop
        from ..infrastructure.market_data.binance_public import (
            closed_only,
            fetch_klines,
        )

        market = view.runtime["market"]
        engine = view.runtime["engine"]

        # Hourly market awareness: real macro/fundamental/onchain evidence for
        # the permanent-wallet committee, refreshed inside the poll loop.
        awareness = _build_awareness()
        engine.evidence_fn = awareness.evidence_fn()

        def _awareness_hook() -> None:
            if awareness.refresh():  # hourly-gated internally
                view.awareness = awareness.status_block()

        candle_hooks: tuple = ()
        if state_path:
            from ..infrastructure import state_store

            def _save_snapshot(_snapshot=None) -> None:
                state_store.save(state_path, engine.snapshot_state())

            candle_hooks = (_save_snapshot,)

        loop = LiveLoop(
            engine=engine, view=view,
            fetch_closed=lambda limit: closed_only(
                fetch_klines(interval=args.interval, limit=limit)),
            seed_window=tuple(market[-WINDOW:]),
            merge_candles=merge_closed_candles,
            poll_hooks=(_awareness_hook,),
            candle_hooks=candle_hooks,
        )
        loop.start()
        print(f"[evolution] LIVE TRADING loop running: strategies re-evaluate "
              f"every closed {args.interval} candle (poll 15s)")
        print("[evolution] hourly awareness: coingecko + mempool.space + news "
              "RSS -> local model brief -> committee evidence")

    settings = ApiSettings(host=args.host, port=args.port, auth_token=DEV_TOKEN)
    app = create_app(view, settings)

    print(f"[evolution] dashboard  http://{args.host}:{args.port}/")
    print(f"[evolution] api        http://{args.host}:{args.port}/api/v2/portfolio/summary")
    print(f"[evolution] mutation token: {DEV_TOKEN}")
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        if loop is not None:
            loop.stop()
            if state_path:
                from ..infrastructure import state_store

                state_store.save(state_path,
                                 view.runtime["engine"].snapshot_state())
                print(f"[evolution] final state snapshot -> {state_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    raise SystemExit(main())
