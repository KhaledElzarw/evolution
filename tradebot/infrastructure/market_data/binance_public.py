"""Live BTCUSDT market data from Binance's public endpoint.

**No credentials.** Public market data needs none, and requiring exchange keys
for a paper platform was audit finding A10. This adapter only ever issues GET
requests to `data-api.binance.vision`, and every URL is revalidated against the
DataBroker allowlist before it leaves the process.

Correctness notes that matter for the execution engine:

* Binance returns the **in-progress** candle last. It is marked `is_closed=False`
  so the A01 watermark and the strategies' closed-candle guards reject it. Only
  completed candles ever drive a decision.
* Prices arrive as decimal *strings* and are parsed straight to `Decimal` — they
  never pass through a binary float.
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Protocol

from ...domain.market import MarketSnapshot
from ...domain.money import ExchangeFilters, base, quote
from ..data_broker.policy import validate_request

BINANCE_HOST = "data-api.binance.vision"
BASE_URL = f"https://{BINANCE_HOST}"
SYMBOL = "BTCUSDT"
SOURCE = "binance_public"


class HttpGet(Protocol):  # pragma: no cover - structural
    def __call__(self, url: str, timeout: float = 10.0) -> tuple[int, object]: ...


def _default_get(url: str, timeout: float = 10.0) -> tuple[int, object]:
    import httpx

    r = httpx.get(url, timeout=timeout,
                  headers={"User-Agent": "tradebot-research/1.0"})
    return r.status_code, (r.json() if r.content else None)


class MarketDataError(RuntimeError):
    """Raised when live data cannot be obtained or is malformed."""


def _check(url: str) -> None:
    """Deny-by-default: the allowlist vets scheme/host/port/method/path + DNS."""

    validate_request(url, "GET")


def fetch_klines(interval: str = "5m", limit: int = 500,
                 http_get: HttpGet = _default_get,
                 now_ms: int | None = None) -> tuple[MarketSnapshot, ...]:
    """Fetch recent BTCUSDT candles as immutable snapshots, oldest first."""

    if limit < 1 or limit > 1000:
        raise MarketDataError("limit must be between 1 and 1000")
    url = (f"{BASE_URL}/api/v3/klines?symbol={SYMBOL}"
           f"&interval={interval}&limit={limit}")
    _check(url)
    status, payload = http_get(url)
    if status != 200 or not isinstance(payload, list):
        raise MarketDataError(f"klines request failed: HTTP {status}")

    stamp = now_ms if now_ms is not None else int(time.time() * 1000)
    out: list[MarketSnapshot] = []
    for row in payload:
        try:
            open_ms, o, h, low, c, vol, close_ms = (
                int(row[0]), row[1], row[2], row[3], row[4], row[5], int(row[6])
            )
        except (IndexError, TypeError, ValueError) as exc:
            raise MarketDataError(f"malformed kline row: {exc}") from None
        # The final candle is still forming until its close time passes.
        is_closed = close_ms < stamp
        out.append(MarketSnapshot(
            snapshot_id=f"{SOURCE}:{SYMBOL}:{interval}:{open_ms}",
            source=SOURCE, symbol=SYMBOL, interval=interval,
            open_time_ms=open_ms, close_time_ms=close_ms, is_closed=is_closed,
            open=quote(o), high=quote(h), low=quote(low), close=quote(c),
            volume=base(vol),
            retrieved_at_ms=stamp, source_time_ms=close_ms,
        ))
    return tuple(out)


def closed_only(snapshots: tuple[MarketSnapshot, ...]) -> tuple[MarketSnapshot, ...]:
    """Drop the in-progress candle. Strategies must only see completed bars."""

    return tuple(s for s in snapshots if s.is_closed)


def fetch_exchange_filters(http_get: HttpGet = _default_get) -> ExchangeFilters:
    """Read the REAL tick size / lot size / min notional from the exchange."""

    url = f"{BASE_URL}/api/v3/exchangeInfo?symbol={SYMBOL}"
    _check(url)
    status, payload = http_get(url)
    if status != 200 or not isinstance(payload, dict):
        raise MarketDataError(f"exchangeInfo request failed: HTTP {status}")
    try:
        symbols = payload["symbols"]
        filters = {f["filterType"]: f for f in symbols[0]["filters"]}
    except (KeyError, IndexError, TypeError) as exc:
        raise MarketDataError(f"malformed exchangeInfo: {exc}") from None

    price_f = filters.get("PRICE_FILTER", {})
    lot_f = filters.get("LOT_SIZE", {})
    notional_f = filters.get("NOTIONAL", filters.get("MIN_NOTIONAL", {}))

    return ExchangeFilters(
        tick_size=quote(price_f.get("tickSize", "0.01")),
        step_size=base(lot_f.get("stepSize", "0.00000001")),
        min_qty=base(lot_f.get("minQty", "0.00000001")),
        min_notional=quote(notional_f.get("minNotional", "5.00")),
    )


def fetch_last_price(http_get: HttpGet = _default_get) -> Decimal:
    url = f"{BASE_URL}/api/v3/ticker/price?symbol={SYMBOL}"
    _check(url)
    status, payload = http_get(url)
    if status != 200 or not isinstance(payload, dict) or "price" not in payload:
        raise MarketDataError(f"ticker request failed: HTTP {status}")
    return quote(payload["price"])
