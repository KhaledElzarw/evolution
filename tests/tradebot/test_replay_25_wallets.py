"""Deterministic 25-wallet shared-snapshot replay (Phase 6 acceptance gate).

All 25 wallets (12 active + 12 shadow + Dark Horse placeholder) receive the
identical immutable snapshot stream; strategies run in-process (builtin,
trusted); fills go through the deterministic ExecutionService; accounting
invariants hold throughout; and the whole run is bit-reproducible.
"""

import datetime as dt
import random
from decimal import Decimal

from tests.tradebot.strategy_helpers import candle
from tradebot.application.execution import ExecutionService, OrderIntent, OrderType
from tradebot.application.portfolio import seed_portfolio
from tradebot.domain.strategies import StrategyContext, WalletView
from tradebot.strategies.builtin import BUILTIN_STRATEGIES

NOW = dt.datetime(2026, 7, 17)
N_CANDLES = 300
WINDOW = 150  # trailing candles fed to strategies


def make_market(seed: int = 42):
    """Deterministic pseudo-random walk. Floats only build test data; all
    ledger math downstream is Decimal."""

    rng = random.Random(seed)
    px = 60_000.0
    out = []
    for i in range(N_CANDLES):
        px *= 1 + rng.uniform(-0.004, 0.0042)
        out.append(candle(i, f"{px:.2f}", hi_off=f"{rng.uniform(5, 60):.2f}",
                          lo_off=f"{rng.uniform(5, 60):.2f}",
                          vol=f"{rng.uniform(5, 30):.2f}"))
    return tuple(out)


def run_replay(seed: int = 42):
    market = make_market(seed)
    names = [cls().metadata().name for cls in BUILTIN_STRATEGIES]
    portfolio = seed_portfolio(names, now=NOW, id_factory=lambda h: f"w-{h}")
    strategy_by_name = {cls().metadata().name: cls for cls in BUILTIN_STRATEGIES}

    slots = portfolio.active + portfolio.shadow  # Dark Horse strategy is Phase 10
    runners = []
    for slot in slots:
        strategy = strategy_by_name[slot.strategy_name]()
        runners.append((slot, strategy, strategy.initialize()))

    execution = ExecutionService()
    fills = 0
    intent_seq = 0
    for tick in range(1, N_CANDLES + 1):
        snapshot = market[tick - 1]
        window = market[max(0, tick - WINDOW):tick]
        # Phase 1: collect all intents against the SAME snapshot.
        batch = []
        for idx, (slot, strategy, state) in enumerate(runners):
            w = slot.wallet
            ctx = StrategyContext(
                snapshot=snapshot,
                wallet=WalletView(w.quote_cash, w.base_qty, w.avg_cost),
                candles=window,
            )
            decision = strategy.on_market_snapshot(ctx, state)
            runners[idx] = (slot, strategy, decision.state)
            for spec in decision.intents:
                intent_seq += 1
                batch.append((w, OrderIntent(
                    intent_id=f"i{intent_seq}", wallet_id=w.wallet_id,
                    strategy_version_id=slot.strategy_version_id,
                    side=spec.side, order_type=OrderType(spec.order_type),
                    quantity=spec.quantity, limit_price=spec.limit_price,
                    reason_code=spec.reason_code,
                )))
        # Phase 2: execute everything against that same snapshot.
        for result in execution.process_tick(snapshot, batch):
            if result.accepted:
                fills += 1
    return portfolio, fills, market


def test_replay_produces_fills_and_preserves_invariants():
    portfolio, fills, market = run_replay()
    assert fills > 0, "expected at least some fills across 24 strategy wallets"
    mark = market[-1].mark_price
    for slot in portfolio.active + portfolio.shadow:
        w = slot.wallet
        assert w.quote_cash >= 0
        assert w.base_qty >= 0
        equity = w.equity(mark)
        assert equity > 0
    # Dark Horse untouched in this phase.
    assert portfolio.dark_horse.wallet.quote_cash == Decimal("10000.00")


def test_replay_is_bit_reproducible():
    p1, fills1, m1 = run_replay(seed=42)
    p2, fills2, m2 = run_replay(seed=42)
    assert fills1 == fills2
    state1 = [(s.wallet.quote_cash, s.wallet.base_qty, s.wallet.realized_pnl)
              for s in p1.active + p1.shadow]
    state2 = [(s.wallet.quote_cash, s.wallet.base_qty, s.wallet.realized_pnl)
              for s in p2.active + p2.shadow]
    assert state1 == state2


def test_active_and_shadow_ledgers_evolve_identically_for_same_strategy():
    """Active wallet K and shadow wallet K run the same strategy on the same
    snapshots from the same starting balance -> identical ledgers (fairness)."""

    portfolio, _, _ = run_replay()
    for a, s in zip(portfolio.active, portfolio.shadow):
        assert a.strategy_name == s.strategy_name
        assert a.wallet.quote_cash == s.wallet.quote_cash
        assert a.wallet.base_qty == s.wallet.base_qty
        assert a.wallet.realized_pnl == s.wallet.realized_pnl


def test_wallet_isolation_no_shared_state():
    portfolio, _, _ = run_replay()
    wallets = [s.wallet for s in portfolio.active + portfolio.shadow]
    assert len({id(w) for w in wallets}) == 24
    assert len({w.wallet_id for w in wallets}) == 24
    before = wallets[1].quote_cash
    wallets[0].quote_cash = Decimal("0.00")  # mutate one
    assert wallets[1].quote_cash == before  # others unaffected
