# Legacy Workflow Deprecation Plan

This document records the intentional deprecation direction for non-core
workflows. It is documentation only. It does not authorize file deletion, file
movement, import changes, runtime path changes, trading strategy changes, order
execution changes, or engine main loop changes.

## Core Workflows To Preserve

- `engine.py` main grid engine.
- Dashboard server, routes, contracts, and static assets.
- SQLite persistence through `sqlite_store.py`.
- AI sidecar behavior.
- `migrate_to_sqlite.py` until a replacement migration/backfill path is agreed.
- Tests, CI, and runtime artifact policy.

## Deprecated Non-Core Workflows

| Workflow | Files | Current status | Removal phase |
|---|---|---|---|
| Research replay scripts | Removed: `grid_engine_honest.py`, `grid_engine_honest_v2.py` | Removed deprecated research/backtest scripts; generated replay JSON outputs remain ignored as legacy local artifacts | Complete |
| Telegram control bot | Removed: `control_bot.py` | Removed deprecated operator control surface; optional notification helpers remain for later ownership cleanup | Complete |
| Baserow tooling | Removed: `baserow_sync.py`, `migrate_to_baserow.py`, `clean_baserow_tradebot_db.py`, `prune_tradebot_order_grid_rows.py` | Removed legacy sync/export/cleanup tooling; no supported Baserow env settings remain | Complete |
| Alternate trend engine | Removed: `engine_trend.py` | Removed deprecated alternate engine workflow; trend runtime artifacts remain ignored as legacy local artifacts | Complete |
| Advisor/flexy workflow | Removed: `advisor.py`, `gridMode=flexy` | Removed deprecated advisor entry point and flexy grid mode; supported grid modes are `scalpy` and `fatty` | Complete |

## Do Not Remove Yet

- Do not remove `migrate_to_sqlite.py`.
- Do not remove or rename `TELEGRAM_CONTROL_BOT_TOKEN` until runtime
  notification ownership is updated together.
- Baserow keys have been removed from `.env.example` with the legacy Baserow
  tooling.
- `TELEGRAM_ADMIN_USER_ID` and `python-telegram-bot` have been removed with the
  deprecated Telegram control bot.

## Suggested Removal Order

1. Research replay scripts have been removed.
2. Legacy Baserow scripts and their dedicated tests/env docs have been removed.
3. Telegram control bot documentation, dependency, and entry point have been
   removed.
4. Alternate trend engine has been removed; keep trend-specific runtime
   artifacts ignored as legacy local artifacts.
5. Advisor/flexy workflow has been removed; `gridMode=flexy` is no longer a
   supported engine/dashboard configuration.
