"""Contract, determinism, warmup, and distinctness tests for all 12 built-ins."""

from decimal import Decimal

import pytest

from tests.tradebot.strategy_helpers import context_for, run_ticks, series
from tradebot.domain.ledger import Side
from tradebot.domain.strategies import StrategyDecision
from tradebot.strategies.builtin import BUILTIN_STRATEGIES

FLAT = series([Decimal("60000") + Decimal(5 * ((-1) ** i)) for i in range(120)])


def test_exactly_twelve_builtin_strategies():
    assert len(BUILTIN_STRATEGIES) == 12


def test_strategies_are_materially_distinct():
    families = {cls().metadata().family for cls in BUILTIN_STRATEGIES}
    names = {cls().metadata().name for cls in BUILTIN_STRATEGIES}
    signals = {cls.signal.__qualname__.split(".")[0] for cls in BUILTIN_STRATEGIES}
    assert len(families) == 12  # distinct conceptual families
    assert len(names) == 12
    assert len(signals) == 12  # each implements its own signal(), not a preset


@pytest.mark.parametrize("cls", BUILTIN_STRATEGIES, ids=lambda c: c.__name__)
def test_contract_shape(cls):
    strategy = cls()
    meta = strategy.metadata()
    assert meta.supported_symbol == "BTCUSDT"
    assert meta.origin == "builtin"
    state = strategy.initialize()
    assert isinstance(state, dict)
    decision = strategy.on_market_snapshot(context_for(FLAT), state)
    assert isinstance(decision, StrategyDecision)
    for intent in decision.intents:
        assert intent.side in (Side.BUY, Side.SELL)
        assert intent.quantity > 0


@pytest.mark.parametrize("cls", BUILTIN_STRATEGIES, ids=lambda c: c.__name__)
def test_insufficient_warmup_produces_no_intents(cls):
    strategy = cls()
    short = FLAT[: strategy.min_warmup - 1]
    decision = strategy.on_market_snapshot(context_for(short), strategy.initialize())
    assert decision.intents == ()


@pytest.mark.parametrize("cls", BUILTIN_STRATEGIES, ids=lambda c: c.__name__)
def test_unclosed_candle_produces_no_intents(cls):
    strategy = cls()
    candles = list(FLAT)
    last = candles[-1]
    open_candle = type(last)(
        snapshot_id=last.snapshot_id, source=last.source, symbol=last.symbol,
        interval=last.interval, open_time_ms=last.open_time_ms,
        close_time_ms=last.close_time_ms, is_closed=False, open=last.open,
        high=last.high, low=last.low, close=last.close, volume=last.volume,
        retrieved_at_ms=last.retrieved_at_ms, source_time_ms=last.source_time_ms,
    )
    ctx = context_for(tuple(candles[:-1]) + (open_candle,))
    ctx = type(ctx)(snapshot=open_candle, wallet=ctx.wallet, candles=ctx.candles)
    decision = strategy.on_market_snapshot(ctx, strategy.initialize())
    assert decision.intents == ()


@pytest.mark.parametrize("cls", BUILTIN_STRATEGIES, ids=lambda c: c.__name__)
def test_deterministic_replay(cls):
    """Same candle stream twice -> identical decisions (bit-reproducible)."""

    d1, s1 = run_ticks(cls(), FLAT)
    d2, s2 = run_ticks(cls(), FLAT)
    assert s1 == s2
    assert [
        [(i.side, i.quantity, i.reason_code) for i in d.intents] for d in d1
    ] == [
        [(i.side, i.quantity, i.reason_code) for i in d.intents] for d in d2
    ]


@pytest.mark.parametrize("cls", BUILTIN_STRATEGIES, ids=lambda c: c.__name__)
def test_no_lookahead(cls):
    """Decisions at tick N are identical whether or not future candles exist."""

    n = 80
    d_full, _ = run_ticks(cls(), FLAT[:n])
    d_prefix, _ = run_ticks(cls(), FLAT[:n // 2])
    for a, b in zip(d_prefix, d_full[: n // 2]):
        assert [(i.side, i.quantity) for i in a.intents] == [
            (i.side, i.quantity) for i in b.intents
        ]
