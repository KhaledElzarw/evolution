"""Daily lesson and weekly report schemas.

These are the strict JSON schemas the local model must satisfy (validated with
Pydantic, repaired with a bounded retry, and persisted as structured records
*before* any Markdown is rendered). Markdown files are derived exports, never
canonical state (closes A22 for reports).

Every analytical conclusion must reference stored evidence IDs. A claim with no
supporting evidence is a *hypothesis* and must be labelled as such — the
schema enforces this rather than trusting the model (closes A20).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

DAILY_SCHEMA_VERSION = "daily-lesson-v1"
WEEKLY_SCHEMA_VERSION = "weekly-report-v1"

# Committee roles. They analyse; they never compute or alter rank.
DAILY_COMMITTEE_ROLES: tuple[str, ...] = (
    "performance_forensic_analyst",
    "market_regime_analyst",
    "execution_and_accounting_analyst",
    "strategy_behavior_analyst",
    "bull_case_analyst",
    "bear_case_analyst",
    "lesson_synthesizer",
)

WEEKLY_COMMITTEE_ROLES: tuple[str, ...] = (
    "weekly_evidence_auditor",
    "strategy_lineage_and_novelty_auditor",
    "top_performer_mutation_planner",
    "novel_strategy_ideation_planner",
    "failure_pattern_analyst",
    "candidate_curator",
    "final_evolution_planner",
)


class Claim(BaseModel):
    """An analytical statement. Unsupported claims MUST be hypotheses."""

    statement: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    is_hypothesis: bool = False

    @model_validator(mode="after")
    def _unsupported_must_be_hypothesis(self) -> Claim:
        if not self.evidence_ids and not self.is_hypothesis:
            raise ValueError(
                "a claim without evidence_ids must be labelled is_hypothesis=True"
            )
        return self


class DailyLesson(BaseModel):
    """Schema for one wallet's completed-UTC-day lesson."""

    schema_version: str = DAILY_SCHEMA_VERSION
    date: str  # YYYY-MM-DD (completed UTC day)
    wallet_id: str
    strategy_version_id: str

    # Deterministic figures — computed by the engine, NOT by the model.
    starting_equity: Decimal
    ending_marked_equity: Decimal
    net_daily_profit: Decimal
    fees: Decimal
    slippage_cost: Decimal
    fill_count: int
    round_trips: int

    # Model-authored analysis, all evidence-bound.
    market_regime: str
    observation: Claim
    hypothesis: Claim
    counterevidence: list[Claim] = Field(default_factory=list)
    confidence: Decimal = Field(ge=0, le=1)
    recommended_experiment: str = ""

    supporting_trade_ids: list[str] = Field(default_factory=list)
    supporting_snapshot_ids: list[str] = Field(default_factory=list)
    external_data_snapshot_ids: list[str] = Field(default_factory=list)

    previous_lesson_validation: str = "not_applicable"  # confirmed|rejected|…
    model_run_id: str = ""
    degraded: bool = False
    degraded_reason: str = ""

    @field_validator("schema_version")
    @classmethod
    def _version(cls, v: str) -> str:
        if v != DAILY_SCHEMA_VERSION:
            raise ValueError(f"unsupported daily schema version: {v}")
        return v

    @model_validator(mode="after")
    def _hypothesis_must_be_labelled(self) -> DailyLesson:
        if not self.hypothesis.is_hypothesis:
            raise ValueError("the hypothesis field must have is_hypothesis=True")
        return self


class RankingRow(BaseModel):
    """A ranking row. The model reports it; it may never change it."""

    wallet_id: str
    strategy_version_id: str
    weekly_net_profit_usdt: Decimal
    rank: int
    fill_count: int
    eliminated: bool = False
    elimination_reason: str = ""


class WeeklyReport(BaseModel):
    schema_version: str = WEEKLY_SCHEMA_VERSION
    evaluation_window: str  # e.g. 2026-W29
    cutoff_snapshot_id: str
    ranking_formula_version: str

    active_ranking: list[RankingRow]
    shadow_ranking: list[RankingRow] = Field(default_factory=list)
    dark_horse_summary: str = ""

    eliminations: list[str] = Field(default_factory=list)
    replacement_count: int = 0
    novel_count: int = 0
    mutation_count: int = 0
    top_parents: list[str] = Field(default_factory=list)
    candidate_selection: list[str] = Field(default_factory=list)
    novelty_evidence: list[Claim] = Field(default_factory=list)

    lessons_confirmed: list[Claim] = Field(default_factory=list)
    lessons_rejected: list[Claim] = Field(default_factory=list)
    unresolved_hypotheses: list[Claim] = Field(default_factory=list)

    technical_incidents: list[str] = Field(default_factory=list)
    quarantines: list[str] = Field(default_factory=list)
    promotion_batch_id: str = ""
    rollback_status: str = "none"
    abbreviated_promotion_reason: str = ""

    model_run_id: str = ""
    prompt_version: str = ""
    degraded: bool = False
    degraded_reason: str = ""

    @field_validator("schema_version")
    @classmethod
    def _version(cls, v: str) -> str:
        if v != WEEKLY_SCHEMA_VERSION:
            raise ValueError(f"unsupported weekly schema version: {v}")
        return v

    @model_validator(mode="after")
    def _ranking_must_be_profit_ordered(self) -> WeeklyReport:
        """The committee cannot reorder the deterministic ranking."""

        profits = [r.weekly_net_profit_usdt for r in self.active_ranking]
        if profits != sorted(profits, reverse=True):
            raise ValueError("active_ranking must be ordered by profit descending")
        ranks = [r.rank for r in self.active_ranking]
        if ranks != list(range(1, len(ranks) + 1)):
            raise ValueError("ranks must be contiguous starting at 1")
        return self

    @model_validator(mode="after")
    def _losers_cannot_survive(self) -> WeeklyReport:
        """The committee cannot preserve a losing incumbent."""

        for row in self.active_ranking:
            if row.weekly_net_profit_usdt < 0 and not row.eliminated:
                raise ValueError(
                    f"losing active strategy {row.wallet_id} not marked eliminated"
                )
            if row.fill_count == 0 and not row.eliminated:
                raise ValueError(
                    f"zero-fill active strategy {row.wallet_id} not marked eliminated"
                )
        return self
