"""Atomic Markdown rendering of stored report records.

Markdown files are DERIVED EXPORTS. The structured record is canonical; these
files are regenerable and are written atomically (temp file + os.replace) so a
crash never leaves a torn report (closes A22 for reports).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ..domain.lessons import Claim, DailyLesson, WeeklyReport


def atomic_write(path: Path, content: str) -> None:
    """Write via temp file + atomic replace within the same directory."""

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):  # pragma: no cover - cleanup path
            os.unlink(tmp)
        raise


def _claim(c: Claim) -> str:
    tag = " *(hypothesis)*" if c.is_hypothesis else ""
    refs = f" [evidence: {', '.join(c.evidence_ids)}]" if c.evidence_ids else ""
    return f"{c.statement}{tag}{refs}"


def render_daily(lesson: DailyLesson) -> str:
    lines = [
        f"# Daily Lesson — {lesson.date} — {lesson.wallet_id}",
        "",
        f"Strategy version: `{lesson.strategy_version_id}`",
        "",
    ]
    if lesson.degraded:
        lines += [f"> **DEGRADED:** {lesson.degraded_reason}", ""]
    lines += [
        "## Deterministic figures",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Starting equity | {lesson.starting_equity} |",
        f"| Ending marked equity | {lesson.ending_marked_equity} |",
        f"| Net daily profit | {lesson.net_daily_profit} |",
        f"| Fees | {lesson.fees} |",
        f"| Slippage cost | {lesson.slippage_cost} |",
        f"| Fills | {lesson.fill_count} |",
        f"| Round trips | {lesson.round_trips} |",
        "",
        "## Analysis",
        "",
        f"- Market regime: {lesson.market_regime}",
        f"- Observation: {_claim(lesson.observation)}",
        f"- Hypothesis: {_claim(lesson.hypothesis)}",
    ]
    for c in lesson.counterevidence:
        lines.append(f"- Counterevidence: {_claim(c)}")
    lines += [
        f"- Confidence: {lesson.confidence}",
        f"- Recommended experiment: {lesson.recommended_experiment or '—'}",
        f"- Previous lesson validation: {lesson.previous_lesson_validation}",
        "",
        "## Evidence",
        "",
        f"- Trades: {', '.join(lesson.supporting_trade_ids) or '—'}",
        f"- Snapshots: {', '.join(lesson.supporting_snapshot_ids) or '—'}",
        f"- External data: {', '.join(lesson.external_data_snapshot_ids) or '—'}",
        f"- Model run: `{lesson.model_run_id or '—'}`",
        "",
    ]
    return "\n".join(lines)


def render_weekly(report: WeeklyReport) -> str:
    lines = [
        f"# Weekly Report — {report.evaluation_window}",
        "",
        f"Cutoff snapshot: `{report.cutoff_snapshot_id}`  ",
        f"Ranking formula: `{report.ranking_formula_version}`",
        "",
    ]
    if report.degraded:
        lines += [f"> **DEGRADED:** {report.degraded_reason}", ""]
    lines += [
        "## Active ranking",
        "",
        "| Rank | Wallet | Strategy version | Weekly net profit (USDT) | Fills | Eliminated |",
        "|-----:|--------|------------------|-------------------------:|------:|------------|",
    ]
    for row in report.active_ranking:
        elim = row.elimination_reason if row.eliminated else "—"
        lines.append(
            f"| {row.rank} | {row.wallet_id} | `{row.strategy_version_id}` | "
            f"{row.weekly_net_profit_usdt} | {row.fill_count} | {elim} |"
        )
    if report.shadow_ranking:
        lines += [
            "",
            "## Shadow ranking (virtual capital — not part of active totals)",
            "",
            "| Rank | Wallet | Weekly net profit (USDT) | Fills |",
            "|-----:|--------|-------------------------:|------:|",
        ]
        for row in report.shadow_ranking:
            lines.append(f"| {row.rank} | {row.wallet_id} | "
                         f"{row.weekly_net_profit_usdt} | {row.fill_count} |")
    lines += [
        "",
        "## Dark Horse",
        "",
        report.dark_horse_summary or "—",
        "",
        "## Evolution",
        "",
        f"- Eliminations: {', '.join(report.eliminations) or '—'}",
        f"- Replacements: {report.replacement_count} "
        f"(novel {report.novel_count} / mutation {report.mutation_count})",
        f"- Top parents: {', '.join(report.top_parents) or '—'}",
        f"- Candidates: {', '.join(report.candidate_selection) or '—'}",
        f"- Promotion batch: `{report.promotion_batch_id or '—'}`",
        f"- Rollback status: {report.rollback_status}",
    ]
    if report.abbreviated_promotion_reason:
        lines.append(f"- Abbreviated promotion: {report.abbreviated_promotion_reason}")
    for title, claims in (("Novelty evidence", report.novelty_evidence),
                          ("Lessons confirmed", report.lessons_confirmed),
                          ("Lessons rejected", report.lessons_rejected),
                          ("Unresolved hypotheses", report.unresolved_hypotheses)):
        lines += ["", f"## {title}", ""]
        lines += [f"- {_claim(c)}" for c in claims] or ["—"]
    lines += [
        "",
        "## Technical",
        "",
        f"- Incidents: {', '.join(report.technical_incidents) or '—'}",
        f"- Quarantines: {', '.join(report.quarantines) or '—'}",
        f"- Model run: `{report.model_run_id or '—'}` "
        f"(prompt `{report.prompt_version or '—'}`)",
        "",
    ]
    return "\n".join(lines)


def daily_path(root: Path, date: str) -> Path:
    return root / "daily" / f"{date}.md"


def weekly_path(root: Path, window: str) -> Path:
    return root / "weekly" / f"{window}.md"


def write_daily(root: Path, lesson: DailyLesson) -> Path:
    path = daily_path(root, lesson.date)
    atomic_write(path, render_daily(lesson))
    return path


def write_weekly(root: Path, report: WeeklyReport) -> Path:
    path = weekly_path(root, report.evaluation_window)
    atomic_write(path, render_weekly(report))
    return path
