# Ruff Cleanup Foundation

This classification records the first Ruff cleanup pass for
`chore/ruff-cleanup-foundation`.

The pass used the repository Ruff scope in `pyproject.toml`: `E` and `F`.
Initial findings:

- `E501`: 195 long-line findings.
- `F821`: 53 undefined-name findings in `dashboard_routes.py`.
- `F401`: 9 unused-import findings.
- `F841`: 3 unused-local findings.
- `E402`: 1 late-import finding.

## Fixed or Baseline-Cleared

- Removed genuinely unused imports from `ai_memory.py`, `ai_sidecar.py`,
  `grid_engine_honest.py`, and `dashboard_server.py`.
- Kept `dashboard_contracts.validate_dashboard_payload` available from
  `dashboard_server.py` because `dashboard_routes.py` binds dashboard globals
  lazily.
- Marked the late `dashboard_routes.Handler` import in `dashboard_server.py` as
  intentional because it avoids the dashboard module cycle.
- Marked `dashboard_routes.py` `F821` findings as intentional for now because
  handler methods call `_bind_app_globals()` before using dashboard server
  helpers and constants.
- Renamed unused local Binance client bindings to `_client` in `advisor.py`,
  `engine.py`, and `engine_trend.py` without removing construction.

## Deferred

- `E501` long-line cleanup is deferred. The initial line-length findings span
  trading, dashboard, migration, tests, and legacy/manual scripts. Wrapping all
  of them in this branch would create broad formatting churn with little safety
  value.

Future branches should reduce `E501` incrementally in small, tested areas
rather than auto-formatting the repository in one pass.

## E501 Area Plan

As of `chore/ruff-e501-area-plan`, Ruff reports 197 `E501` findings when run
with `--select E501`.

`E501` should remain ignored in CI until the findings are reduced to zero
through small, area-specific branches. Mass formatting is intentionally avoided
to keep diffs reviewable and reduce the risk of accidental runtime changes.

Recommended cleanup order:

1. Tests: lowest risk; wrap fixtures, assertions, and test data first.
2. Pure helper modules: medium risk; preserve public data shapes.
3. Migration and Baserow maintenance tools: medium risk; verify payloads.
4. Dashboard routes/server rendering: medium-high risk; protect HTML/JS output.
5. Engine, trading, and runtime control code: high risk; defer until covered by
   characterization tests.
6. Legacy/research scripts: defer unless the files are quarantined or excluded.
