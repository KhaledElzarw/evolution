"""Wallet-isolated double-entry ledger with weighted-average cost basis.

Closes audit findings:

* **A02** — acquisition fees affect cost basis exactly once; disposal fees
  affect proceeds exactly once. Net P&L is derived from event postings, never
  by subtracting a fee total from a fee-inclusive figure.
* **A04/A03** — every wallet is an isolated aggregate; there is no shared
  global mutable balance. Cross-wallet postings are structurally impossible
  because a :class:`Wallet` only mutates its own state.

Accounting model: weighted-average cost for BTCUSDT paper wallets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from .money import BASE_SCALE, QUOTE_SCALE, apply_fee, base, notional, quote


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class LedgerError(Exception):
    """Raised when an accounting invariant would be violated."""


@dataclass(frozen=True, slots=True)
class Posting:
    """One leg of a balanced transaction. Quote postings net to zero."""

    account: str  # e.g. "quote_cash", "base_asset", "fee_expense"
    currency: str
    amount: Decimal  # signed: credit positive, debit negative (quote terms)


@dataclass(frozen=True, slots=True)
class LedgerTransaction:
    transaction_id: str
    wallet_id: str
    order_id: str
    fill_id: str
    idempotency_key: str
    strategy_version_id: str
    market_snapshot_id: str
    side: Side
    qty: Decimal
    price: Decimal
    fee: Decimal
    postings: tuple[Posting, ...]

    def quote_balance_check(self) -> Decimal:
        """Sum of quote-denominated postings; must be zero for a balanced txn."""

        return sum(
            (p.amount for p in self.postings if p.currency == "USDT"),
            start=Decimal("0"),
        )


@dataclass(slots=True)
class Wallet:
    """An isolated spot BTCUSDT paper wallet.

    Holds quote cash (USDT) and base asset (BTC) with a weighted-average cost.
    Spot only: no leverage, margin, or shorting. A SELL may only reduce owned,
    unreserved BTC.
    """

    wallet_id: str
    quote_cash: Decimal = field(default_factory=lambda: quote("10000.00"))
    base_qty: Decimal = field(default_factory=lambda: base("0"))
    reserved_base: Decimal = field(default_factory=lambda: base("0"))
    avg_cost: Decimal = field(default_factory=lambda: quote("0"))  # per-BTC, fee-inclusive
    realized_pnl: Decimal = field(default_factory=lambda: quote("0"))
    total_fees: Decimal = field(default_factory=lambda: quote("0"))
    _txn_count: int = 0
    _idempotency_keys: set[str] = field(default_factory=set)

    @property
    def available_base(self) -> Decimal:
        return base(self.base_qty - self.reserved_base)

    def equity(self, mark_price: Decimal) -> Decimal:
        """Equity = quote cash + base_qty * mark_price."""

        return quote(self.quote_cash + notional(self.base_qty, mark_price))

    def unrealized_pnl(self, mark_price: Decimal) -> Decimal:
        if self.base_qty <= 0:
            return quote("0")
        market_value = notional(self.base_qty, mark_price)
        cost_value = notional(self.base_qty, self.avg_cost)
        return quote(market_value - cost_value)

    # -- mutation -----------------------------------------------------------

    def apply_fill(
        self,
        *,
        transaction_id: str,
        order_id: str,
        fill_id: str,
        idempotency_key: str,
        strategy_version_id: str,
        market_snapshot_id: str,
        side: Side,
        qty: Decimal,
        fill_price: Decimal,
        fee_rate: Decimal,
    ) -> LedgerTransaction:
        """Apply one atomic fill, returning a balanced ledger transaction.

        Idempotent on ``idempotency_key`` — a duplicate raises LedgerError so
        callers cannot double-post the same fill.
        """

        if idempotency_key in self._idempotency_keys:
            raise LedgerError(f"duplicate idempotency key: {idempotency_key}")
        if qty <= 0:
            raise LedgerError("fill quantity must be positive")
        if fill_price <= 0:
            raise LedgerError("fill price must be positive")

        qty = base(qty)
        fill_price = quote(fill_price)
        gross = notional(qty, fill_price)
        fee = apply_fee(gross, fee_rate)

        if side is Side.BUY:
            txn = self._apply_buy(qty, fill_price, gross, fee)
        else:
            txn = self._apply_sell(qty, fill_price, gross, fee)

        postings = txn
        self.total_fees = quote(self.total_fees + fee)
        self._idempotency_keys.add(idempotency_key)
        self._txn_count += 1

        transaction = LedgerTransaction(
            transaction_id=transaction_id,
            wallet_id=self.wallet_id,
            order_id=order_id,
            fill_id=fill_id,
            idempotency_key=idempotency_key,
            strategy_version_id=strategy_version_id,
            market_snapshot_id=market_snapshot_id,
            side=side,
            qty=qty,
            price=fill_price,
            fee=fee,
            postings=postings,
        )
        # Invariant: quote postings net to zero.
        if transaction.quote_balance_check() != Decimal("0"):
            raise LedgerError("unbalanced transaction: quote postings != 0")
        self._assert_invariants()
        return transaction

    def _apply_buy(
        self, qty: Decimal, fill_price: Decimal, gross: Decimal, fee: Decimal
    ) -> tuple[Posting, ...]:
        total_debit = quote(gross + fee)
        if total_debit > self.quote_cash:
            raise LedgerError("insufficient quote cash for BUY (no overspend)")

        # A02: acquisition fee is folded into cost basis exactly ONCE, here.
        old_cost_value = notional(self.base_qty, self.avg_cost)
        new_cost_value = quote(old_cost_value + gross + fee)
        new_base = base(self.base_qty + qty)
        self.avg_cost = (
            quote(new_cost_value / new_base) if new_base > 0 else quote("0")
        )
        self.base_qty = new_base
        self.quote_cash = quote(self.quote_cash - total_debit)

        # A02: fee capitalized into the base_asset cost leg exactly once.
        return (
            Posting("quote_cash", "USDT", -total_debit),
            Posting("base_asset", "USDT", total_debit),
            Posting("base_asset", "BTC", qty),
        )

    def _apply_sell(
        self, qty: Decimal, fill_price: Decimal, gross: Decimal, fee: Decimal
    ) -> tuple[Posting, ...]:
        if qty > self.available_base:
            raise LedgerError("cannot SELL more than owned unreserved BTC (no short)")

        # Cost basis released for the sold quantity (fee-inclusive avg cost).
        cost_released = notional(qty, self.avg_cost)
        # A02: disposal fee reduces proceeds exactly ONCE, here.
        net_proceeds = quote(gross - fee)
        realized = quote(net_proceeds - cost_released)

        self.base_qty = base(self.base_qty - qty)
        self.quote_cash = quote(self.quote_cash + net_proceeds)
        self.realized_pnl = quote(self.realized_pnl + realized)
        if self.base_qty <= 0:
            self.base_qty = base("0")
            self.avg_cost = quote("0")

        # Double-entry (USDT legs sum to zero). Sign convention: credit
        # positive. The realized_pnl leg carries the TRUE net realized P&L
        # (a gain is a credit to equity -> negative here); its magnitude equals
        # the wallet's realized_pnl delta. The base_asset contra leg is the
        # balancing plug (inventory basis released plus the disposal fee drawn
        # against the conversion). This is NOT the old `cost_released - gross`
        # plug, which mislabelled a sign-inverted gross figure as realized P&L.
        base_contra = quote(-(cost_released + fee))
        realized_leg = quote(-realized)
        # Check: net_proceeds + fee - realized - (cost_released + fee)
        #      = net_proceeds - realized - cost_released = 0.
        return (
            Posting("base_asset", "BTC", -qty),
            Posting("quote_cash", "USDT", net_proceeds),
            Posting("fee_expense", "USDT", fee),
            Posting("base_asset", "USDT", base_contra),
            Posting("realized_pnl", "USDT", realized_leg),
        )

    def _assert_invariants(self) -> None:
        if self.quote_cash < Decimal("0"):
            raise LedgerError("quote balance became negative")
        if self.base_qty < Decimal("0"):
            raise LedgerError("base balance became negative")
        if self.reserved_base > self.base_qty:
            raise LedgerError("reserved exceeds owned base")
        if self.quote_cash != self.quote_cash.quantize(QUOTE_SCALE):
            raise LedgerError("quote cash lost quantization")
        if self.base_qty != self.base_qty.quantize(BASE_SCALE):
            raise LedgerError("base qty lost quantization")
