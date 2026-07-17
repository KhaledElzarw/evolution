"""Fixed-point money and market primitives.

Domain layer: no FastAPI, no SQLAlchemy, no filesystem, no env, no network.

All monetary and quantity values use :class:`decimal.Decimal` with explicit,
currency-specific quantization. Binary ``float`` is never used for ledger
mutation. This module is the single source of rounding policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, getcontext
from enum import Enum
from typing import Final

# A wide precision context so intermediate products (price * qty) never lose
# significance before we quantize back to a currency scale.
getcontext().prec = 40

# Quote currency (USDT) is quantized to cents; base (BTC) to satoshis.
QUOTE_SCALE: Final[Decimal] = Decimal("0.01")
BASE_SCALE: Final[Decimal] = Decimal("0.00000001")

ZERO_QUOTE: Final[Decimal] = Decimal("0.00")
ZERO_BASE: Final[Decimal] = Decimal("0.00000000")


class Currency(str, Enum):
    """The only two assets a spot BTCUSDT paper wallet holds."""

    USDT = "USDT"
    BTC = "BTC"


def _to_decimal(value: str | int | Decimal) -> Decimal:
    """Coerce a *non-float* value to Decimal.

    Passing a binary ``float`` is rejected on purpose: floats silently carry
    representation error and must never enter the ledger. Callers holding a
    float must stringify it at the boundary and accept the rounding here.
    """

    if isinstance(value, float):
        raise TypeError(
            "float is not allowed in money math; pass str/int/Decimal"
        )
    return Decimal(value)


def quote(value: str | int | Decimal) -> Decimal:
    """Quantize a quote-currency (USDT) amount to 2 dp, banker-safe HALF_UP."""

    return _to_decimal(value).quantize(QUOTE_SCALE, rounding=ROUND_HALF_UP)


def base(value: str | int | Decimal) -> Decimal:
    """Quantize a base-currency (BTC) amount to 8 dp, HALF_UP."""

    return _to_decimal(value).quantize(BASE_SCALE, rounding=ROUND_HALF_UP)


def price(value: str | int | Decimal) -> Decimal:
    """Quantize a price to quote scale (2 dp for USDT quotes)."""

    return _to_decimal(value).quantize(QUOTE_SCALE, rounding=ROUND_HALF_UP)


def notional(qty: Decimal, px: Decimal) -> Decimal:
    """Quote-value of ``qty`` BTC at ``px``, quantized to quote scale.

    Uses HALF_UP so a computed notional matches how an exchange would bill it.
    """

    return (qty * px).quantize(QUOTE_SCALE, rounding=ROUND_HALF_UP)


def apply_fee(gross: Decimal, fee_rate: Decimal) -> Decimal:
    """Fee charged on a gross quote amount, rounded UP-to-cents against the payer.

    Fees always round in the exchange's favour (ROUND_UP would over-bill by a
    cent on ties; we use HALF_UP for symmetry and document it). This value is
    applied to a ledger exactly once by the caller.
    """

    return (gross * fee_rate).quantize(QUOTE_SCALE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True, slots=True)
class ExchangeFilters:
    """Binance public spot exchange filters for BTCUSDT.

    Values are Decimal so filter arithmetic stays in fixed point.
    """

    tick_size: Decimal  # PRICE_FILTER
    step_size: Decimal  # LOT_SIZE
    min_notional: Decimal  # NOTIONAL / MIN_NOTIONAL
    min_qty: Decimal = Decimal("0")

    def round_price_down(self, px: Decimal) -> Decimal:
        return (px // self.tick_size) * self.tick_size

    def round_qty_down(self, qty: Decimal) -> Decimal:
        """Floor a quantity to the LOT_SIZE step (never round up an order)."""

        stepped = (qty // self.step_size) * self.step_size
        return stepped.quantize(BASE_SCALE, rounding=ROUND_DOWN)

    def passes_min_notional(self, qty: Decimal, px: Decimal) -> bool:
        return notional(qty, px) >= self.min_notional

    def passes_min_qty(self, qty: Decimal) -> bool:
        return qty >= self.min_qty


# Representative BTCUSDT public filters (documented simulation assumption; the
# live values are fetched via the market-data adapter in a later phase).
DEFAULT_BTCUSDT_FILTERS: Final[ExchangeFilters] = ExchangeFilters(
    tick_size=Decimal("0.01"),
    step_size=Decimal("0.00001"),
    min_notional=Decimal("5.00"),
    min_qty=Decimal("0.00001"),
)
