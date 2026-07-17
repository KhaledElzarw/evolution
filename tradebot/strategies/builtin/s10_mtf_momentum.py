"""Strategy 10 — Multi-Timeframe Time-Series Momentum.

Return momentum aggregated across independent horizons, volatility-normalized
sizing: BUY when the weighted momentum score is positive across enough
horizons; exit when the composite turns nonpositive or on a volatility shock.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...domain.market import MarketSnapshot
from ...domain.strategies import IntentSpec, StrategyContext
from ..base import BuiltinStrategy
from ..indicators import closes, stddev


class MtfMomentum(BuiltinStrategy):
    name = "MtfMomentum"
    family = "ts_momentum"
    min_warmup = 100
    horizons = (10, 25, 50, 90)  # candle-count proxies for 15m/1h/4h/1d horizons
    weights = (Decimal("0.4"), Decimal("0.3"), Decimal("0.2"), Decimal("0.1"))
    min_agreeing = 3
    vol_period = 20
    vol_shock_mult = Decimal("3.0")
    max_fraction = Decimal("0.5")

    def _score(self, values: list[Decimal]) -> tuple[Decimal, int]:
        score = Decimal(0)
        agreeing = 0
        for horizon, weight in zip(self.horizons, self.weights):
            ret = (values[-1] - values[-horizon]) / values[-horizon]
            score += ret * weight
            if ret > 0:
                agreeing += 1
        return score, agreeing

    def signal(self, context: StrategyContext,
               candles: tuple[MarketSnapshot, ...],
               state: dict[str, Any], *, holding: bool) -> list[IntentSpec | None]:
        values = closes(candles)
        score, agreeing = self._score(values)
        sd = stddev(values, self.vol_period)
        if sd is None or values[-1] == 0:
            return []
        rel_vol = sd / values[-1]

        if not holding:
            if score > 0 and agreeing >= self.min_agreeing:
                # Size inversely to recent volatility within wallet limits.
                denominator = Decimal(1) + rel_vol * Decimal(100)
                fraction = min(self.max_fraction, self.max_fraction / denominator
                               + Decimal("0.1"))
                return [self.buy_intent(context, "mtf_momentum", fraction=fraction)]
            return []

        if score <= 0:
            return [self.sell_all_intent(context, "momentum_nonpositive")]
        # Volatility shock exit: recent vol N times its longer-run baseline.
        long_sd = stddev(values, self.vol_period * 4)
        if long_sd is not None and long_sd > 0 and sd > long_sd * self.vol_shock_mult:
            return [self.sell_all_intent(context, "vol_shock")]
        return []


def create_strategy() -> MtfMomentum:
    return MtfMomentum()
