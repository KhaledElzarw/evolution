"""Deterministic elimination + replacement-allocation rule tests (Phase 9)."""

from decimal import Decimal

from tradebot.application.evolution import (
    BanRegistry,
    eliminate_zero_fill_shadows,
    plan_replacements,
    rank_active,
)
from tradebot.domain.evaluations import WalletEvaluation


def ev(wid, profit, *, fills=5, kind="active", start="10000.00"):
    start_d = Decimal(start)
    return WalletEvaluation(
        wallet_id=wid,
        strategy_version_id=f"sv-{wid}",
        code_hash=f"hash-{wid}",
        structural_fingerprint=f"fp-{wid}",
        kind=kind,
        evaluation_start_equity=start_d,
        pre_liquidation_equity=start_d + Decimal(profit),
        liquidation_adjusted_equity=start_d + Decimal(profit),
        fill_count=fills,
        completed_round_trip_count=max(0, fills // 2),
    )


def make_active(profits, fills=None):
    fills = fills or [5] * len(profits)
    return [ev(f"w{i:02d}", p, fills=f) for i, (p, f) in enumerate(zip(profits, fills))]


def test_weekly_net_profit_is_fixed_point_and_canonical():
    e = ev("w1", "123.456")  # excess precision
    assert e.weekly_net_profit_usdt == Decimal("123.46")  # quantized to cents
    assert e.is_losing is False


def test_ranking_is_profit_only_with_stable_tiebreak():
    active = make_active(["100", "300", "300", "-50"])
    ranked = [e.wallet_id for e in rank_active(active)]
    # 300s ranked above 100 above -50; equal profits tie-break by wallet_id.
    assert ranked == ["w01", "w02", "w00", "w03"]


def test_all_losers_eliminated():
    active = make_active(["-1", "-2", "50", "60"] + ["10"] * 8)
    plan = plan_replacements(active)
    reasons = {e.wallet_id: e.reason for e in plan.eliminations}
    assert reasons["w00"] == "loss"
    assert reasons["w01"] == "loss"
    assert plan.replacement_count == 2


def test_no_trade_active_eliminated_even_if_profitable():
    # A wallet with 0 fills but positive marked profit is still eliminated.
    active = make_active(["100"] + ["10"] * 11, fills=[0] + [5] * 11)
    plan = plan_replacements(active)
    elim = {e.wallet_id: e.reason for e in plan.eliminations}
    assert elim == {"w00": "no_trade"}
    assert plan.replacement_count == 1


def test_loss_and_no_trade_combined_reason():
    active = make_active(["-5"] + ["10"] * 11, fills=[0] + [5] * 11)
    plan = plan_replacements(active)
    assert plan.eliminations[0].reason == "loss+no_trade"


def test_no_losers_all_traded_retires_bottom_six():
    profits = ["10", "20", "30", "40", "50", "60", "70", "80", "90", "100", "110", "120"]
    active = make_active(profits)
    plan = plan_replacements(active)
    assert plan.replacement_count == 6
    retired = {e.wallet_id for e in plan.eliminations}
    assert retired == {"w00", "w01", "w02", "w03", "w04", "w05"}  # lowest six
    assert all(e.reason == "bottom_six" and not e.banned for e in plan.eliminations)


def test_allocation_ceil_novel_floor_mutation():
    # Cases with >=1 surviving parent: pure ceil(novel)/floor(mutation) split.
    cases = {1: (1, 0), 2: (1, 1), 5: (3, 2), 6: (3, 3), 7: (4, 3), 11: (6, 5)}
    for n_elim, (exp_novel, exp_mut) in cases.items():
        profits = ["-1"] * n_elim + ["50"] * (12 - n_elim)
        plan = plan_replacements(make_active(profits))
        assert plan.replacement_count == n_elim, n_elim
        assert (plan.novel_count, plan.mutation_count) == (exp_novel, exp_mut), n_elim


def test_twelve_eliminations_all_novel_no_parents():
    # All 12 lose -> 12 replacements, no survivors -> mutation slots become novel.
    plan = plan_replacements(make_active(["-1"] * 12))
    assert plan.replacement_count == 12
    assert (plan.novel_count, plan.mutation_count) == (12, 0)


def test_odd_replacement_allocation_five():
    plan = plan_replacements(make_active(["-1"] * 5 + ["50"] * 7))
    assert (plan.novel_count, plan.mutation_count) == (3, 2)  # ceil/floor of 5


def test_no_surviving_parent_converts_mutation_to_novel():
    # All 12 lose -> 12 eliminated, no survivors -> all replacements become novel.
    plan = plan_replacements(make_active(["-1"] * 12))
    assert plan.replacement_count == 12
    assert plan.parent_version_ids == ()
    assert plan.novel_count == 12 and plan.mutation_count == 0


def test_parents_are_top_survivors_only():
    profits = ["-5", "-5", "10", "20", "300", "200", "100"] + ["30"] * 5
    plan = plan_replacements(make_active(profits))
    # Two losers eliminated; parents are the 3 most profitable survivors.
    assert plan.parent_version_ids == ("sv-w04", "sv-w05", "sv-w06")
    assert "sv-w00" not in plan.parent_version_ids


def test_shadow_zero_fill_elimination():
    shadows = [ev("s0", "5", fills=0, kind="shadow"),
               ev("s1", "-5", fills=3, kind="shadow"),
               ev("s2", "5", fills=1, kind="shadow")]
    elim = eliminate_zero_fill_shadows(shadows)
    assert [e.wallet_id for e in elim] == ["s0"]
    assert elim[0].banned is True


def test_ban_registry_blocks_reuse():
    bans = BanRegistry()
    bans.ban("h1", "fp1")
    assert bans.is_banned("h1")
    assert bans.is_banned("other", "fp1")  # fingerprint match
    assert not bans.is_banned("h2", "fp2")
