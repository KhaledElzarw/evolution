# Legacy and Quarantine Candidate Inventory

This inventory records files that may be legacy, duplicated, experimental, or
runtime/archive material. It does not authorize deletion, movement, import
changes, or behavior changes.

Default status for every item is **Needs confirmation**. Do not claim a file is
unused until references, tests, operator workflows, and runtime behavior have
been checked.

## Summary

Candidate groups:

- Historical or experimental grid engines.
- Trend-specific engine path.
- Advisor process.
- Backup files matching `*.bak` or `*.bak_*`.
- `accounting_archive_*` folders.
- Runtime JSON/JSONL archives and mirrors.

## grid_engine_honest.py

Status: **Research/backtest replay script; quarantine candidate later; do-not-move-yet**

1. Current path: `grid_engine_honest.py`
2. Why suspicious: Root-level engine-like module with grid/accounting classes
   and helper names that overlap with the main engine.
3. Evidence of duplication or runtime/archive nature:
   - Defines `PaperAccount`, `GridOrder`, and `GridState`.
   - Defines helpers such as `_ema`, `_atr`, `_spacing_for_mode`,
     `_build_grid_orders`, and `_fill_order_paper`.
   - Similar concepts also exist in the main engine path.
   - Generates `grid_honest_replay.json` and
     `grid_honest_replay_equity.json`; these should remain ignored if produced.
   - No supported README/OPERATIONS invocation found.
   - Performs replay work at import time.
   - Reads a hardcoded local feather data path.
   - Writes replay outputs and prints a result.
4. Risk if moved:
   - May break manual backtesting, ad hoc operator workflows, or undocumented
     scripts.
   - May remove useful comparison behavior for historical strategy analysis.
5. How to confirm usage:
   - Run `rg "grid_engine_honest" .`.
   - Check shell history, runbooks, README/OPERATIONS references, and open pull
     requests.
   - Ask the repository owner whether it is still used for local research.
6. Proposed future action:
   - If confirmed research-only, move later to a documented quarantine or
     research area in a dedicated commit.
   - If still useful, document its purpose and supported invocation.
7. Required tests before moving:
   - Full pytest suite.
   - Compileall.
   - Any existing or newly added characterization test that captures expected
     grid/accounting output for a small fixture.

## grid_engine_honest_v2.py

Status: **Research/backtest replay script; quarantine candidate later; do-not-move-yet**

1. Current path: `grid_engine_honest_v2.py`
2. Why suspicious: Versioned root-level engine-like module suggests an
   experiment or replacement candidate.
3. Evidence of duplication or runtime/archive nature:
   - Defines `PaperAccount`, `GridOrder`, and `GridState`.
   - Defines `_ema`, `_atr`, `_build_grid_orders`, and `_fill`.
   - Overlaps conceptually with `grid_engine_honest.py` and `engine.py`.
   - Generates `grid_honest_replay_v2.json`; this should remain ignored if
     produced.
   - No supported README/OPERATIONS invocation found.
   - Performs replay work at import time.
   - Reads a hardcoded local feather data path.
   - Writes replay output and prints a result.
4. Risk if moved:
   - May break local experiments or comparison scripts.
   - Versioned name may hide knowledge about why v2 exists.
5. How to confirm usage:
   - Run `rg "grid_engine_honest_v2|grid_engine_honest" .`.
   - Compare with `grid_engine_honest.py` and `engine.py`.
   - Ask maintainers whether v2 supersedes or documents a prior behavior.
6. Proposed future action:
   - Preserve in place until usage is confirmed.
   - Later quarantine with notes or convert into documented test fixtures if it
     is purely historical.
7. Required tests before moving:
   - Full pytest suite.
   - Compileall.
   - Characterization test for a representative v2 grid scenario if movement is
     approved.

## engine_trend.py

Status: **Operator-facing legacy workflow; do-not-move-yet**

1. Current path: `engine_trend.py`
2. Why suspicious: Alternate engine entry point in the repository root.
3. Evidence of duplication or runtime/archive nature:
   - Defines engine-like runtime classes and functions such as `PaperAccount`,
     `Position`, `Stats`, `_write_status`, `_append_trade`, `_ema`, `_atr`, and
     `main`.
   - Writes or references trend-specific runtime artifacts such as
     `engine_trend.log`.
   - Telegram control bot exposes TREND commands that read and write
     trend-specific state/status/trade files.
   - Importing the module loads local environment variables.
   - Runtime execution is guarded by `main`.
4. Risk if moved:
   - May break an alternate trend engine workflow.
   - May affect operators who still run the trend engine manually.
   - Could disrupt migration or comparison work if the file is still active.
