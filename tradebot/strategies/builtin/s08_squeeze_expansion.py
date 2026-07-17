"""Strategy 8 — Bollinger–Keltner Squeeze Expansion.

Volatility compression → expansion: detect a completed squeeze (Bollinger
bands inside Keltner channel), BUY only on confirmed bullish expansion with
volume and momentum alignment; exit on return inside the channel or reversal.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ...domain.market import MarketSnapshot
from ...domain.strategies import IntentSpec, StrategyContext
from ..base import BuiltinStrategy
from ..indicators import atr, closes, relative_volume, sma, stddev


class SqueezeExpansion(BuiltinStrategy):
    name = "SqueezeExpansion"
    family = "volatility_squeeze"
    min_warmup = 50
    period = 20
    bb_mult = Decimal("2.0")
    kc_mult = Decimal("1.5")
    min_rel_volume = Decimal("1.2")
    min_squeeze_candles = 5

    def initialize(self) -> dict[str, Any]:
        state = super().initialize()
        state["squeeze_run"] = 0
        return state

    def _bands(self, candles: tuple[MarketSnapshot, ...]):
        values = closes(candles)
        mid = sma(values, self.period)
        sd = stddev(values, self.period)
        rng = atr(candles, self.period)
        if mid is None or sd is None or rng is None:
            return None
        bb_up, bb_dn = mid + sd * self.bb_mult, mid - sd * self.bb_mult
        kc_up, kc_dn = mid + rng * self.kc_mult, mid - rng * self.kc_mult
        in_squeeze = bb_up < kc_up and bb_dn > kc_dn
        return mid, bb_up, bb_dn, kc_up, in_squeeze

    def signal(self, context: StrategyContext,
               candles: tuple[MarketSnapshot, ...],
               state: dict[str, Any], *, holding: bool) -> list[IntentSpec | None]:
        bands = self._bands(candles)
        if bands is None:
            return []
        mid, bb_up, _bb_dn, _kc_up, in_squeeze = bands
        close = candles[-1].close
        rel_vol = relative_volume(candles, self.period)

        if in_squeeze:
            state["squeeze_run"] = state.get("squeeze_run", 0) + 1
        was_squeezed = state.get("squeeze_run", 0) >= self.min_squeeze_candles
        if not in_squeeze and not holding and was_squeezed:
            # Completed squeeze; look for bullish expansion confirmation.
            momentum_up = close > candles[-2].close > candles[-3].close
            if (close > bb_up and momentum_up
                    and rel_vol is not None and rel_vol >= self.min_rel_volume):
                state["squeeze_run"] = 0
                return [self.buy_intent(context, "squeeze_expansion",
                                        fraction=Decimal("0.35"))]
        if not in_squeeze and not was_squeezed:
            state["squeeze_run"] = 0

        if holding:
            if close < mid:
                return [self.sell_all_intent(context, "back_inside_channel")]
            if close < candles[-2].close < candles[-3].close:
                return [self.sell_all_intent(context, "momentum_reversal")]
        return []


def create_strategy() -> SqueezeExpansion:
    return SqueezeExpansion()
