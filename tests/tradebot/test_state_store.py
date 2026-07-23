"""Snapshot persistence: engine state survives restarts bit-for-bit.

Key guarantee: restore + gap-replay produces the SAME portfolio as an
uninterrupted run — the restart is invisible to the accounting.
"""

import datetime as dt
import json

from tradebot.api.devserver import WINDOW, _permanent_runners, build_market
from tradebot.application.execution import ExecutionService
from tradebot.application.order_book import RestingBook
from tradebot.application.portfolio import seed_portfolio
from tradebot.application.tick_engine import TickEngine
from tradebot.infrastructure import state_store
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
    return TickEngine(runners=runners, permanents=_permanent_runners(portfolio),
                      execution=ExecutionService(), book=RestingBook())


def _drive(engine, market, start=1, end=None):
    end = len(market) if end is None else end
    for tick in range(start, end + 1):
        engine.process(market[tick - 1], market[max(0, tick - WINDOW):tick])


def _fingerprint(engine):
    return (engine.fills, engine.seq, engine.last_processed_open_ms,
            engine.trades,
            {w: (x.quote_cash, x.base_qty, x.avg_cost, x.realized_pnl,
                 x.total_fees) for w, x in engine.wallet_by_id.items()},
            engine.book.snapshot_open(),
            [(p.last_eval_ms, p.cadence_seconds) for p in engine.permanents])


def test_snapshot_state_is_json_serializable_and_round_trips(tmp_path):
    market = build_market()
    e1 = _engine()
    _drive(e1, market, end=250)
    path = tmp_path / "state.json"
    state_store.save(path, e1.snapshot_state())

    payload, reason = state_store.load(path)
    assert reason == "ok"
    e2 = _engine()
    e2.restore_state(payload["engine"])
    assert _fingerprint(e2) == _fingerprint(e1)


def test_restore_plus_gap_replay_equals_uninterrupted_run(tmp_path):
    market = build_market()
    ref = _engine()
    _drive(ref, market)  # uninterrupted reference

    e1 = _engine()
    _drive(e1, market, end=250)  # "crash" at candle 250
    path = tmp_path / "state.json"
    state_store.save(path, e1.snapshot_state())

    e2 = _engine()  # "restart"
    payload, _ = state_store.load(path)
    e2.restore_state(payload["engine"])
    _drive(e2, market, start=251)  # the live loop's gap catch-up

    assert _fingerprint(e2) == _fingerprint(ref)


def test_corrupt_wrong_version_and_stale_snapshots_are_rejected(tmp_path):
    path = tmp_path / "state.json"
    assert state_store.load(path) == (None, "missing")

    path.write_text("{not json", encoding="utf-8")
    payload, reason = state_store.load(path)
    assert payload is None and reason.startswith("corrupt")

    e = _engine()
    _drive(e, build_market(), end=50)
    state_store.save(path, e.snapshot_state())
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schema_version"] = 99
    path.write_text(json.dumps(raw), encoding="utf-8")
    payload, reason = state_store.load(path)
    assert payload is None and "version" in reason

    # A snapshot too far behind "now" cannot be gap-fetched -> rejected.
    state_store.save(path, e.snapshot_state())
    far_future = e.last_processed_open_ms + state_store.MAX_RESUME_GAP_MS + 1
    payload, reason = state_store.load(path, now_ms=far_future)
    assert payload is None and reason.startswith("too old")


def test_restore_rejects_a_different_portfolio_shape(tmp_path):
    e = _engine()
    _drive(e, build_market(), end=50)
    state = e.snapshot_state()
    state["wallets"] = {"w-someone-else": next(iter(state["wallets"].values()))}
    e2 = _engine()
    try:
        e2.restore_state(state)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
