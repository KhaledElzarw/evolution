"""One-candle trading tick, shared by startup replay and the live loop.

Extracted verbatim from the devserver replay loop so that "catching up after an
outage" and "replaying history at startup" are literally the same code path as
live trading. ``TickEngine.process`` consumes exactly one CLOSED candle:

1. day boundary   — close the finished day's lessons and re-tune Darkhorse -
                    Daily (when a retuner is attached);
2. book expiry    — resting limits past their TTL become "expired" records;
3. due fills      — resting limits the candle traded through become LIMIT
                    intents this tick;
4. strategy tick  — every builtin-strategy wallet sees the snapshot + window;
5. committee tick — permanent wallets evaluate the five-domain committee on
                    their cadences;
6. execution      — everything submitted goes through the audited
                    ``ExecutionService.process_tick``.

Idempotency: ``ExecutionService`` enforces a per-wallet candle watermark (A01)
and the engine additionally refuses to re-process a candle at or before the
last one it consumed, so re-feeding a candle (poll overlap, restart replay) is
a structural no-op.

The engine is single-writer: exactly one thread may call ``process``. Readers
(the FastAPI view) receive data via published copies, never via these internals.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Protocol

from ..domain.dark_horse import (
    LIQUIDITY,
    REQUIRED_DOMAINS,
    TECHNICAL,
    DarkHorseAction,
    DomainReport,
    DomainStatus,
    EvidenceItem,
)
from ..domain.ledger import Side
from ..domain.market import MarketSnapshot
from ..domain.money import base as base_qty
from ..domain.money import quote
from ..domain.strategies import StrategyContext, WalletView
from .dark_horse import DomainSignal, synthesize
from .execution import ExecutionService, OrderIntent, OrderType
from .order_book import RestingBook, RestingOrder
from .portfolio import WalletSlot

FIVE_MIN_MS = 5 * 60 * 1000

#: Committee evidence: signatures for the pluggable evidence source. The dev
#: default derives everything from candles; the awareness service (live mode)
#: substitutes real macro/fundamental/onchain reports.
EvidenceFn = Callable[
    [tuple[MarketSnapshot, ...]],
    tuple[dict[str, DomainReport], dict[str, "DomainSignal"], dt.datetime],
]


def _money(value: Decimal) -> str:
    """Fixed-point decimal string (mirrors api.views.money; kept local so the
    application layer does not import the api layer)."""

    return f"{value:f}"


def _iso(ms: int) -> str:
    return (dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
            .replace(tzinfo=None).isoformat() + "Z")


def _realized_from_txn(txn) -> Decimal:
    """Realized P&L booked by a fill (0 for buys). The sell posts a
    ``realized_pnl`` leg equal to ``-realized`` (credit convention)."""

    return -sum((p.amount for p in txn.postings if p.account == "realized_pnl"),
                start=Decimal("0"))


def trade_record(snapshot: MarketSnapshot, intent: OrderIntent,
                 result, placed_ms: int | None = None) -> dict:
    """One row of a wallet's order history: what was asked, what happened.

    ``placed_ms`` is the open time of the candle the order was PLACED on — it
    differs from the fill candle for a resting limit order. When omitted the
    order was placed and resolved on the same candle (a market order).
    """

    ts = _iso(snapshot.close_time_ms)
    placed_at = _iso(placed_ms + FIVE_MIN_MS) if placed_ms is not None else ts
    filled = result.accepted
    txn = result.transaction
    price = result.fill_price
    filled_qty = txn.qty if txn is not None else Decimal("0")
    notional = (quote(price * filled_qty)
                if (price is not None and filled) else None)
    return {
        "order_id": intent.intent_id,
        "placed_at": placed_at,
        "filled_at": ts if filled else None,
        "side": intent.side.value,
        "order_type": intent.order_type.value,
        "requested_qty": _money(intent.quantity),
        "filled_qty": _money(filled_qty),
        "price": _money(price) if price is not None else None,
        "notional": _money(notional) if notional is not None else None,
        "fee": _money(txn.fee) if txn is not None else None,
        "status": "filled" if filled else "rejected",
        "reason": (result.reason.value if result.reason is not None
                   else intent.reason_code),
        "strategy_version_id": intent.strategy_version_id,
        "realized_pnl": (_money(_realized_from_txn(txn))
                         if txn is not None else None),
    }


def expired_record(snapshot: MarketSnapshot, order) -> dict:
    """History row for a resting limit order that timed out unfilled."""

    return {
        "order_id": order.order_id,
        "placed_at": _iso(order.placed_open_ms + FIVE_MIN_MS),
        "filled_at": None,
        "side": order.side.value,
        "order_type": "LIMIT",
        "requested_qty": _money(order.quantity),
        "filled_qty": "0.00000000",
        "price": _money(order.limit_price),
        "notional": None,
        "fee": None,
        "status": "expired",
        "reason": order.reason_code,
        "strategy_version_id": order.strategy_version_id,
        "realized_pnl": None,
    }


# ---- permanent-wallet committee (Dark Horse + Darkhorse - Daily) -----------

_FLAT_EPSILON = Decimal("0.001")  # <0.1% drift = no directional call


def committee_evidence(
    window: tuple[MarketSnapshot, ...],
) -> tuple[dict[str, DomainReport], dict[str, DomainSignal], dt.datetime]:
    """Five-domain evidence derived from the candle window alone.

    ``technical`` and ``liquidity_derivatives`` are genuinely derived from the
    candle window (short-horizon drift). The macro/fundamental/onchain feeds do
    not exist here, so those domains carry synthetic placeholder evidence
    following the long-window drift, with ``source_id`` labelling them as such
    — the REAL committee logic is exercised, but nothing pretends this default
    evidence source has production data feeds. Live mode swaps those three
    domains for the hourly awareness service's real reports.
    """

    last = window[-1]
    now = dt.datetime.fromtimestamp(last.close_time_ms / 1000,
                                    dt.timezone.utc).replace(tzinfo=None)
    closes = [c.close for c in window]

    def drift(n: int) -> Decimal:
        seg = closes[-min(n, len(closes)):]
        return (seg[-1] - seg[0]) / seg[0]

    # Each domain reads its own horizon so the evidence is not one number
    # repeated five times: technical/liquidity are short-horizon reads of the
    # real candles; the placeholder domains follow progressively longer drifts.
    horizons = {TECHNICAL: 24, LIQUIDITY: 36}
    placeholder_horizons = {"macro": 288, "bitcoin_fundamental": 144,
                            "onchain": 72}
    market_derived = {d: drift(n) for d, n in horizons.items()}
    moves = {d: market_derived.get(d,
                                   drift(placeholder_horizons.get(d, len(closes))))
             for d in REQUIRED_DOMAINS}

    reports: dict[str, DomainReport] = {}
    signals: dict[str, DomainSignal] = {}
    for domain, move in moves.items():
        confidence = min(Decimal("0.85"),
                         Decimal("0.50") + min(abs(move) * 25, Decimal("0.35")))
        derived = domain in market_derived
        reports[domain] = DomainReport(domain, DomainStatus.OK, (EvidenceItem(
            source_id="dev-market" if derived else "dev-harness-demo",
            metric=f"{domain}_drift",
            value=f"{move:.6f}",
            interpretation=("candle-window drift" if derived
                            else "synthetic dev placeholder"),
            confidence=confidence,
            source_time=now,
            retrieved_at=now,
            data_snapshot_id=last.snapshot_id,
        ),))
        bullish = None if abs(move) < _FLAT_EPSILON else move > 0
        signals[domain] = DomainSignal(domain, bullish, confidence)
    return reports, signals, now


@dataclass
class PermanentRunner:
    """Cadenced committee loop for one permanent wallet.

    ``entry_limit_bps`` / ``exit_limit_bps`` place accumulate/reduce as RESTING
    limit orders off the mark (0 = market). For Darkhorse - Daily these are read
    from its live params dict and re-tuned by the daily LLM adaptation loop; for
    Dark Horse they are fixed.
    """

    slot: WalletSlot
    cadence_seconds: int
    accumulate_fraction: Decimal
    reduce_fraction: Decimal
    entry_limit_bps: Decimal = Decimal("0")
    exit_limit_bps: Decimal = Decimal("0")
    last_eval_ms: int | None = None


def permanent_committee_intent(
    runner: PermanentRunner,
    snapshot: MarketSnapshot,
    window: tuple[MarketSnapshot, ...],
    evidence_fn: EvidenceFn = committee_evidence,
) -> tuple[Side, Decimal, str, Decimal | None] | None:
    """Evaluate the committee on cadence; map the decision to a spot order.

    Returns ``(side, quantity, reason, limit_price)`` — ``limit_price`` is
    ``None`` for a market order. Accumulate rests a bid below the mark and
    reduce rests an ask above it (both tunable); exit-to-cash is always a market
    order because a risk-off exit must not sit unfilled on the book.
    """

    if (runner.last_eval_ms is not None
            and snapshot.close_time_ms - runner.last_eval_ms
            < runner.cadence_seconds * 1000):
        return None
    runner.last_eval_ms = snapshot.close_time_ms

    wallet = runner.slot.wallet
    reports, signals, now = evidence_fn(window)
    decision = synthesize(
        reports, signals, now=now,
        strategy_version_id=runner.slot.strategy_version_id,
        holds_btc=wallet.base_qty > 0,
    )
    px = snapshot.mark_price
    limit_price: Decimal | None = None
    if decision.action is DarkHorseAction.ACCUMULATE:
        budget = quote(wallet.quote_cash * runner.accumulate_fraction)
        if budget < Decimal("10"):
            return None
        if runner.entry_limit_bps > 0:
            limit_price = quote(px * (Decimal(1) - runner.entry_limit_bps / Decimal(10_000)))
        qty = base_qty(budget / (limit_price or px))
        side = Side.BUY
    elif decision.action is DarkHorseAction.REDUCE:
        qty = base_qty(wallet.base_qty * runner.reduce_fraction)
        if runner.exit_limit_bps > 0:
            limit_price = quote(px * (Decimal(1) + runner.exit_limit_bps / Decimal(10_000)))
        side = Side.SELL
    elif decision.action is DarkHorseAction.EXIT_TO_CASH:
        qty = base_qty(wallet.base_qty)
        side = Side.SELL  # urgent risk-off -> market (limit_price stays None)
    else:
        return None
    if qty <= 0:
        return None
    return side, qty, decision.action.value, limit_price


def apply_daily_params(permanents, params: dict) -> None:
    """Push freshly-adapted tunables onto the live Darkhorse - Daily runner."""

    for pr in permanents:
        if pr.slot.kind == "dark_horse_daily":
            pr.accumulate_fraction = params["accumulate_fraction"]
            pr.reduce_fraction = params["reduce_fraction"]
            pr.cadence_seconds = int(params["signal_cadence_hours"] * 3600)
            pr.entry_limit_bps = params["entry_limit_bps"]
            pr.exit_limit_bps = params["exit_limit_bps"]


class DailyRetuner(Protocol):
    """Duck type for api.harness_adaptation.DailyReTuner (application must not
    import the api layer)."""

    def end_day(self, day, wallet_by_id, mark, version_by_id, day_fills) -> dict: ...

    def begin_day(self, wallet_by_id, mark) -> None: ...


@dataclass
class TickResult:
    """What one candle did: fill count plus new history rows by wallet."""

    fills_accepted: int = 0
    records: list[tuple[str, dict]] = field(default_factory=list)
    skipped: bool = False  # candle at/behind the watermark -> structural no-op


class TickEngine:
    """Single-writer trading engine advancing one closed candle at a time."""

    def __init__(
        self,
        *,
        runners: list,  # mutable [ (WalletSlot, strategy, state), ... ]
        permanents: list[PermanentRunner],
        execution: ExecutionService,
        book: RestingBook,
        retuner: DailyRetuner | None = None,
        evidence_fn: EvidenceFn = committee_evidence,
        intent_prefix: str = "dev-i",
    ) -> None:
        self.runners = runners
        self.permanents = permanents
        self.execution = execution
        self.book = book
        self.retuner = retuner
        self.evidence_fn = evidence_fn
        self.intent_prefix = intent_prefix

        _perm_slots = [p.slot for p in permanents]
        all_slots = [r[0] for r in runners] + _perm_slots
        self.wallet_by_id = {s.wallet.wallet_id: s.wallet for s in all_slots}
        self.version_by_id = {s.wallet.wallet_id: s.strategy_version_id
                              for s in all_slots}

        self.trades: dict[str, list[dict]] = {}
        self.fills = 0
        self.seq = 0
        self.prev: MarketSnapshot | None = None
        self.current_day: str | None = None
        self.day_fills: dict[str, list[dict]] = {}
        self.last_processed_open_ms: int = -1

    # -- main entry ----------------------------------------------------------

    def process(self, snapshot: MarketSnapshot,
                window: tuple[MarketSnapshot, ...]) -> TickResult:
        """Advance the whole portfolio by one closed candle (idempotent)."""

        if snapshot.open_time_ms <= self.last_processed_open_ms:
            return TickResult(skipped=True)
        self.last_processed_open_ms = snapshot.open_time_ms

        result = TickResult()

        # Day boundary: close out the completed day's lessons, adapt Darkhorse -
        # Daily, and apply the new params before this day's candles run.
        day = _iso(snapshot.close_time_ms)[:10]
        if self.retuner is not None and day != self.current_day:
            if self.current_day is not None and self.prev is not None:
                new_params = self.retuner.end_day(
                    self.current_day, self.wallet_by_id, self.prev.mark_price,
                    self.version_by_id, self.day_fills)
                apply_daily_params(self.permanents, new_params)
            self.retuner.begin_day(self.wallet_by_id, snapshot.mark_price)
            self.day_fills = {}
            self.current_day = day

        self.book.observe_spacing(snapshot, self.prev)
        self.prev = snapshot
        batch: list[tuple] = []
        placed_ms_by_id: dict[str, int] = {}

        # (1) Expire resting orders that have sat unfilled past their TTL.
        for order in self.book.expire(snapshot):
            self._record(result, order.wallet_id,
                         expired_record(snapshot, order))
        # (2) Resting orders the candle traded through become fills this tick.
        for order in self.book.due_fills(snapshot):
            iid = self._next_id()
            placed_ms_by_id[iid] = order.placed_open_ms
            batch.append((self.wallet_by_id[order.wallet_id], OrderIntent(
                intent_id=iid, wallet_id=order.wallet_id,
                strategy_version_id=order.strategy_version_id,
                side=order.side, order_type=OrderType.LIMIT,
                quantity=order.quantity, limit_price=order.limit_price,
                reason_code=order.reason_code,
            )))

        def place(wallet, version_id, side, order_type, qty, limit_price, reason):
            """Market -> execute this tick; not-yet-marketable limit -> rest."""
            iid = self._next_id()
            if order_type is OrderType.LIMIT and limit_price is not None:
                # A limit placed at this close cannot fill on its own candle (the
                # high/low already happened) — it rests and is checked next tick.
                self.book.rest(RestingOrder(
                    order_id=iid, wallet_id=wallet.wallet_id,
                    strategy_version_id=version_id, side=side,
                    limit_price=limit_price, quantity=qty, reason_code=reason,
                    placed_open_ms=snapshot.open_time_ms))
            else:
                batch.append((wallet, OrderIntent(
                    intent_id=iid, wallet_id=wallet.wallet_id,
                    strategy_version_id=version_id, side=side,
                    order_type=order_type, quantity=qty, limit_price=limit_price,
                    reason_code=reason)))

        # (3) Strategy wallets.
        for idx, (slot, strategy, state) in enumerate(self.runners):
            w = slot.wallet
            ctx = StrategyContext(
                snapshot=snapshot,
                wallet=WalletView(w.quote_cash, w.base_qty, w.avg_cost),
                candles=window,
            )
            decision = strategy.on_market_snapshot(ctx, state)
            self.runners[idx] = (slot, strategy, decision.state)
            for spec in decision.intents:
                place(w, slot.strategy_version_id, spec.side,
                      OrderType(spec.order_type), spec.quantity,
                      spec.limit_price, spec.reason_code)

        # (4) Permanent wallets: the real five-domain committee on their cadences.
        for pr in self.permanents:
            order = permanent_committee_intent(pr, snapshot, window,
                                               evidence_fn=self.evidence_fn)
            if order is None:
                continue
            side, qty, reason, limit_price = order
            place(pr.slot.wallet, pr.slot.strategy_version_id, side,
                  OrderType.LIMIT if limit_price is not None else OrderType.MARKET,
                  qty, limit_price, f"committee_{reason}")

        # (5) Execute everything submitted this tick.
        intents_by_id = {intent.intent_id: intent for _, intent in batch}
        for exec_result in self.execution.process_tick(snapshot, batch):
            self.fills += exec_result.accepted
            result.fills_accepted += exec_result.accepted
            intent = intents_by_id[exec_result.intent_id]
            record = trade_record(
                snapshot, intent, exec_result,
                placed_ms=placed_ms_by_id.get(exec_result.intent_id))
            self._record(result, exec_result.wallet_id, record)
            if self.retuner is not None and exec_result.accepted:
                self.day_fills.setdefault(exec_result.wallet_id, []).append(record)

        return result

    # -- persistence ---------------------------------------------------------

    def snapshot_state(self) -> dict:
        """JSON-safe engine state: everything needed to resume after restart.

        Decimals are stringified; the restore side reconstructs them. Candle
        history is NOT included — on restart the live loop gap-replays from
        ``last_processed_open_ms``, so market data always comes from the venue.
        """

        def wallet_state(w) -> dict:
            return {
                "quote_cash": str(w.quote_cash), "base_qty": str(w.base_qty),
                "reserved_base": str(w.reserved_base),
                "avg_cost": str(w.avg_cost),
                "realized_pnl": str(w.realized_pnl),
                "total_fees": str(w.total_fees),
                "txn_count": w._txn_count,
                "idempotency_keys": sorted(w._idempotency_keys),
            }

        def candle_state(c: MarketSnapshot | None) -> dict | None:
            if c is None:
                return None
            return {
                "snapshot_id": c.snapshot_id, "source": c.source,
                "symbol": c.symbol, "interval": c.interval,
                "open_time_ms": c.open_time_ms,
                "close_time_ms": c.close_time_ms, "is_closed": c.is_closed,
                "open": str(c.open), "high": str(c.high), "low": str(c.low),
                "close": str(c.close), "volume": str(c.volume),
                "retrieved_at_ms": c.retrieved_at_ms,
                "source_time_ms": c.source_time_ms,
            }

        return {
            "last_processed_open_ms": self.last_processed_open_ms,
            "seq": self.seq,
            "fills": self.fills,
            "current_day": self.current_day,
            "prev_candle": candle_state(self.prev),
            "wallets": {wid: wallet_state(w)
                        for wid, w in self.wallet_by_id.items()},
            "strategy_states": {slot.wallet.wallet_id: state
                                for slot, _strategy, state in self.runners},
            "permanents": {p.slot.wallet.wallet_id: {
                "last_eval_ms": p.last_eval_ms,
                "cadence_seconds": p.cadence_seconds,
                "accumulate_fraction": str(p.accumulate_fraction),
                "reduce_fraction": str(p.reduce_fraction),
                "entry_limit_bps": str(p.entry_limit_bps),
                "exit_limit_bps": str(p.exit_limit_bps),
            } for p in self.permanents},
            "book": {
                "step_ms": self.book._step_ms,
                "orders": [{
                    "order_id": o.order_id, "wallet_id": o.wallet_id,
                    "strategy_version_id": o.strategy_version_id,
                    "side": o.side.value, "limit_price": str(o.limit_price),
                    "quantity": str(o.quantity),
                    "reason_code": o.reason_code,
                    "placed_open_ms": o.placed_open_ms,
                    "expires_after_candles": o.expires_after_candles,
                } for orders in self.book._orders.values() for o in orders],
            },
            "execution_watermark": dict(self.execution._watermark),
            "trades": self.trades,
            "day_fills": self.day_fills,
        }

    def restore_state(self, state: dict) -> None:
        """Rebuild engine internals from ``snapshot_state`` output.

        The engine must have been constructed with the SAME portfolio shape
        (wallet ids and strategies are deterministic); a wallet-id mismatch
        raises so the caller falls back to a fresh replay instead of trading
        on half-restored balances.
        """

        wallets = state["wallets"]
        if set(wallets) != set(self.wallet_by_id):
            raise ValueError("wallet-id mismatch: snapshot is for a different "
                             "portfolio shape")
        for wid, ws in wallets.items():
            w = self.wallet_by_id[wid]
            w.quote_cash = Decimal(ws["quote_cash"])
            w.base_qty = Decimal(ws["base_qty"])
            w.reserved_base = Decimal(ws["reserved_base"])
            w.avg_cost = Decimal(ws["avg_cost"])
            w.realized_pnl = Decimal(ws["realized_pnl"])
            w.total_fees = Decimal(ws["total_fees"])
            w._txn_count = ws["txn_count"]
            w._idempotency_keys = set(ws["idempotency_keys"])

        states = state["strategy_states"]
        for idx, (slot, strategy, _old) in enumerate(self.runners):
            if slot.wallet.wallet_id in states:
                self.runners[idx] = (slot, strategy,
                                     states[slot.wallet.wallet_id])

        for p in self.permanents:
            ps = state["permanents"].get(p.slot.wallet.wallet_id)
            if ps is None:
                continue
            p.last_eval_ms = ps["last_eval_ms"]
            p.cadence_seconds = ps["cadence_seconds"]
            p.accumulate_fraction = Decimal(ps["accumulate_fraction"])
            p.reduce_fraction = Decimal(ps["reduce_fraction"])
            p.entry_limit_bps = Decimal(ps["entry_limit_bps"])
            p.exit_limit_bps = Decimal(ps["exit_limit_bps"])

        self.book._step_ms = state["book"]["step_ms"]
        self.book._orders = {}
        for o in state["book"]["orders"]:
            self.book.rest(RestingOrder(
                order_id=o["order_id"], wallet_id=o["wallet_id"],
                strategy_version_id=o["strategy_version_id"],
                side=Side(o["side"]), limit_price=Decimal(o["limit_price"]),
                quantity=Decimal(o["quantity"]), reason_code=o["reason_code"],
                placed_open_ms=o["placed_open_ms"],
                expires_after_candles=o["expires_after_candles"]))

        self.execution._watermark = {k: int(v) for k, v
                                     in state["execution_watermark"].items()}

        prev = state.get("prev_candle")
        if prev is not None:
            self.prev = MarketSnapshot(
                snapshot_id=prev["snapshot_id"], source=prev["source"],
                symbol=prev["symbol"], interval=prev["interval"],
                open_time_ms=prev["open_time_ms"],
                close_time_ms=prev["close_time_ms"],
                is_closed=prev["is_closed"], open=Decimal(prev["open"]),
                high=Decimal(prev["high"]), low=Decimal(prev["low"]),
                close=Decimal(prev["close"]), volume=Decimal(prev["volume"]),
                retrieved_at_ms=prev["retrieved_at_ms"],
                source_time_ms=prev["source_time_ms"])

        self.last_processed_open_ms = state["last_processed_open_ms"]
        self.seq = state["seq"]
        self.fills = state["fills"]
        self.current_day = state["current_day"]
        self.trades = {k: list(v) for k, v in state["trades"].items()}
        self.day_fills = {k: list(v) for k, v in state["day_fills"].items()}

    # -- helpers -------------------------------------------------------------

    def _next_id(self) -> str:
        self.seq += 1
        return f"{self.intent_prefix}{self.seq}"

    def _record(self, result: TickResult, wallet_id: str, record: dict) -> None:
        self.trades.setdefault(wallet_id, []).append(record)
        result.records.append((wallet_id, record))
