"""Daily/weekly learning tests: schema guards, idempotency, degradation (Phase 8)."""

import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from tradebot.application.lessons import (
    DailyFacts,
    LessonService,
    build_ranking_rows,
    daily_idempotency_key,
    utc_day_of,
    utc_week_of,
    weekly_idempotency_key,
)
from tradebot.domain.evaluations import RANKING_FORMULA_VERSION, WalletEvaluation
from tradebot.domain.lessons import (
    DAILY_COMMITTEE_ROLES,
    WEEKLY_COMMITTEE_ROLES,
    Claim,
    DailyLesson,
    RankingRow,
    WeeklyReport,
)
from tradebot.reports.renderer import (
    render_daily,
    render_weekly,
    write_daily,
    write_weekly,
)

FACTS = DailyFacts(
    date="2026-07-16", wallet_id="w1", strategy_version_id="sv1",
    starting_equity=Decimal("10000.00"), ending_marked_equity=Decimal("10120.00"),
    net_daily_profit=Decimal("120.00"), fees=Decimal("3.10"),
    slippage_cost=Decimal("1.20"), fill_count=4, round_trips=2,
    trade_ids=("t1", "t2"), snapshot_ids=("s1",), external_snapshot_ids=("e1",),
)


def good_lesson(**over):
    base = dict(
        date="2026-07-16", wallet_id="w1", strategy_version_id="sv1",
        starting_equity=Decimal("10000.00"),
        ending_marked_equity=Decimal("10120.00"),
        net_daily_profit=Decimal("120.00"), fees=Decimal("3.10"),
        slippage_cost=Decimal("1.20"), fill_count=4, round_trips=2,
        market_regime="range",
        observation=Claim(statement="Mean reversion worked", evidence_ids=["t1"]),
        hypothesis=Claim(statement="Tighter bands help", is_hypothesis=True),
        confidence=Decimal("0.6"),
    )
    base.update(over)
    return DailyLesson(**base)


# ---- committee roles --------------------------------------------------------

def test_committee_roles_defined():
    assert len(DAILY_COMMITTEE_ROLES) == 7
    assert len(WEEKLY_COMMITTEE_ROLES) == 7
    assert "lesson_synthesizer" in DAILY_COMMITTEE_ROLES
    assert "final_evolution_planner" in WEEKLY_COMMITTEE_ROLES


# ---- A20: no unsupported claims --------------------------------------------

def test_claim_without_evidence_must_be_hypothesis():
    with pytest.raises(ValidationError, match="is_hypothesis"):
        Claim(statement="Grid strategies always win")
    # Labelled as a hypothesis -> allowed.
    assert Claim(statement="Maybe", is_hypothesis=True).is_hypothesis
    # Evidence-backed -> allowed without the label.
    assert Claim(statement="Fact", evidence_ids=["e1"]).evidence_ids == ["e1"]


def test_hypothesis_field_must_be_labelled():
    with pytest.raises(ValidationError, match="must have is_hypothesis=True"):
        good_lesson(hypothesis=Claim(statement="x", evidence_ids=["e1"]))


def test_bad_schema_version_rejected():
    with pytest.raises(ValidationError, match="unsupported daily schema"):
        good_lesson(schema_version="daily-lesson-v99")


# ---- committee cannot alter deterministic outcomes -------------------------

def _row(wid, profit, rank, fills=3, eliminated=False, reason=""):
    return RankingRow(wallet_id=wid, strategy_version_id=f"{wid}-v1",
                      weekly_net_profit_usdt=Decimal(profit), rank=rank,
                      fill_count=fills, eliminated=eliminated,
                      elimination_reason=reason)


def weekly(**over):
    base = dict(
        evaluation_window="2026-W29", cutoff_snapshot_id="snap-1",
        ranking_formula_version=RANKING_FORMULA_VERSION,
        active_ranking=[_row("a", "100", 1), _row("b", "50", 2)],
    )
    base.update(over)
    return WeeklyReport(**base)


