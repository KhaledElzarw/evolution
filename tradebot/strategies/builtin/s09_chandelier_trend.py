"""Strategy 9 — Chandelier Trend Follower.

Long-horizon trailing trend following: BUY on a new-high breakout with trend
confirmation; the position rides a chandelier stop (highest high since entry
minus an ATR multiple) until stopped or the trend filter reverses.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...domain.market import MarketSnapshot
from ...domain.strategies import IntentSpec, StrategyContext
from ..base import BuiltinStrategy
from ..indicators import atr, closes, donchian_high, ema


class ChandelierTrend(BuiltinStrategy):
    name = "ChandelierTrend"
    family = "trailing_trend"
    min_warmup = 60
    breakout_period = 40
    atr_period = 22
    atr_mult = Decimal("3.0")
    trend_ema = 50

    def initialize(self) -> dict[str, Any]:
        state = super().initialize()
        state["highest_since_entry"] = None
        return state

    def signal(self, context: StrategyContext,
               candles: tuple[MarketSnapshot, ...],
               state: dict[str, Any], *, holding: bool) -> list[IntentSpec | None]:
        values = closes(candles)
        vol = atr(candles, self.atr_period)
        trend = ema(values, self.trend_ema)
        upper = donchian_high(candles, self.breakout_period)
        if vol is None or trend is None or upper is None:
            return []
        close = candles[-1].close

        if not holding:
            if close > upper and close > trend:
                state["highest_since_entry"] = str(candles[-1].high)
                return [self.buy_intent(context, "chandelier_entry",
                                        fraction=Decimal("0.40"))]
            return []

        highest = Decimal(state.get("highest_since_entry") or str(candles[-1].high))
        highest = max(highest, candles[-1].high)
        state["highest_since_entry"] = str(highest)
        stop = highest - vol * self.atr_mult
        if close < stop:
            state["highest_since_entry"] = None
            return [self.sell_all_intent(context, "chandelier_stop")]
        if close < trend:
            state["highest_since_entry"] = None
            return [self.sell_all_intent(context, "trend_filter_reversal")]
        return []


def create_strategy() -> ChandelierTrend:
    return ChandelierTrend()
