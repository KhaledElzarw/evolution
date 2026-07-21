"""Strategy 1 — Volatility-Adaptive Inventory Grid.

Range capture with a genuinely two-sided resting book: symmetric levels around a
closed-candle anchor, spacing widened by ATR. It keeps a resting BUY limit below
the anchor and, while holding, a resting SELL limit above it — both on the book
at once. Recenters after a configured displacement + minimum elapsed candles.
"""

from __future__ import annotations

from typing import Any

from ...domain.market import MarketSnapshot
from ...domain.strategies import IntentSpec, StrategyContext
from ..grid import GridStrategy


class VolAdaptiveGrid(GridStrategy):
    name = "VolAdaptiveGrid"

    def signal(self, context: StrategyContext,
               candles: tuple[MarketSnapshot, ...],
               state: dict[str, Any], *, holding: bool) -> list[IntentSpec | None]:
        # Delegates to the shared two-sided grid; kept as its own method so the
        # built-in owns a distinct signal() per the plugin contract.
        return self.grid_intents(context, candles, state)


def create_strategy() -> VolAdaptiveGrid:
    return VolAdaptiveGrid()
