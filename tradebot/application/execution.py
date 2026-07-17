"""Deterministic multi-wallet paper execution.

Closes audit findings:

* **A01** — a closed-candle order intent can fill against a given candle at most
  once per wallet. A per-wallet ``candle watermark`` records the last processed
  ``open_time_ms``; re-submitting against the same still-open (or already
  processed) candle is rejected, so replacement orders cannot repeatedly fill
  off one candle's high/low.
* **A11** — Binance public exchange filters (PRICE_FILTER / LOT_SIZE /
  MIN_NOTIONAL) are enforced before a fill is simulated.

Two-phase tick: (1) collect all intents against one immutable snapshot, then
(2) validate + execute deterministically. No wallet sees another's fill before
producing its own intent, and execution is independent of wallet iteration order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from ..domain.ledger import LedgerError, LedgerTransaction, Side, Wallet
from ..domain.market import MarketSnapshot
from ..domain.money import DEFAULT_BTCUSDT_FILTERS, ExchangeFilters, quote


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class RejectReason(str, Enum):
    DUPLICATE_CANDLE = "duplicate_candle"
    CANDLE_NOT_CLOSED = "candle_not_closed"
    LOT_SIZE = "lot_size"
    MIN_NOTIONAL = "min_notional"
    LIMIT_NOT_MARKETABLE = "limit_not_marketable"
    LEDGER_REJECTED = "ledger_rejected"
    NEGATIVE_QTY = "negative_qty"


@dataclass(frozen=True, slots=True)
class OrderIntent:
    intent_id: str
    wallet_id: str
    strategy_version_id: str
    side: Side
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    reason_code: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    intent_id: str
    wallet_id: str
    accepted: bool
    reason: RejectReason | None = None
    transaction: LedgerTransaction | None = None
    fill_price: Decimal | None = None


@dataclass(slots=True)
class ExecutionModel:
    """Documented, reproducible paper-execution assumptions.

    ``taker_fee_rate`` and ``slippage_rate`` are identical across all competing
    wallets. ``model_version`` is stored with every evaluation.
    """

    taker_fee_rate: Decimal = Decimal("0.0010")
    slippage_rate: Decimal = Decimal("0.0005")
    filters: ExchangeFilters = DEFAULT_BTCUSDT_FILTERS
    model_version: str = "paper-exec-v1"


@dataclass(slots=True)
class ExecutionService:
    model: ExecutionModel = field(default_factory=ExecutionModel)
    # A01 watermark: wallet_id -> last processed candle open_time_ms.
    _watermark: dict[str, int] = field(default_factory=dict)
    _txn_seq: int = 0

    def process_tick(
        self,
        snapshot: MarketSnapshot,
        intents: list[tuple[Wallet, OrderIntent]],
        *,
        require_closed: bool = True,
    ) -> list[ExecutionResult]:
        """Phase 2 of a tick: execute all intents against one shared snapshot.

        Deterministic and iteration-order independent: results depend only on
        each (wallet, intent, snapshot) triple, not on ordering.
        """

        results: list[ExecutionResult] = []
        for wallet, intent in intents:
            results.append(self._execute_one(snapshot, wallet, intent, require_closed))
        return results

    def _next_txn_id(self) -> str:
        self._txn_seq += 1
        return f"txn-{self._txn_seq:012d}"

    def _execute_one(
        self,
        snapshot: MarketSnapshot,
        wallet: Wallet,
        intent: OrderIntent,
        require_closed: bool,
    ) -> ExecutionResult:
        def reject(reason: RejectReason) -> ExecutionResult:
            return ExecutionResult(intent.intent_id, wallet.wallet_id, False, reason)

        if intent.quantity <= 0:
            return reject(RejectReason.NEGATIVE_QTY)

        if require_closed and not snapshot.is_closed:
            return reject(RejectReason.CANDLE_NOT_CLOSED)

        # A01: refuse a second execution against the same-or-earlier candle.
        last = self._watermark.get(wallet.wallet_id)
        if last is not None and snapshot.open_time_ms <= last:
            return reject(RejectReason.DUPLICATE_CANDLE)

        filters = self.model.filters
        qty = filters.round_qty_down(intent.quantity)
        if not filters.passes_min_qty(qty) or qty <= 0:
            return reject(RejectReason.LOT_SIZE)

        fill_price = self._fill_price(snapshot, intent)
        if fill_price is None:
            return reject(RejectReason.LIMIT_NOT_MARKETABLE)

        if not filters.passes_min_notional(qty, fill_price):
            return reject(RejectReason.MIN_NOTIONAL)

        idem = f"{wallet.wallet_id}:{snapshot.snapshot_id}:{intent.intent_id}"
        try:
            txn = wallet.apply_fill(
                transaction_id=self._next_txn_id(),
                order_id=intent.intent_id,
                fill_id=f"fill-{intent.intent_id}",
                idempotency_key=idem,
                strategy_version_id=intent.strategy_version_id,
                market_snapshot_id=snapshot.snapshot_id,
                side=intent.side,
                qty=qty,
                fill_price=fill_price,
                fee_rate=self.model.taker_fee_rate,
            )
        except LedgerError:
            return reject(RejectReason.LEDGER_REJECTED)

        # Advance watermark only after a successful fill on a closed candle.
        self._watermark[wallet.wallet_id] = snapshot.open_time_ms
        return ExecutionResult(
            intent.intent_id, wallet.wallet_id, True, None, txn, fill_price
        )

    def _fill_price(
        self, snapshot: MarketSnapshot, intent: OrderIntent
    ) -> Decimal | None:
        """Conservative fill price with slippage against the taker.

        MARKET: fill at close adjusted by slippage in the adverse direction.
        LIMIT: marketable only if the candle traded through the limit; fill at
        the limit price (pessimistic — no price improvement).
        """

        close = snapshot.mark_price
        slip = self.model.slippage_rate
        if intent.order_type is OrderType.MARKET:
            if intent.side is Side.BUY:
                return quote(close * (Decimal("1") + slip))
            return quote(close * (Decimal("1") - slip))

        # LIMIT
        limit = intent.limit_price
        if limit is None:
            return None
        if intent.side is Side.BUY:
            # Buy limit fills only if price dipped to/below the limit.
            return quote(limit) if snapshot.low <= limit else None
        # Sell limit fills only if price rose to/above the limit.
        return quote(limit) if snapshot.high >= limit else None
