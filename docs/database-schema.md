# Database Schema

The database is **canonical**; JSON and Markdown files are derived exports
(closes A22). Implemented with SQLAlchemy 2.x in
[models.py](../tradebot/infrastructure/database/models.py) with a unit-of-work
boundary in [unit_of_work.py](../tradebot/infrastructure/database/unit_of_work.py).

## Honest implementation status

This is a **partial** schema. The tables below are implemented and tested; the
remainder of the spec's ~30 entities are modelled at the application layer
(dataclasses in `tradebot/domain` and `tradebot/application`) but **not yet
persisted**. This gap is tracked — see `docs/implementation-progress.md` Phase
3 ("Schema/UoW core done; legacy import pending").

### Implemented tables

| Table | Purpose |
|-------|---------|
| `wallets` | wallet_id, kind, stable/display name, status, balances, timestamps |
| `strategy_definitions` | conceptual family, canonical name, origin, banned flag |
| `strategy_versions` | version id, hashes, fingerprint, generation, status, timestamps |
| `wallet_strategy_assignments` | assignment id, activation/deactivation, starting balances; **unique active assignment per wallet** |
| `strategy_bans` | code hash + fingerprint, reason, permanent flag |
| `market_snapshots` | immutable snapshot rows with content hash |
| `ledger_transactions` | one atomic transaction per fill |
| `ledger_postings` | balanced postings (sum to zero) |
| `job_runs` | durable idempotency keys, lease owner, timestamps, result hash |

### Not yet persisted (application-layer only for now)

`wallet_archives`, `strategy_lineage`, `orders`, `fills`, `position_lots`,
`wallet_equity_snapshots`, `evaluation_windows`, `strategy_evaluations`,
`daily_lessons`, `weekly_reports`, `data_sources`, `data_snapshots`,
`llm_runs`, `candidate_runs`, `shadow_candidates`, `validation_runs`,
`promotion_batches`, `promotion_items`, `quarantines`, `audit_events`,
`app_settings`. Their shapes exist as typed value objects; wiring them to
persistence is remaining Phase 3 work.

## Enforced constraints (implemented + tested)

- **Foreign keys enabled** per connection (`PRAGMA foreign_keys=ON`).
- **Unique active assignment per wallet** — partial unique constraint.
- **Check constraints** for non-negative balances and valid enum values.
- **Unique idempotency key** on `job_runs`.
- Explicit transactions via the unit-of-work; commit is atomic and an exception
  mid-transaction rolls back all writes.
- Money columns stored as text/`Numeric`, never `REAL`/float.

Tested in `tests/tradebot/test_database.py` (idempotent migration, FK
enforcement, unique idempotency key, check constraints, atomic rollback).

## Retention

Historical ledger, lineage, reports, and bans are kept indefinitely. Retention
may compact replaceable market snapshots but never accounting or lineage
evidence.
