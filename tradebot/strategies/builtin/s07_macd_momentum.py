"""Strategy 7 — MACD Histogram Momentum Acceleration.

Momentum transition: BUY when the histogram crosses positive or shows renewed
acceleration in a bullish context; exit on histogram deceleration streak,
negative cross, or maximum holding period.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...domain.market import MarketSnapshot
from ...domain.strategies import IntentSpec, StrategyContext
from ..base import BuiltinStrategy
from ..indicators import closes, macd_histogram


class MacdMomentum(BuiltinStrategy):
    name = "MacdMomentum"
    family = "macd_momentum"
    min_warmup = 60
    decel_streak_exit = 3
    max_hold = 60

    def signal(self, context: StrategyContext,
               candles: tuple[MarketSnapshot, ...],
               state: dict[str, Any], *, holding: bool) -> list[IntentSpec | None]:
        hist = macd_histogram(closes(candles))
        if len(hist) < 4:
            return []
        h0, h1, h2, h3 = hist[-4], hist[-3], hist[-2], hist[-1]

        if not holding:
            crossed_positive = h2 <= 0 < h3
            accelerating = h3 > 0 and h3 > h2 > h1  # renewed acceleration
            if crossed_positive or accelerating:
                return [self.buy_intent(context, "macd_momentum",
                                        fraction=Decimal("0.30"))]
            return []

        if h3 < 0:
            return [self.sell_all_intent(context, "macd_negative_cross")]
        if h3 < h2 < h1 < h0:
            # Deceleration for `decel_streak_exit` consecutive candles.
            return [self.sell_all_intent(context, "macd_deceleration")]
        if state.get("candles_held", 0) >= self.max_hold:
            return [self.sell_all_intent(context, "max_hold")]
        return []


def create_strategy() -> MacdMomentum:
    return MacdMomentum()
