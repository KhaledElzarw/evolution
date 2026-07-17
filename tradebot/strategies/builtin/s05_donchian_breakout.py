"""Strategy 5 — Donchian Breakout With Volume Confirmation.

Price-channel breakout: BUY on a confirmed upper-channel breakout with volume
confirmation; exit on trailing lower band or failed breakout. Duplicate entry
on the same breakout candle is prevented via the entry-cooldown mechanism.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...domain.market import MarketSnapshot
from ...domain.strategies import IntentSpec, StrategyContext
from ..base import BuiltinStrategy
from ..indicators import donchian_high, donchian_low, relative_volume


class DonchianBreakout(BuiltinStrategy):
    name = "DonchianBreakout"
    family = "channel_breakout"
    min_warmup = 40
    entry_period = 20
    exit_period = 10
    min_rel_volume = Decimal("1.3")
    cooldown_candles = 5
    failed_breakout_candles = 5

    def signal(self, context: StrategyContext,
               candles: tuple[MarketSnapshot, ...],
               state: dict[str, Any], *, holding: bool) -> list[IntentSpec | None]:
        upper = donchian_high(candles, self.entry_period)
        lower = donchian_low(candles, self.exit_period)
        rel_vol = relative_volume(candles, self.entry_period)
        if upper is None or lower is None or rel_vol is None:
            return []
        close = candles[-1].close

        if not holding:
            if close > upper and rel_vol >= self.min_rel_volume:
                state["breakout_level"] = str(upper)
                return [self.buy_intent(context, "donchian_breakout",
                                        fraction=Decimal("0.35"))]
            return []

        # Trailing exit: close below the lower exit channel.
        if close < lower:
            return [self.sell_all_intent(context, "donchian_trail_exit")]
        # Failed breakout: price back under the breakout level shortly after.
        level = state.get("breakout_level")
        if (level is not None and close < Decimal(level)
                and state.get("candles_held", 0) <= self.failed_breakout_candles):
            return [self.sell_all_intent(context, "failed_breakout")]
        return []


def create_strategy() -> DonchianBreakout:
    return DonchianBreakout()
