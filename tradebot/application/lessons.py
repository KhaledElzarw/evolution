"""Daily/weekly learning service.

Deterministic figures are computed by the engine and injected into the model's
prompt as facts; the model only supplies analysis. Every job carries a durable
idempotency key so re-running a completed window is a no-op that returns the
stored record rather than re-generating it.

Model failure never blocks the pipeline: it produces an explicit *degraded*
record (with the reason recorded), and active trading continues untouched.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

from ..domain.evaluations import RANKING_FORMULA_VERSION, WalletEvaluation
from ..domain.lessons import (
    Claim,
    DailyLesson,
    RankingRow,
    WeeklyReport,
)

DAILY_JOB = "daily_lesson"
WEEKLY_JOB = "weekly_report"


def daily_idempotency_key(date: str, wallet_id: str) -> str:
    return f"{DAILY_JOB}:{date}:{wallet_id}"


def weekly_idempotency_key(window: str) -> str:
    return f"{WEEKLY_JOB}:{window}"


def utc_day_of(moment: dt.datetime) -> str:
    return moment.strftime("%Y-%m-%d")


def utc_week_of(moment: dt.datetime) -> str:
    iso = moment.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


@dataclass(slots=True)
class JobStore:
    """Durable idempotency records (in-memory adapter; DB-backed in prod)."""

    _completed: dict[str, object] = field(default_factory=dict)

    def get(self, key: str) -> object | None:
        return self._completed.get(key)

    def put(self, key: str, record: object) -> None:
        self._completed[key] = record

    def is_complete(self, key: str) -> bool:
        return key in self._completed


@dataclass(frozen=True, slots=True)
class DailyFacts:
    """Engine-computed figures. The model may not alter these."""

    date: str
    wallet_id: str
    strategy_version_id: str
    starting_equity: Decimal
    ending_marked_equity: Decimal
    net_daily_profit: Decimal
    fees: Decimal
    slippage_cost: Decimal
    fill_count: int
    round_trips: int
    trade_ids: tuple[str, ...] = ()
    snapshot_ids: tuple[str, ...] = ()
    external_snapshot_ids: tuple[str, ...] = ()


def degraded_daily_lesson(facts: DailyFacts, reason: str) -> DailyLesson:
    """Explicit degraded output when the model is unavailable/invalid.

    The deterministic figures are still recorded; the analysis is explicitly
    absent rather than invented.
    """

    return DailyLesson(
        date=facts.date,
        wallet_id=facts.wallet_id,
        strategy_version_id=facts.strategy_version_id,
        starting_equity=facts.starting_equity,
        ending_marked_equity=facts.ending_marked_equity,
        net_daily_profit=facts.net_daily_profit,
        fees=facts.fees,
        slippage_cost=facts.slippage_cost,
        fill_count=facts.fill_count,
        round_trips=facts.round_trips,
        market_regime="unknown",
        observation=Claim(statement="Model analysis unavailable.",
                          is_hypothesis=True),
        hypothesis=Claim(statement="No hypothesis generated.",
                         is_hypothesis=True),
        confidence=Decimal("0"),
        supporting_trade_ids=list(facts.trade_ids),
        supporting_snapshot_ids=list(facts.snapshot_ids),
        external_data_snapshot_ids=list(facts.external_snapshot_ids),
        degraded=True,
        degraded_reason=reason,
    )


def build_ranking_rows(evaluations: list[WalletEvaluation],
                       eliminated: dict[str, str]) -> list[RankingRow]:
    """Deterministic ranking rows from evaluations — profit order only."""

    from .evolution import rank_active

    rows: list[RankingRow] = []
    for i, ev in enumerate(rank_active(evaluations), start=1):
        reason = eliminated.get(ev.wallet_id, "")
        rows.append(RankingRow(
            wallet_id=ev.wallet_id,
            strategy_version_id=ev.strategy_version_id,
            weekly_net_profit_usdt=ev.weekly_net_profit_usdt,
            rank=i,
            fill_count=ev.fill_count,
            eliminated=bool(reason),
            elimination_reason=reason,
        ))
    return rows


@dataclass(slots=True)
class LessonService:
    job_store: JobStore = field(default_factory=JobStore)

    def generate_daily(
        self, facts: DailyFacts, analyst, *, force: bool = False
    ) -> tuple[DailyLesson, bool]:
        """Return (lesson, was_cached). Idempotent per (date, wallet)."""

        key = daily_idempotency_key(facts.date, facts.wallet_id)
        if not force and self.job_store.is_complete(key):
            return self.job_store.get(key), True  # type: ignore[return-value]

        try:
            lesson = analyst(facts)
        except Exception as exc:
            lesson = degraded_daily_lesson(facts, f"{type(exc).__name__}: {exc}")
        if lesson is None:
            lesson = degraded_daily_lesson(facts, "model returned no valid lesson")
        else:
            lesson = _enforce_facts(lesson, facts)

        self.job_store.put(key, lesson)
        return lesson, False

    def generate_weekly(
        self,
        window: str,
        cutoff_snapshot_id: str,
        evaluations: list[WalletEvaluation],
        eliminated: dict[str, str],
        synthesizer,
        *,
        force: bool = False,
    ) -> tuple[WeeklyReport, bool]:
        key = weekly_idempotency_key(window)
        if not force and self.job_store.is_complete(key):
            return self.job_store.get(key), True  # type: ignore[return-value]

        rows = build_ranking_rows(
            [e for e in evaluations if e.kind == "active"], eliminated)
        shadow_rows = build_ranking_rows(
            [e for e in evaluations if e.kind == "shadow"], eliminated)
        try:
            report = synthesizer(rows, shadow_rows)
        except Exception as exc:
            report = None
            reason = f"{type(exc).__name__}: {exc}"
        else:
            reason = "model returned no valid report"

        if report is None:
            report = WeeklyReport(
                evaluation_window=window,
                cutoff_snapshot_id=cutoff_snapshot_id,
                ranking_formula_version=RANKING_FORMULA_VERSION,
                active_ranking=rows,
                shadow_ranking=shadow_rows,
                degraded=True,
                degraded_reason=reason,
            )
        else:
            # The committee may never alter the deterministic ranking.
            report = report.model_copy(update={
                "active_ranking": rows,
                "shadow_ranking": shadow_rows,
                "ranking_formula_version": RANKING_FORMULA_VERSION,
                "evaluation_window": window,
                "cutoff_snapshot_id": cutoff_snapshot_id,
            })
            WeeklyReport.model_validate(report.model_dump())

        self.job_store.put(key, report)
        return report, False


def _enforce_facts(lesson: DailyLesson, facts: DailyFacts) -> DailyLesson:
    """Overwrite any model-supplied deterministic figure with the engine's."""

    return lesson.model_copy(update={
        "date": facts.date,
        "wallet_id": facts.wallet_id,
        "strategy_version_id": facts.strategy_version_id,
        "starting_equity": facts.starting_equity,
        "ending_marked_equity": facts.ending_marked_equity,
        "net_daily_profit": facts.net_daily_profit,
        "fees": facts.fees,
        "slippage_cost": facts.slippage_cost,
        "fill_count": facts.fill_count,
        "round_trips": facts.round_trips,
    })
