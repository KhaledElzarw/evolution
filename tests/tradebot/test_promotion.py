"""Atomic promotion, roll-forward, and post-commit invariant tests (Phase 9)."""

import datetime as dt
from decimal import Decimal

import pytest

from tradebot.application.evolution import BanRegistry, plan_replacements
from tradebot.application.portfolio import seed_portfolio
from tradebot.application.promotion import (
    Candidate,
    ListCandidateProvider,
    PromotionError,
    promote,
)
from tradebot.domain.evaluations import WalletEvaluation
from tradebot.strategies.builtin import BUILTIN_STRATEGIES

NOW = dt.datetime(2026, 7, 20)
NAMES = [c().metadata().name for c in BUILTIN_STRATEGIES]


def fresh_portfolio():
    counter = iter(range(10_000))
    return seed_portfolio(NAMES, now=NOW,
                          id_factory=lambda h: f"{h}-{next(counter):05d}")


def evals_for(portfolio, profits, fills=None):
    fills = fills or [5] * 12
    out = []
    for slot, profit, fc in zip(portfolio.active, profits, fills):
        start = Decimal("10000.00")
        out.append(WalletEvaluation(
            wallet_id=slot.wallet.wallet_id,
            strategy_version_id=slot.strategy_version_id,
            code_hash=f"hash-{slot.wallet.wallet_id}",
            structural_fingerprint=f"fp-{slot.strategy_name}",
            kind="active",
            evaluation_start_equity=start,
            pre_liquidation_equity=start + Decimal(profit),
            liquidation_adjusted_equity=start + Decimal(profit),
            fill_count=fc,
            completed_round_trip_count=fc // 2,
        ))
    return out


def candidates(n, category, valid=True, prefix="c"):
    return [Candidate(f"Gen{prefix}{i}", f"sv-{prefix}{i}", f"h-{prefix}{i}",
                      f"fp-{prefix}{i}", category, valid) for i in range(n)]


def cid():
    counter = iter(range(10_000))
    return lambda h: f"{h}-{next(counter):05d}"


def test_promotion_replaces_losers_keeps_twelve_active():
    p = fresh_portfolio()
    plan = plan_replacements(evals_for(p, ["-1", "-2"] + ["50"] * 10))
    provider = ListCandidateProvider(novel=candidates(1, "novel"),
                                     mutation=candidates(1, "mutation"))
    bans = BanRegistry()
    result = promote(p, plan, provider, bans, now=NOW, id_factory=cid())
    assert len(p.active) == 12
    assert len(result.activated) == 2
    assert set(result.banned_hashes)  # losers banned
    # New wallets start at exactly 10,000.00 and zero BTC.
    new_ids = {wid for wid, _ in result.activated}
    for slot in p.active:
        if slot.wallet.wallet_id in new_ids:
            assert slot.wallet.quote_cash == Decimal("10000.00")
            assert slot.wallet.base_qty == 0


def test_promotion_bans_prevent_reuse_of_eliminated_hash():
    p = fresh_portfolio()
    evals = evals_for(p, ["-1"] + ["50"] * 11)
    plan = plan_replacements(evals)
    banned_hash = plan.eliminations[0].code_hash
    # Provider offers the banned hash first, then a clean candidate.
    provider = ListCandidateProvider(novel=[
        Candidate("Reused", "sv-x", banned_hash, "fp-x", "novel", True),
        *candidates(1, "novel", prefix="ok"),
    ])
    bans = BanRegistry()
    result = promote(p, plan, provider, bans, now=NOW, id_factory=cid())
    assert "sv-x" in result.quarantined  # banned candidate skipped
    assert result.activated[0][1] == "sv-ok0"


def test_promotion_rolls_forward_past_invalid_candidate():
    p = fresh_portfolio()
    plan = plan_replacements(evals_for(p, ["-1"] + ["50"] * 11))
    provider = ListCandidateProvider(novel=[
        Candidate("Bad", "sv-bad", "h-bad", "fp-bad", "novel", technically_valid=False),
        *candidates(1, "novel", prefix="good"),
    ])
    result = promote(p, plan, provider, BanRegistry(), now=NOW, id_factory=cid())
    assert "sv-bad" in result.quarantined
    assert result.activated[0][1] == "sv-good0"


def test_promotion_fails_atomically_on_candidate_shortage():
    p = fresh_portfolio()
    before = [s.wallet.wallet_id for s in p.active]
    plan = plan_replacements(evals_for(p, ["-1", "-2", "-3"] + ["50"] * 9))
    # Only one novel candidate for a plan needing novel=2, mutation=1.
    provider = ListCandidateProvider(novel=candidates(1, "novel"))
    with pytest.raises(PromotionError):
        promote(p, plan, provider, BanRegistry(), now=NOW, id_factory=cid())
    # Rollback: active roster unchanged.
    assert [s.wallet.wallet_id for s in p.active] == before


def test_promotion_archives_retirees_via_sink():
    p = fresh_portfolio()
    plan = plan_replacements(evals_for(p, ["-1"] + ["50"] * 11))
    archived = []
    provider = ListCandidateProvider(novel=candidates(1, "novel"))
    promote(p, plan, provider, BanRegistry(), now=NOW, id_factory=cid(),
            archive_sink=lambda slot, reason: archived.append((slot.wallet.wallet_id, reason)))
    assert len(archived) == 1
    assert archived[0][1] == "loss"