5. How to confirm usage:
   - Run `rg "engine_trend|run_engine_trend|status_trend|summary_trend" .`.
   - Check Telegram trend commands and operations docs.
   - Confirm with operators whether trend mode is active or historical.
6. Proposed future action:
   - Keep in place until trend-engine usage is confirmed.
   - If legacy, move later with a compatibility note and explicit rollback path.
7. Required tests before moving:
   - Full pytest suite.
   - Compileall.
   - Characterization coverage for trend status/runtime file expectations.
   - Manual operator confirmation that no service manager invokes it.

## advisor.py

Status: **Operator-facing legacy workflow; do-not-move-yet**

1. Current path: `advisor.py`
2. Why suspicious: Separate root-level process with deterministic advisor
   behavior and its own log path.
3. Evidence of duplication or runtime/archive nature:
   - Defines market helpers such as `_ema`, `_atr`, `_advisor_light`,
     `_advisor_full`, and `_fetch_market`.
   - Writes `advisor.log`.
   - Control text references an advisor choosing mode for `flexy`.
   - Main engine still accepts `gridMode=flexy` and has a fallback when no
     advisor process is active.
   - Importing the module loads local environment variables.
   - Runtime execution is guarded by `main`.
4. Risk if moved:
   - May break a still-supported advisor workflow.
   - May break manual operator commands or expectations around `flexy`.
   - May obscure historical decision support behavior.
5. How to confirm usage:
   - Run `rg "advisor|advisorEnabled|advisor.log|flexy" .`.
   - Confirm whether any operators still run `python advisor.py`.
   - Check service wrappers, process managers, and local runbooks.
6. Proposed future action:
   - Keep in place until advisor usage is confirmed.
   - If active, document ownership and invocation.
   - If inactive, quarantine later with a clear note that behavior was not
     deleted.
7. Required tests before moving:
   - Full pytest suite.
   - Compileall.
   - Characterization tests around advisor state patching if still relevant.
   - Manual smoke check of any control command that references advisor behavior.

## migrate_to_sqlite.py

Status: **Active/manual migration tool; do-not-move-yet**

1. Current path: `migrate_to_sqlite.py`
2. Why inventoried: Root-level manual command that reads runtime JSON/JSONL
   mirrors and writes the SQLite runtime store.
3. Evidence of active/manual ownership:
   - Documented in `OPERATIONS.md` as an optional/manual storage migration or
     backfill command.
   - Listed in `docs/architecture.md` as the migration/backfill entry point.
   - Covered by `tests/test_migrate_to_sqlite.py`.
   - Importing the module does not run migration; execution is guarded by
     `main`.
4. Risk if moved:
   - Would break the documented operator command unless wrappers, docs, and
     tests are updated together.
5. Proposed future action:
   - Keep in place until migration commands are intentionally reorganized.
   - Preserve current command compatibility or provide an explicit replacement
     before any move.
6. Required tests before moving:
   - Full pytest suite.
   - Compileall.
   - SQLite migration tests.
   - Manual operator confirmation that documented migration/backfill commands
     have been updated.

## Backup Files Matching `*.bak` or `*.bak_*`

Status: **Needs confirmation for local cleanup; do not track**

1. Current path:
   - `trades.jsonl.bak_pre_resume_cleanup`
   - `trades.jsonl.bak_pre_dedupe`
   - `dashboard_server.py.bak_20260501_080913`
   - `engine.py.bak_20260501_080913`
   - Future files matching `*.bak` or `*.bak_*`
2. Why suspicious: Backup suffixes indicate local snapshots rather than source.
3. Evidence of duplication or runtime/archive nature:
   - The patterns are ignored by `.gitignore`.
   - Some names point to old copies of source files.
   - Some names point to archived runtime trade logs.
4. Risk if moved:
   - Usually low for source control because ignored backups are not tracked.
   - Operational risk exists if an operator expects the local backup for manual
     rollback or audit.
5. How to confirm usage:
   - Run `find . -maxdepth 3 \( -name '*.bak' -o -name '*.bak_*' \) -print`.
   - Confirm whether any file is intentionally retained for audit or rollback.
   - Check that no scripts reference a specific backup filename.
6. Proposed future action:
   - Keep ignored and out of Git.
   - Move to private local backup storage if retention is required.
   - Delete locally only after operator confirmation; do not delete as part of
     modernization commits.
7. Required tests before moving:
   - Full pytest suite if moving source-like backups out of the repo tree.
   - No runtime tests are required for ignored backups unless a script references
     them.
   - Operator confirmation for any retained audit backup.

## `accounting_archive_*` Folders

Status: **Needs confirmation for local cleanup; do not track**

1. Current path:
   - `accounting_archive_2026-04-30/`
   - Future folders matching `accounting_archive_*`
