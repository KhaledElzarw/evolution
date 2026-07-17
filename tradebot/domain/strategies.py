"""Strategy plugin SDK contract (domain layer — pure data).

A strategy plugin receives ONLY immutable, JSON-compatible context and returns
a schema-validated decision. It never receives database handles, repositories,
other wallets' state, environment variables, filesystem paths, HTTP clients,
credentials, or process handles (closes A05 boundary at the type level).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol

from .ledger import Side
from .market import MarketSnapshot

SDK_SCHEMA_VERSION = "plugin-sdk-v1"


@dataclass(frozen=True, slots=True)
class StrategyMetadata:
    strategy_id: str
    strategy_version_id: str
    name: str
    family: str
    origin: str  # builtin | novel | mutation | dark_horse
    required_intervals: tuple[str, ...]
    min_warmup_candles: int
    supported_symbol: str = "BTCUSDT"


@dataclass(frozen=True, slots=True)
class WalletView:
    """The plugin's read-only view of ITS OWN wallet — nothing else."""

    quote_cash: Decimal
    base_qty: Decimal
    avg_cost: Decimal


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Immutable per-tick input fanned identically to every strategy."""

    snapshot: MarketSnapshot
    wallet: WalletView
    candles: tuple[MarketSnapshot, ...] = ()  # trailing closed candles, oldest first


@dataclass(frozen=True, slots=True)
class IntentSpec:
    """A plugin's proposed order. Core execution owns final validation."""

    side: Side
    order_type: str  # "MARKET" | "LIMIT"
    quantity: Decimal
    limit_price: Decimal | None = None
    reason_code: str = ""


@dataclass(frozen=True, slots=True)
class StrategyDecision:
    intents: tuple[IntentSpec, ...] = ()
    state: dict[str, Any] = field(default_factory=dict)  # opaque, JSON-compatible


class StrategyPlugin(Protocol):
    def metadata(self) -> StrategyMetadata: ...

    def initialize(self) -> dict[str, Any]: ...

    def on_market_snapshot(
        self, context: StrategyContext, state: dict[str, Any]
    ) -> StrategyDecision: ...
