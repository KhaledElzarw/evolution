"""Deterministic weekly elimination, replacement, and allocation rules.

Pure functions over evaluations — no I/O, no randomness. This is the numerical
heart of the product spec; the transactional promotion that *applies* the
result lives in promotion.py.

Rules encoded (product rules 8-14):

* Ranking uses ``weekly_net_profit_usdt`` only. No risk-adjusted terms.
* Eliminate every losing active strategy (< 0).
* Eliminate every active or shadow strategy with zero fills (Dark Horse exempt).
* If any active strategy is eliminated -> replacement_count = eliminated count.
* If all 12 traded and none lost -> retire the bottom six by profit.
* novel_count = ceil(replacement_count / 2); mutation_count = floor(/2).
* Mutation parents = top surviving performers (up to 3); an eliminated
  strategy may never be a parent; no valid parent -> mutation slots become novel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from ..domain.evaluations import WalletEvaluation

ACTIVE_TARGET = 12
NO_TRADE_ELIMINATION = "no_trade"
LOSS_ELIMINATION = "loss"
RETIRE_BOTTOM_SIX = "bottom_six"


@dataclass(frozen=True, slots=True)
class EliminationDecision:
    wallet_id: str
    strategy_version_id: str
    code_hash: str
    reason: str
    banned: bool  # loss and no-trade eliminations ban the code hash


@dataclass(frozen=True, slots=True)
class ReplacementPlan:
    eliminations: tuple[EliminationDecision, ...]
    replacement_count: int
    novel_count: int
    mutation_count: int
    parent_version_ids: tuple[str, ...]
    ranking: tuple[str, ...]  # active wallet_ids, most profitable first


def rank_active(evaluations: list[WalletEvaluation]) -> list[WalletEvaluation]:
    """Rank active wallets by profit only. Ties broken by wallet_id for
    determinism (a stable, value-independent tiebreak — not a ranking factor)."""

    return sorted(
        evaluations,
        key=lambda e: (-e.weekly_net_profit_usdt, e.wallet_id),
    )


def _active(evaluations: list[WalletEvaluation]) -> list[WalletEvaluation]:
    return [e for e in evaluations if e.kind == "active"]


def plan_replacements(evaluations: list[WalletEvaluation]) -> ReplacementPlan:
    """Compute the full weekly elimination + replacement allocation."""

    active = _active(evaluations)
    ranked = rank_active(active)

    eliminations: list[EliminationDecision] = []
    eliminated_ids: set[str] = set()

    # Rule 10/11: losing OR zero-fill active strategies are eliminated & banned.
    for ev in active:
        reasons = []
        if ev.is_losing:
            reasons.append(LOSS_ELIMINATION)
        if not ev.traded:
            reasons.append(NO_TRADE_ELIMINATION)
        if reasons:
            eliminations.append(EliminationDecision(
                wallet_id=ev.wallet_id,
                strategy_version_id=ev.strategy_version_id,
                code_hash=ev.code_hash,
                reason="+".join(reasons),
                banned=True,
            ))
            eliminated_ids.add(ev.wallet_id)

    if eliminated_ids:
        replacement_count = len(eliminated_ids)
    else:
        # Rule 12: none lost and all traded -> retire the bottom six by profit.
        # (This branch only reaches here when no eliminations fired, which given
        # the rules above means every active strategy traded and none lost.)
        bottom_six = ranked[-6:]
        for ev in bottom_six:
            eliminations.append(EliminationDecision(
                wallet_id=ev.wallet_id,
                strategy_version_id=ev.strategy_version_id,
                code_hash=ev.code_hash,
                reason=RETIRE_BOTTOM_SIX,
                banned=False,  # retirement is not a ban
            ))
            eliminated_ids.add(ev.wallet_id)
        replacement_count = 6

    novel_count = math.ceil(replacement_count / 2)
    mutation_count = math.floor(replacement_count / 2)

    # Rule 13: mutation parents are surviving top performers (up to 3), never an
    # eliminated strategy. No survivor -> mutation slots convert to novel.
    survivors = [e for e in ranked if e.wallet_id not in eliminated_ids]
    parents = tuple(e.strategy_version_id for e in survivors[:3])
    if not parents and mutation_count > 0:
        novel_count += mutation_count
        mutation_count = 0

    return ReplacementPlan(
        eliminations=tuple(eliminations),
        replacement_count=replacement_count,
        novel_count=novel_count,
        mutation_count=mutation_count,
        parent_version_ids=parents,
        ranking=tuple(e.wallet_id for e in ranked),
    )


def eliminate_zero_fill_shadows(
    evaluations: list[WalletEvaluation],
) -> list[EliminationDecision]:
    """Rule 11: shadow strategies with zero weekly fills are eliminated."""

    out = []
    for ev in evaluations:
        if ev.kind == "shadow" and not ev.traded:
            out.append(EliminationDecision(
                wallet_id=ev.wallet_id,
                strategy_version_id=ev.strategy_version_id,
                code_hash=ev.code_hash,
                reason=NO_TRADE_ELIMINATION,
                banned=True,
            ))
    return out


@dataclass(slots=True)
class BanRegistry:
    """Permanent ban of losing / no-trade code hashes and fingerprints.

    A banned hash or fingerprint may never be activated or reused (rule 10/20).
    """

    _hashes: set[str] = field(default_factory=set)
    _fingerprints: set[str] = field(default_factory=set)

    def ban(self, code_hash: str, fingerprint: str | None = None) -> None:
        self._hashes.add(code_hash)
        if fingerprint:
            self._fingerprints.add(fingerprint)

    def is_banned(self, code_hash: str, fingerprint: str | None = None) -> bool:
        if code_hash in self._hashes:
            return True
        return bool(fingerprint and fingerprint in self._fingerprints)