2. Why suspicious: Date-stamped archive directory containing runtime state.
3. Evidence of duplication or runtime/archive nature:
   - The observed folder contains runtime files such as `runtime_state.json`,
     `trades.jsonl`, `engine_status.json`, and `cumulative.json`.
   - The folder is ignored by Git as an untracked runtime/archive artifact.
4. Risk if moved:
   - May remove local rollback/audit material.
   - May affect manual accounting reconciliation if operators still need it.
5. How to confirm usage:
   - List filenames only; do not print values from archive contents.
   - Ask operators whether the archive is needed for accounting, incident
     review, or migration rollback.
   - Check scripts for references to `accounting_archive_`.
6. Proposed future action:
   - Keep out of Git.
   - Move to private backup storage if retention is required.
   - Clean locally only after explicit operator approval.
7. Required tests before moving:
   - No application behavior tests are required for purely local archive
     movement.
   - Run full pytest suite if scripts or migration workflows are adjusted later.
   - Confirm SQLite and JSON migration rollback workflows before removing any
     archive needed for recovery.

## Runtime JSON/JSONL Archives and Mirrors

Status: **Needs confirmation; runtime artifacts must stay out of Git**

1. Current path:
   - `state.json`
   - `state_trend.json`
   - `runtime_state.json`
   - `engine_status.json`
   - `engine_status_trend.json`
   - `cumulative.json`
   - `cumulative_trend.json`
   - `trades.jsonl`
   - `trades_trend.jsonl`
   - `ai_signal.json`
   - `ai_decisions.jsonl`
   - `ai_memory.json`
   - `dashboard_history.json`
   - `*.json.bak*`
   - `*.jsonl.bak*`
2. Why suspicious: These are generated runtime state, compatibility mirrors, or
   archives of operational data.
3. Evidence of duplication or runtime/archive nature:
   - They are ignored by `.gitignore`.
   - The architecture and operations docs identify SQLite as the canonical
     local runtime store, with JSON/JSONL files retained as compatibility
     mirrors.
   - JSONL trade logs and AI logs may contain operational history.
4. Risk if moved:
   - Moving active runtime files can break local services if paths are still
     expected by wrappers, dashboard code, engine code, or migration scripts.
   - Moving archives can remove operator recovery context.
5. How to confirm usage:
   - Run `rg "state.json|runtime_state.json|engine_status.json|trades.jsonl|ai_signal.json|ai_decisions.jsonl|ai_memory.json|dashboard_history.json" .`.
   - Confirm active runtime paths in code before moving anything.
   - Confirm operator backup and recovery requirements.
6. Proposed future action:
   - Keep active runtime files ignored and local-only.
   - Later separate runtime directories from source only with explicit path
     compatibility and migration planning.
   - Add tests before changing any path used by runtime code.
7. Required tests before moving:
   - Full pytest suite.
   - Compileall.
   - Dashboard payload tests.
   - Engine runtime snapshot tests.
   - SQLite migration tests.
   - Manual smoke check for orchestrator, dashboard, and engine status.

## Runtime Cache: dashboard_intelligence.json

Status: **Classified as generated runtime cache.** See
[Dashboard Intelligence Cache Classification](dashboard-intelligence-classification.md).

1. Current path: `dashboard_intelligence.json`
2. Why suspicious: JSON snapshot-style filename in the repository root.
3. Evidence of duplication or runtime/archive nature:
   - The file is not tracked and is ignored by `.gitignore`.
   - The filename suggests cached dashboard intelligence or generated state.
   - Similar runtime JSON files are ignored elsewhere.
4. Risk if moved:
   - May break dashboard boot or deterministic fallback behavior if the file is
     intentionally a fixture or seed.
   - May remove a known-good cached intelligence payload used by operators.
5. How to confirm usage:
   - Run `rg "dashboard_intelligence" .`.
   - Determine whether it is a fixture, seed data, or runtime cache.
   - Check dashboard tests and startup behavior.
6. Proposed future action:
   - Keep ignored and local-only, preserving any local operator copy.
   - Move to ignored runtime storage only in a later explicit
     behavior-preserving commit.
   - If fixture/seed, rename or document its role in a future docs/test commit.
7. Required tests before moving:
   - Full pytest suite.
   - Dashboard server tests.
   - Manual dashboard boot check.
   - Confirmation that no deployment expects the root path.

## General Quarantine Criteria

A future quarantine move should require:

1. A clean working tree or intentional preservation branch/stash.
2. Reference search results.
3. Maintainer/operator confirmation for ambiguous files.
4. Characterization tests for any executable or importable code.
5. A dedicated commit that moves files without editing logic.
6. A rollback command and clear release note.

Until then, these files should remain in place.