def test_committee_cannot_reorder_ranking():
    with pytest.raises(ValidationError, match="ordered by profit descending"):
        weekly(active_ranking=[_row("a", "50", 1), _row("b", "100", 2)])


def test_ranks_must_be_contiguous():
    with pytest.raises(ValidationError, match="contiguous"):
        weekly(active_ranking=[_row("a", "100", 1), _row("b", "50", 5)])


def test_committee_cannot_preserve_a_losing_incumbent():
    with pytest.raises(ValidationError, match="not marked eliminated"):
        weekly(active_ranking=[_row("a", "100", 1), _row("b", "-50", 2)])
    # Correctly eliminated -> valid.
    weekly(active_ranking=[_row("a", "100", 1),
                           _row("b", "-50", 2, eliminated=True, reason="loss")])


def test_committee_cannot_preserve_a_zero_fill_incumbent():
    with pytest.raises(ValidationError, match="zero-fill"):
        weekly(active_ranking=[_row("a", "100", 1), _row("b", "50", 2, fills=0)])


# ---- deterministic figures are engine-owned --------------------------------

def test_model_cannot_alter_deterministic_figures():
    """A model that returns inflated profit has it overwritten by engine facts."""

    def lying_analyst(facts):
        return good_lesson(net_daily_profit=Decimal("999999.00"),
                           fill_count=1000)

    lesson, cached = LessonService().generate_daily(FACTS, lying_analyst)
    assert lesson.net_daily_profit == Decimal("120.00")  # engine value wins
    assert lesson.fill_count == 4
    assert cached is False


def test_ranking_rows_are_profit_ordered_from_evaluations():
    evals = [
        _eval("w1", Decimal("10")), _eval("w2", Decimal("300")),
        _eval("w3", Decimal("-50")),
    ]
    rows = build_ranking_rows(evals, {"w3": "loss"})
    assert [r.wallet_id for r in rows] == ["w2", "w1", "w3"]
    assert [r.rank for r in rows] == [1, 2, 3]
    assert rows[2].eliminated is True


def _eval(wid, profit, fills=3, kind="active"):
    return WalletEvaluation(
        wallet_id=wid, strategy_version_id=f"{wid}-v1", code_hash=f"{wid}-h",
        structural_fingerprint=f"{wid}-fp", kind=kind,
        evaluation_start_equity=Decimal("10000.00"),
        pre_liquidation_equity=Decimal("10000.00") + profit,
        liquidation_adjusted_equity=Decimal("10000.00") + profit,
        fill_count=fills, completed_round_trip_count=1,
    )


# ---- idempotency ------------------------------------------------------------

def test_daily_generation_is_idempotent():
    calls = []

    def analyst(facts):
        calls.append(facts.wallet_id)
        return good_lesson()

    svc = LessonService()
    first, cached1 = svc.generate_daily(FACTS, analyst)
    second, cached2 = svc.generate_daily(FACTS, analyst)
    assert cached1 is False and cached2 is True
    assert len(calls) == 1  # model called exactly once for the window
    assert first == second


def test_weekly_generation_is_idempotent():
    calls = []

    def synth(rows, shadow_rows):
        calls.append(1)
        return weekly()

    svc = LessonService()
    evals = [_eval("a", Decimal("100")), _eval("b", Decimal("50"))]
    r1, c1 = svc.generate_weekly("2026-W29", "snap-1", evals, {}, synth)
    r2, c2 = svc.generate_weekly("2026-W29", "snap-1", evals, {}, synth)
    assert c1 is False and c2 is True
    assert len(calls) == 1
    assert r1 == r2


def test_idempotency_keys_and_utc_windows():
    assert daily_idempotency_key("2026-07-16", "w1") == "daily_lesson:2026-07-16:w1"
    assert weekly_idempotency_key("2026-W29") == "weekly_report:2026-W29"
    assert utc_day_of(dt.datetime(2026, 7, 16, 23, 59)) == "2026-07-16"
    assert utc_week_of(dt.datetime(2026, 7, 16)) == "2026-W29"


