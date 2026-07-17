"""Immutable market snapshots shared identically across all wallets."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal

from .money import price


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """One BTCUSDT interval, fetched once and fanned to every wallet.

    Frozen + hashable so a single instance is shared; no wallet can mutate what
    another sees. ``is_closed`` distinguishes a completed candle (eligible for
    closed-candle strategies) from an in-progress one.
    """

    snapshot_id: str
    source: str
    symbol: str
    interval: str
    open_time_ms: int
    close_time_ms: int
    is_closed: bool
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    retrieved_at_ms: int
    source_time_ms: int

    @property
    def mark_price(self) -> Decimal:
        return price(self.close)

    @property
    def content_hash(self) -> str:
        payload = "|".join(
            str(x)
            for x in (
                self.source,
                self.symbol,
                self.interval,
                self.open_time_ms,
                self.close_time_ms,
                self.is_closed,
                self.open,
                self.high,
                self.low,
                self.close,
                self.volume,
            )
        )
        return hashlib.sha256(payload.encode()).hexdigest()