def test_promotion_preserves_dark_horse():
    p = fresh_portfolio()
    dh_cash = p.dark_horse.wallet.quote_cash
    plan = plan_replacements(evals_for(p, ["-1"] * 12))
    provider = ListCandidateProvider(novel=candidates(12, "novel"))
    promote(p, plan, provider, BanRegistry(), now=NOW, id_factory=cid())
    assert p.dark_horse.wallet.quote_cash == dh_cash  # never reset
    assert len(p.active) == 12


def test_bottom_six_promotion_when_no_losers():
    p = fresh_portfolio()
    profits = [str(10 * (i + 1)) for i in range(12)]
    plan = plan_replacements(evals_for(p, profits))
    provider = ListCandidateProvider(novel=candidates(3, "novel"),
                                     mutation=candidates(3, "mutation"))
    result = promote(p, plan, provider, BanRegistry(), now=NOW, id_factory=cid())
    assert len(result.activated) == 6
    assert len(p.active) == 12
    assert not result.banned_hashes  # retirement is not a ban


# ---- Phase-13 verifier regressions -----------------------------------------

def _candidate(vid, code_hash, fingerprint, category="novel", valid=True):
    return Candidate(
        strategy_name="Cand", strategy_version_id=vid, code_hash=code_hash,
        structural_fingerprint=fingerprint, category=category,
        technically_valid=valid,
    )


def test_eliminated_fingerprint_is_banned_not_just_the_hash():
    """D1: `bans.ban(elim.code_hash)` never populated _fingerprints, so the
    fingerprint arm of is_banned was dead code. An eliminated loser could
    return under a new hash with a comment changed."""

    portfolio = fresh_portfolio()
    profits = [Decimal("-100")] + [Decimal("100")] * 11
    plan = plan_replacements(evals_for(portfolio, profits))
    loser = plan.eliminations[0]
    assert loser.structural_fingerprint, "fingerprint must reach the decision"

    bans = BanRegistry()
    # Offer a structurally IDENTICAL clone under a brand-new hash.
    clone = _candidate("v-clone", "brand-new-hash", loser.structural_fingerprint)
    fallback = _candidate("v-ok", "ok-hash", "fp-unique")
    provider = ListCandidateProvider(novel=[clone, fallback],
                                     mutation=[_candidate("v-mut", "mut-hash",
                                                          "fp-mut", "mutation")])
    result = promote(portfolio, plan, provider, bans, now=NOW,
                     id_factory=lambda h: f"new-{h}")

    assert "v-clone" in result.quarantined, "reskinned clone must be rejected"
    assert "v-clone" not in [v for _, v in result.activated]
    assert bans.is_banned("anything", loser.structural_fingerprint) is True


def test_failed_invariant_leaves_portfolio_untouched():
    """D2: the roster was swapped BEFORE invariants were asserted, so a
    failure left e.g. 11 active with no rollback, contradicting the
    all-or-nothing contract."""

    portfolio = fresh_portfolio()
    profits = [Decimal("-100"), Decimal("-100")] + [Decimal("100")] * 10
    plan = plan_replacements(evals_for(portfolio, profits))
    # Force a desync: 2 eliminations but only 1 replacement will be built.
    broken = type(plan)(
        eliminations=plan.eliminations, replacement_count=1,
        novel_count=1, mutation_count=0,
        parent_version_ids=plan.parent_version_ids, ranking=plan.ranking,
    )
    before_ids = [s.wallet.wallet_id for s in portfolio.active]
    bans = BanRegistry()
    provider = ListCandidateProvider(
        novel=[_candidate("v-new", "h-new", "fp-new")], mutation=[])

    with pytest.raises(PromotionError, match="active count != 12"):
        promote(portfolio, broken, provider, bans, now=NOW,
                id_factory=lambda h: f"new-{h}")

    after_ids = [s.wallet.wallet_id for s in portfolio.active]
    assert after_ids == before_ids, "portfolio must be untouched on failure"
    assert len(portfolio.active) == 12


def test_aborted_batch_leaves_no_bans_behind():
    """D4: bans were applied before candidate acquisition could abort, so an
    aborted batch still mutated the registry."""

    portfolio = fresh_portfolio()
    profits = [Decimal("-100")] + [Decimal("100")] * 11
    plan = plan_replacements(evals_for(portfolio, profits))
    loser = plan.eliminations[0]
    bans = BanRegistry()
    # No candidates at all -> the batch must abort.
    provider = ListCandidateProvider(novel=[], mutation=[])

    with pytest.raises(PromotionError, match="no technically valid"):
        promote(portfolio, plan, provider, bans, now=NOW,
                id_factory=lambda h: f"new-{h}")

    assert bans.is_banned(loser.code_hash) is False, "aborted batch left bans"
    assert len(portfolio.active) == 12


def test_successful_promotion_does_apply_bans():
    """The staging must not lose the bans on the success path."""

    portfolio = fresh_portfolio()
    profits = [Decimal("-100")] + [Decimal("100")] * 11
    plan = plan_replacements(evals_for(portfolio, profits))
    loser = plan.eliminations[0]
    bans = BanRegistry()
    provider = ListCandidateProvider(
        novel=[_candidate("v-new", "h-new", "fp-new")], mutation=[])

    promote(portfolio, plan, provider, bans, now=NOW,
            id_factory=lambda h: f"new-{h}")
    assert bans.is_banned(loser.code_hash) is True
    assert bans.is_banned("x", loser.structural_fingerprint) is True
    assert len(portfolio.active) == 12
