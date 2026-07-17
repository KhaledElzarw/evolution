"""Deterministic synthetic candle builders for strategy tests."""

from __future__ import annotations

from decimal import Decimal

from tradebot.domain.ledger import Wallet
from tradebot.domain.market import MarketSnapshot
from tradebot.domain.money import quote
from tradebot.domain.strategies import StrategyContext, WalletView


def candle(i: int, close: str | Decimal, *, hi_off="10", lo_off="10",
           vol="10", open_=None) -> MarketSnapshot:
    c = Decimal(str(close))
    o = Decimal(str(open_)) if open_ is not None else c
    return MarketSnapshot(
        snapshot_id=f"c{i}", source="test", symbol="BTCUSDT", interval="5m",
        open_time_ms=i * 300_000, close_time_ms=(i + 1) * 300_000, is_closed=True,
        open=o, high=max(o, c) + Decimal(str(hi_off)),
        low=min(o, c) - Decimal(str(lo_off)), close=c, volume=Decimal(str(vol)),
        retrieved_at_ms=(i + 1) * 300_000, source_time_ms=(i + 1) * 300_000,
    )


def series(closes: list, **kw) -> tuple[MarketSnapshot, ...]:
    return tuple(candle(i, c, **kw) for i, c in enumerate(closes))


def context_for(candles: tuple[MarketSnapshot, ...],
                wallet: Wallet | None = None) -> StrategyContext:
    w = wallet or Wallet("w-test")
    return StrategyContext(
        snapshot=candles[-1],
        wallet=WalletView(quote_cash=w.quote_cash, base_qty=w.base_qty,
                          avg_cost=w.avg_cost),
        candles=candles,
    )


def holding_context(candles: tuple[MarketSnapshot, ...],
                    base_qty: str = "0.1") -> StrategyContext:
    return StrategyContext(
        snapshot=candles[-1],
        wallet=WalletView(quote_cash=quote("4000"), base_qty=Decimal(base_qty),
                          avg_cost=quote("60000")),
        candles=candles,
    )


def run_ticks(strategy, candle_list: tuple[MarketSnapshot, ...],
              wallet: Wallet | None = None):
    """Feed candles one at a time (no lookahead possible) and collect decisions."""

    state = strategy.initialize()
    decisions = []
    for i in range(1, len(candle_list) + 1):
        ctx = context_for(candle_list[:i], wallet)
        decision = strategy.on_market_snapshot(ctx, state)
        state = decision.state
        decisions.append(decision)
    return decisions, state
