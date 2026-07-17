"""Strategy 12 — Deterministic Regime-Switching Ensemble.

Classifies the market into TREND / RANGE / AMBIGUOUS regimes using the
Kaufman efficiency ratio and volatility, then delegates to an independently
implemented bounded subpolicy (trend-continuation vs mean-reversion), holding
cash in ambiguous regimes. Every intent's reason code records the subpolicy.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...domain.market import MarketSnapshot
from ...domain.strategies import IntentSpec, StrategyContext
from ..base import BuiltinStrategy
from ..indicators import closes, efficiency_ratio, ema, sma, zscore


class RegimeEnsemble(BuiltinStrategy):
    name = "RegimeEnsemble"
    family = "regime_ensemble"
    min_warmup = 60
    er_period = 20
    trend_threshold = Decimal("0.45")
    range_threshold = Decimal("0.25")
    z_period = 20
    z_entry = Decimal("-1.5")
    trend_ema = 30
    time_stop = 40

    def _regime(self, values: list[Decimal]) -> str:
        er = efficiency_ratio(values, self.er_period)
        if er is None:
            return "ambiguous"
        if er >= self.trend_threshold:
            return "trend"
        if er <= self.range_threshold:
            return "range"
        return "ambiguous"

    # -- independent subpolicies ---------------------------------------------

    def _trend_subpolicy(self, context: StrategyContext, values: list[Decimal],
                         state: dict[str, Any], holding: bool) -> list[IntentSpec | None]:
        trend = ema(values, self.trend_ema)
        if trend is None:
            return []
        close = values[-1]
        if not holding and close > trend and values[-1] > values[-2] > values[-3]:
            return [self.buy_intent(context, "regime:trend_continuation",
                                    fraction=Decimal("0.30"))]
        if holding and close < trend:
            return [self.sell_all_intent(context, "regime:trend_exit")]
        return []

    def _range_subpolicy(self, context: StrategyContext, values: list[Decimal],
                         state: dict[str, Any], holding: bool) -> list[IntentSpec | None]:
        z = zscore(values, self.z_period)
        mean = sma(values, self.z_period)
        if z is None or mean is None:
            return []
        if not holding and z <= self.z_entry:
            return [self.buy_intent(context, "regime:range_reversion",
                                    fraction=Decimal("0.20"))]
        if holding and values[-1] >= mean:
            return [self.sell_all_intent(context, "regime:range_exit")]
        return []

    def signal(self, context: StrategyContext,
               candles: tuple[MarketSnapshot, ...],
               state: dict[str, Any], *, holding: bool) -> list[IntentSpec | None]:
        values = closes(candles)
        regime = self._regime(values)
        state["regime"] = regime

        if regime == "trend":
            return self._trend_subpolicy(context, values, state, holding)
        if regime == "range":
            return self._range_subpolicy(context, values, state, holding)
        # Ambiguous/transition regime: hold cash; exit stale positions on time.
        if holding and state.get("candles_held", 0) >= self.time_stop:
            return [self.sell_all_intent(context, "regime:ambiguous_time_exit")]
        return []


def create_strategy() -> RegimeEnsemble:
    return RegimeEnsemble()
