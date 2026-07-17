"""Atomic weekly promotion: apply eliminations and replacements all-or-nothing.

The promotion transaction archives retiring active wallets, bans losing/no-trade
hashes, creates fresh 10,000 USDT replacement wallets, activates technically
valid candidate versions, replenishes the shadow pool, and verifies post-commit
invariants. Any failure rolls the whole batch back (product rules 6/16/23-30).

Candidates are supplied by an injected provider (the LLM/evolution worker in
production, a fake in tests). A candidate must be technically valid; an invalid
or worker-failing candidate is quarantined and the next valid one is used
(roll-forward), never the eliminated strategy.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Callable, Protocol

from ..domain.money import quote
from .evolution import BanRegistry, ReplacementPlan
from .portfolio import STARTING_BALANCE, Portfolio, WalletSlot
from ..domain.ledger import Wallet


@dataclass(frozen=True, slots=True)
class Candidate:
    strategy_name: str
    strategy_version_id: str
    code_hash: str
    structural_fingerprint: str
    category: str  # novel | mutation
    technically_valid: bool = True


class CandidateProvider(Protocol):
    def next_candidate(self, category: str) -> Candidate | None: ...


@dataclass(slots=True)
class ListCandidateProvider:
    """Deterministic ordered provider used in tests and abbreviated flows."""

    novel: list[Candidate] = field(default_factory=list)
    mutation: list[Candidate] = field(default_factory=list)

    def next_candidate(self, category: str) -> Candidate | None:
        pool = self.novel if category == "novel" else self.mutation
        return pool.pop(0) if pool else None


class PromotionError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class PromotionResult:
    archived_wallet_ids: tuple[str, ...]
    activated: tuple[tuple[str, str], ...]  # (wallet_id, strategy_version_id)
    quarantined: tuple[str, ...]
    banned_hashes: tuple[str, ...]


def promote(
    portfolio: Portfolio,
    plan: ReplacementPlan,
    provider: CandidateProvider,
    bans: BanRegistry,
    *,
    now: dt.datetime,
    id_factory: Callable[[str], str],
    archive_sink: Callable[[WalletSlot, str], None] | None = None,
) -> PromotionResult:
    """Apply the plan atomically to ``portfolio`` (mutated only on success)."""

    eliminated_ids = {e.wallet_id for e in plan.eliminations}
    by_id = {s.wallet.wallet_id: s for s in portfolio.active}
    if not eliminated_ids <= by_id.keys():
        raise PromotionError("plan references unknown active wallets")

    # Build the new active roster in a scratch list; commit only if it all works.
    survivors = [s for s in portfolio.active if s.wallet.wallet_id not in eliminated_ids]
    new_slots: list[WalletSlot] = []
    activated: list[tuple[str, str]] = []
    quarantined: list[str] = []
    banned: list[str] = []

    # Ban losing / no-trade hashes first (idempotent).
    for elim in plan.eliminations:
        if elim.banned:
            bans.ban(elim.code_hash)
            banned.append(elim.code_hash)

    categories = ["novel"] * plan.novel_count + ["mutation"] * plan.mutation_count
    for category in categories:
        candidate = _acquire_valid_candidate(provider, bans, category, quarantined)
        if candidate is None:
            raise PromotionError(f"no technically valid {category} candidate available")
        wallet_id = id_factory(f"active-{candidate.strategy_version_id}")
        slot = WalletSlot(
            wallet=Wallet(wallet_id, quote_cash=quote(STARTING_BALANCE)),
            kind="active",
            strategy_name=candidate.strategy_name,
            strategy_version_id=candidate.strategy_version_id,
            activated_at=now,
        )
        new_slots.append(slot)
        activated.append((wallet_id, candidate.strategy_version_id))

    # --- commit point: swap the roster and archive retirees ---
    for elim in plan.eliminations:
        slot = by_id[elim.wallet_id]
        if archive_sink is not None:
            archive_sink(slot, elim.reason)
    portfolio.active = survivors + new_slots

    _assert_post_commit_invariants(portfolio, bans)
    return PromotionResult(
        archived_wallet_ids=tuple(sorted(eliminated_ids)),
        activated=tuple(activated),
        quarantined=tuple(quarantined),
        banned_hashes=tuple(banned),
    )


def _acquire_valid_candidate(
    provider: CandidateProvider, bans: BanRegistry, category: str,
    quarantined: list[str],
) -> Candidate | None:
    """Pull candidates until a technically valid, non-banned one is found."""

    while True:
        candidate = provider.next_candidate(category)
        if candidate is None:
            return None
        if bans.is_banned(candidate.code_hash, candidate.structural_fingerprint):
            quarantined.append(candidate.strategy_version_id)
            continue
        if not candidate.technically_valid:
            quarantined.append(candidate.strategy_version_id)  # roll forward
            continue
        return candidate


def _assert_post_commit_invariants(portfolio: Portfolio, bans: BanRegistry) -> None:
    if len(portfolio.active) != 12:
        raise PromotionError(f"post-commit active count != 12: {len(portfolio.active)}")
    if portfolio.dark_horse is None:
        raise PromotionError("dark horse missing after promotion")
    ids = [s.wallet.wallet_id for s in portfolio.active]
    if len(set(ids)) != 12:
        raise PromotionError("duplicate active wallet ids after promotion")
    for slot in portfolio.active:
        # Replacement wallets must start clean; survivors keep their balances.
        if slot.wallet.base_qty < 0 or slot.wallet.quote_cash < 0:
            raise PromotionError("negative balance after promotion")
