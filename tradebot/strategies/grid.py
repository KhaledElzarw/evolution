"""Shared base for two-sided resting grids.

A grid strategy is defined by RESTING orders on both sides of an anchor and
letting price come to them: it keeps a buy limit BELOW the anchor (accumulate on
a dip) and, while holding, a sell limit ABOVE it (take profit on a bounce). Both
sit on the book simultaneously, so a grid always shows a genuinely two-sided set
of open orders.

The mechanics live here in :meth:`GridStrategy.grid_intents` so any current or
future grid inherits the two-sided behaviour for free — a concrete grid only
provides a thin ``signal`` that delegates here (each built-in must still own its
own ``signal`` per the plugin contract) and tunes the anchor/spacing knobs.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..domain.ledger import Side
from ..domain.market import MarketSnapshot
from ..domain.money import base as base_qty
from ..domain.money import quote
from ..domain.strategies import IntentSpec, StrategyContext
from .base import BuiltinStrategy
from .indicators import atr


class GridStrategy(BuiltinStrategy):
    """Two-sided resting grid around an ATR-spaced anchor."""

    family = "inventory_grid"
    min_warmup = 20
    atr_period = 14
    spacing_atr_mult = Decimal("1.0")
    recenter_displacement = Decimal("0.03")  # re-anchor past this drift
    recenter_min_candles = 20
    max_inventory_fraction = Decimal("0.75")  # stop bidding past this equity share
    grid_fraction = Decimal("0.15")           # cash fraction per resting bid
    min_edge = Decimal("0.004")               # fee-aware floor for the ask
    regrid_min_candles = 4                     # re-post the ladder at most this often

    def initialize(self) -> dict[str, Any]:
        state = super().initialize()
        state.update({"anchor": None, "anchor_index": -10_000,
                      "last_grid_index": -10_000})
        return state

    def grid_intents(self, context: StrategyContext,
                     candles: tuple[MarketSnapshot, ...],
                     state: dict[str, Any]) -> list[IntentSpec]:
        """Post a resting bid below and (while holding) a resting ask above.

        Mutates ``state`` (anchor + throttle bookkeeping) in place, mirroring how
        the base template hands the strategy a fresh state dict to update.
        """

        vol = atr(candles, self.atr_period)
        if vol is None or vol == 0:
            return []
        index = self.bar_ordinal(context.snapshot, candles)
        close = candles[-1].close

        anchor = Decimal(state["anchor"]) if state.get("anchor") else None
        # (Re)center on a large displacement; never trade on the recenter candle.
        if anchor is None or (
            abs(close - anchor) / anchor > self.recenter_displacement
            and index - state.get("anchor_index", -10_000) >= self.recenter_min_candles
        ):
            state["anchor"] = str(close)
            state["anchor_index"] = index
            return []

        # Throttle so the ladder does not churn on every candle.
        if index - state.get("last_grid_index", -10_000) < self.regrid_min_candles:
            return []
        state["last_grid_index"] = index

        spacing = vol * self.spacing_atr_mult
        px = context.snapshot.mark_price
        equity = context.wallet.quote_cash + context.wallet.base_qty * px
        inventory_ratio = (
            (context.wallet.base_qty * px) / equity if equity > 0 else Decimal(1)
        )
        intents: list[IntentSpec] = []

        # Resting BID below the anchor — accumulate on a dip.
        bid = anchor - spacing
        if bid > 0 and inventory_ratio < self.max_inventory_fraction:
            budget = quote(context.wallet.quote_cash * self.grid_fraction)
            if budget >= Decimal("10"):
                qty = base_qty(budget / bid)
                if qty > 0:
                    intents.append(IntentSpec(
                        side=Side.BUY, order_type="LIMIT", quantity=qty,
                        limit_price=quote(bid), reason_code="grid_buy"))

        # Resting ASK above the anchor — take profit on a bounce, fee-aware so it
        # never sits below the position's break-even-plus-edge price.
        if context.wallet.base_qty > 0:
            ask = anchor + spacing
            if context.wallet.avg_cost > 0:
                ask = max(ask, context.wallet.avg_cost * (Decimal(1) + self.min_edge))
            intents.append(IntentSpec(
                side=Side.SELL, order_type="LIMIT",
                quantity=context.wallet.base_qty,
                limit_price=quote(ask), reason_code="grid_sell"))

        return intents
