"""TickEngine: the one-candle trading tick shared by replay and live loop.

The engine was extracted verbatim from the devserver replay loop; these tests
pin (a) replay parity — driving the engine candle-by-candle produces the exact
same portfolio as the historical inline loop (which build_view now delegates
to), and (b) idempotency — re-feeding a candle is a structural no-op, which is
what makes live-loop gap catch-up and restart replay safe.
"""

import datetime as dt
from decimal import Decimal

from tradebot.api.devserver import WINDOW, build_market, build_view
from tradebot.application.execution import ExecutionService
from tradebot.application.order_book import RestingBook
from tradebot.application.portfolio import seed_portfolio
from tradebot.application.tick_engine import TickEngine
from tradebot.strategies.builtin import BUILTIN_STRATEGIES

NOW = dt.datetime(2026, 7, 20, 12, 0, 0)


def _build_engine():
    names = [c().metadata().name for c in BUILTIN_STRATEGIES]
    portfolio = seed_portfolio(names, now=NOW - dt.timedelta(days=3),
                               id_factory=lambda h: f"w-{h}")
    by_name = {c().metadata().name: c for c in BUILTIN_STRATEGIES}
    runners = []
    for slot in portfolio.active + portfolio.shadow:
        strategy = by_name[slot.strategy_name]()
        runners.append((slot, strategy, strategy.initialize()))
    from tradebot.api.devserver import _permanent_runners
    engine = TickEngine(runners=runners, permanents=_permanent_runners(portfolio),
                        execution=ExecutionService(), book=RestingBook())
    return portfolio, engine


def _drive(engine, market):
    for tick in range(1, len(market) + 1):
        engine.process(market[tick - 1], market[max(0, tick - WINDOW):tick])


def test_engine_replay_matches_build_view_exactly():
    # Anchor exactly as build_view does, so trade timestamps line up too.
    now_ms = int(NOW.replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    market = build_market(end_ms=now_ms - (now_ms % 300_000))
    portfolio, engine = _build_engine()
    _drive(engine, market)

    view = build_view(NOW)  # synthetic mode drives the same engine internally
    mark = market[-1].mark_price
    assert portfolio.active_equity(mark) == view.portfolio.active_equity(mark)
    assert portfolio.shadow_equity(mark) == view.portfolio.shadow_equity(mark)
    assert (portfolio.dark_horse.wallet.base_qty
            == view.portfolio.dark_horse.wallet.base_qty)
    # Same trade history, wallet for wallet.
    assert engine.trades == view.trades_by_wallet


def test_reprocessing_a_candle_is_a_structural_noop():
    market = build_market()
    _, engine = _build_engine()
    _drive(engine, market[:100])
    fills_before = engine.fills
    trades_before = {k: list(v) for k, v in engine.trades.items()}
    equity_before = {wid: (w.quote_cash, w.base_qty)
                     for wid, w in engine.wallet_by_id.items()}

    # Re-feed the last candle (poll overlap) and an older one (restart replay).
    r1 = engine.process(market[99], market[:100])
    r2 = engine.process(market[50], market[:51])
    assert r1.skipped and r2.skipped
    assert engine.fills == fills_before
    assert engine.trades == trades_before
    assert {wid: (w.quote_cash, w.base_qty)
            for wid, w in engine.wallet_by_id.items()} == equity_before


def test_deterministic_bit_reproducibility():
    market = build_market()
    _, e1 = _build_engine()
    _, e2 = _build_engine()
    _drive(e1, market)
    _drive(e2, market)
    assert e1.trades == e2.trades
    assert e1.fills == e2.fills
    assert {w: (x.quote_cash, x.base_qty) for w, x in e1.wallet_by_id.items()} \
        == {w: (x.quote_cash, x.base_qty) for w, x in e2.wallet_by_id.items()}
