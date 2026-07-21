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
from .indicators import atr, sma


class GridStrategy(BuiltinStrategy):
    """A profitable laddered inventory grid.

    A grid makes money by turning volatility into round trips: it scales INTO
    dips across a ladder of bids and scales OUT of bounces across a ladder of
    asks, banking the spacing between them (minus fees) on each pair. Two design
    choices make this actually profitable rather than a dip-buying accumulator:

    * **Laddered both sides** — several bids below and several asks above rest at
      once, so many oscillations are captured, not just one.
    * **Cost-anchored, fee-aware asks** — the sell ladder is anchored to the
      position's average cost (``avg_cost + k·spacing``), starting just above
      break-even-plus-edge. Every sell therefore locks in a profit and the
      nearest ask catches even a small bounce — the failure mode of the old
      single far ask (buy the dips, never sell) is gone.

    Capital is spread across the bid ladder (``deploy_fraction``) so it can fill
    multiple levels as price falls, and inventory is released in equal slices.
    """

    family = "inventory_grid"
    min_warmup = 60  # need the slow trend average before trading
    atr_period = 14
    n_levels = 5                              # ladder depth per side
    spacing_atr_mult = Decimal("0.5")         # step between levels (in ATR)
    recenter_displacement = Decimal("0.08")   # hold the range; re-anchor only on a big move
    recenter_min_candles = 40
    max_inventory_fraction = Decimal("0.90")  # stop bidding past this equity share
    deploy_fraction = Decimal("0.60")         # total cash spread across the bid ladder
    min_edge = Decimal("0.004")               # fee-aware floor above avg cost
    regrid_min_candles = 3                     # re-post the ladder at most this often
    # Regime filter: a grid profits in RANGES and bleeds buying dips in a
    # sustained downtrend. Pause the bid ladder when the fast average is below
    # the slow one (a robust read of a down-regime, sensitive even to a slow
    # grind); the ask ladder keeps working off any bounce.
    trend_fast = 20
    trend_slow = 60

    def initialize(self) -> dict[str, Any]:
        state = super().initialize()
        state.update({"anchor": None, "anchor_index": -10_000,
                      "last_grid_index": -10_000})
        return state

    def grid_intents(self, context: StrategyContext,
                     candles: tuple[MarketSnapshot, ...],
                     state: dict[str, Any]) -> list[IntentSpec]:
        """Post a ladder of resting bids below and profit-taking asks above.

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
        wallet = context.wallet
        px = context.snapshot.mark_price
        equity = wallet.quote_cash + wallet.base_qty * px
        inventory_ratio = (wallet.base_qty * px) / equity if equity > 0 else Decimal(1)
        intents: list[IntentSpec] = []

        # Regime gate for the bid side: skip accumulation while the fast average
        # sits below the slow one (a down-regime — don't ladder into a decline).
        closes = [c.close for c in candles]
        fast_ma = sma(closes, self.trend_fast)
        slow_ma = sma(closes, self.trend_slow)
        downtrend = (fast_ma is not None and slow_ma is not None
                     and fast_ma < slow_ma)

        # BID LADDER below the anchor — accumulate progressively on dips.
        if inventory_ratio < self.max_inventory_fraction and not downtrend:
            per_level = quote(wallet.quote_cash * self.deploy_fraction / self.n_levels)
            if per_level >= Decimal("10"):
                for k in range(1, self.n_levels + 1):
                    bid = anchor - k * spacing
                    if bid <= 0:
                        break
                    qty = base_qty(per_level / bid)
                    if qty > 0:
                        intents.append(IntentSpec(
                            side=Side.BUY, order_type="LIMIT", quantity=qty,
                            limit_price=quote(bid), reason_code="grid_buy"))

        # ASK LADDER anchored to average cost — every sell books a profit, and
        # the nearest ask catches a small bounce off the accumulated position.
        held = base_qty(wallet.base_qty)
        if held > 0 and wallet.avg_cost > 0:
            slice_qty = base_qty(held / self.n_levels)
            if slice_qty > 0:
                base_ask = wallet.avg_cost * (Decimal(1) + self.min_edge)
                for k in range(self.n_levels):
                    ask = quote(base_ask + k * spacing)
                    intents.append(IntentSpec(
                        side=Side.SELL, order_type="LIMIT", quantity=slice_qty,
                        limit_price=ask, reason_code="grid_sell"))

        return intents
