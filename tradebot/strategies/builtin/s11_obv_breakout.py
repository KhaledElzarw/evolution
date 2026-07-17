"""Strategy 11 — OBV and Relative-Volume Breakout.

Volume-flow-confirmed breakout: BUY when price breaks local resistance and
both OBV slope and relative volume confirm accumulation; exit on OBV
divergence, loss of the breakout level, or trailing stop.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...domain.market import MarketSnapshot
from ...domain.strategies import IntentSpec, StrategyContext
from ..base import BuiltinStrategy
from ..indicators import atr, donchian_high, obv_series, relative_volume


class ObvBreakout(BuiltinStrategy):
    name = "ObvBreakout"
    family = "volume_flow_breakout"
    min_warmup = 50
    resistance_period = 25
    obv_slope_window = 5
    min_rel_volume = Decimal("1.5")
    atr_period = 14
    atr_trail_mult = Decimal("2.5")

    def signal(self, context: StrategyContext,
               candles: tuple[MarketSnapshot, ...],
               state: dict[str, Any], *, holding: bool) -> list[IntentSpec | None]:
        resistance = donchian_high(candles, self.resistance_period)
        rel_vol = relative_volume(candles, self.resistance_period)
        obv = obv_series(candles)
        vol = atr(candles, self.atr_period)
        if resistance is None or rel_vol is None or vol is None or len(obv) < self.obv_slope_window + 1:
            return []
        close = candles[-1].close
        obv_rising = obv[-1] > obv[-1 - self.obv_slope_window]

        if not holding:
            if close > resistance and obv_rising and rel_vol >= self.min_rel_volume:
                state["breakout_level"] = str(resistance)
                return [self.buy_intent(context, "obv_breakout",
                                        fraction=Decimal("0.30"))]
            return []

        # OBV divergence: price up vs 5 ago but OBV down (distribution).
        price_up = close > candles[-1 - self.obv_slope_window].close
        if price_up and not obv_rising:
            return [self.sell_all_intent(context, "obv_divergence")]
        level = state.get("breakout_level")
        if level is not None and close < Decimal(level):
            return [self.sell_all_intent(context, "lost_breakout_level")]
        entry = self.entry_price(state)
        if entry is not None and close < entry - vol * self.atr_trail_mult:
            return [self.sell_all_intent(context, "atr_trail_stop")]
        return []


def create_strategy() -> ObvBreakout:
    return ObvBreakout()