# ---- degraded output --------------------------------------------------------

def test_model_failure_produces_explicit_degraded_lesson():
    def broken(facts):
        raise ConnectionError("llm down")

    lesson, _ = LessonService().generate_daily(FACTS, broken)
    assert lesson.degraded is True
    assert "ConnectionError" in lesson.degraded_reason
    # Deterministic figures survive; analysis is absent, not invented.
    assert lesson.net_daily_profit == Decimal("120.00")
    assert lesson.observation.is_hypothesis is True
    assert lesson.confidence == Decimal("0")


def test_model_returning_none_degrades():
    lesson, _ = LessonService().generate_daily(FACTS, lambda f: None)
    assert lesson.degraded is True
    assert "no valid lesson" in lesson.degraded_reason


def test_weekly_model_failure_still_reports_deterministic_ranking():
    def broken(rows, shadow_rows):
        raise TimeoutError("slow")

    evals = [_eval("a", Decimal("100")), _eval("b", Decimal("-50"))]
    report, _ = LessonService().generate_weekly(
        "2026-W29", "snap-1", evals, {"b": "loss"}, broken)
    assert report.degraded is True
    assert "TimeoutError" in report.degraded_reason
    # Ranking is still complete and correct without the model.
    assert [r.wallet_id for r in report.active_ranking] == ["a", "b"]
    assert report.ranking_formula_version == RANKING_FORMULA_VERSION


def test_weekly_committee_ranking_is_overridden_by_engine():
    """Even if the model returns a different ranking, the engine's wins."""

    def sneaky(rows, shadow_rows):
        return weekly(active_ranking=[_row("zzz", "9999", 1)])

    evals = [_eval("a", Decimal("100")), _eval("b", Decimal("50"))]
    report, _ = LessonService().generate_weekly(
        "2026-W29", "snap-1", evals, {}, sneaky)
    assert [r.wallet_id for r in report.active_ranking] == ["a", "b"]


# ---- rendering --------------------------------------------------------------

def test_render_daily_includes_figures_and_evidence():
    md = render_daily(good_lesson(supporting_trade_ids=["t1"]))
    assert "# Daily Lesson — 2026-07-16 — w1" in md
    assert "| Net daily profit | 120.00 |" in md
    assert "[evidence: t1]" in md
    assert "*(hypothesis)*" in md  # hypothesis clearly labelled


def test_render_daily_marks_degraded():
    from tradebot.application.lessons import degraded_daily_lesson
    md = render_daily(degraded_daily_lesson(FACTS, "llm offline"))
    assert "**DEGRADED:** llm offline" in md


def test_render_weekly_separates_shadow_capital():
    report = weekly(shadow_ranking=[_row("s1", "10", 1)],
                    dark_horse_summary="Accumulated on macro strength.")
    md = render_weekly(report)
    assert "## Active ranking" in md
    assert "virtual capital — not part of active totals" in md
    assert "Dark Horse" in md


def test_reports_written_atomically_to_derived_paths(tmp_path):
    lesson = good_lesson()
    p = write_daily(tmp_path, lesson)
    assert p == tmp_path / "daily" / "2026-07-16.md"
    assert "Daily Lesson" in p.read_text(encoding="utf-8")

    wp = write_weekly(tmp_path, weekly())
    assert wp == tmp_path / "weekly" / "2026-W29.md"
    assert "Weekly Report" in wp.read_text(encoding="utf-8")
    # No temp files left behind.
    assert not list(tmp_path.rglob("*.tmp"))


def test_rerendering_is_stable_and_overwrites_cleanly(tmp_path):
    lesson = good_lesson()
    first = write_daily(tmp_path, lesson).read_text(encoding="utf-8")
    second = write_daily(tmp_path, lesson).read_text(encoding="utf-8")
    assert first == second
    assert len(list((tmp_path / "daily").iterdir())) == 1
