# Migration and Rollback

## Honest status

The **schema and unit-of-work** are implemented and tested. The **legacy-event
import is not** — see `docs/implementation-progress.md` Phase 3
("Schema/UoW core done; legacy import pending") and
`docs/database-schema.md` for the list of entities that exist as application
types but are not yet persisted.

What follows therefore distinguishes **implemented** from **specified but not
yet built**. Do not read the latter as a description of working code.

## Implemented

- `create_schema(engine)` is **idempotent** — running it twice is a no-op, not
  an error (`test_database.py::test_migration_is_idempotent`).
- Foreign keys are enabled on every SQLite connection (`PRAGMA foreign_keys=ON`
  via a connect listener), not once at setup.
- Unit-of-work commits atomically; an exception mid-transaction rolls back all
  writes and leaves no partial state
  (`::test_rollback_on_error_leaves_no_partial_state`).
- Money is stored as exact decimal **text**, never binary float — see
  `docs/audits/phase13-verification.md` V7.
- Tests use temporary SQLite files only. **No test touches a real runtime
  database.** CI Gate 8 asserts the working tree is unchanged after the suite.

## Specified but NOT yet implemented

The legacy migration described below is the **plan**, not shipped code:

1. Detect the legacy singleton schema.
2. Preserve the old database and JSON/JSONL files untouched.
3. Create an archived wallet `LegacyGrid_PreEvolution` (kind: `archived`).
4. Import legacy events into that wallet.
5. **Recompute** balances and P&L from events — never trust the legacy
   cumulative snapshot where it conflicts with event-derived accounting.
6. Record anomalies rather than silently discarding rows; preserve original
   payloads in an audit field.
7. Import AI decisions/memory as *legacy evidence*, not as trusted lessons.
8. Flag fabricated/unverifiable rows. The synthetic `$500` history backfill
   (A17) must never be treated as valid equity.
9. Initialize the 12 active / 12 shadow / Dark Horse wallets only **after**
   migration succeeds.
10. Restartable; a partially completed migration rolls back.

## Backup and rollback procedure (operator)

Before any migration against a real database:

```bash
# 1. Stop services (identity-verified; refuses to signal a mismatched PID).
python -m tradebot.cli.tradebotctl stop

# 2. Timestamped backup — REQUIRED before a destructive migration.
cp runtime/tradebot.db "runtime/tradebot.db.bak.$(date -u +%Y%m%dT%H%M%SZ)"

# 3. Dry run first: writes no production state.
python -m tradebot.cli.tradebotctl migrate --dry-run

# 4. Apply.
python -m tradebot.cli.tradebotctl migrate

# 5. Verify.
python -m tradebot.cli.tradebotctl doctor
```

To roll back: stop services, restore the timestamped backup over
`runtime/tradebot.db`, and restart. Because the legacy database and JSON/JSONL
files are preserved rather than mutated, the pre-migration state is always
recoverable.

**Stop condition:** if a destructive migration cannot be backed up and rolled
back, do not run it.

## Branch rollback

Every commit on this branch is additive to a new `tradebot/` package plus docs;
the legacy flat modules are untouched and still operable. See the per-commit
rollback commands in the session report, or:

```bash
# Discard the whole branch (from a clean tree):
git checkout main
git branch -D claude/evolutionary-multiwallet-rewrite
```

Nothing has been pushed, so no remote state is affected.
