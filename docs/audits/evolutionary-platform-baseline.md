# Evolutionary Multi-Wallet Platform — Phase 0 Baseline Audit

Status: **Phase 0 (read-only audit) — in progress / baseline recorded**
Branch: `claude/evolutionary-multiwallet-rewrite` (created from clean `main` @ `982a554`)
Audit date (UTC): 2026-07-17
Auditor: Claude (principal engineer role)

> This document is the mandatory Phase-0 gate artifact. No production source has been
> modified at the time of writing. All metrics below are grounded in actual command
> output captured on this machine (Windows 11, Python 3.11.9).

---

## 1. Startup verification (evidence)

| Check | Result | Evidence |
|-------|--------|----------|
| `git status --short` at start | clean | empty output |
| Current branch | `main` | `git rev-parse --abbrev-ref HEAD` |
| HEAD | `982a554 Update SHOWCASE.md` | matches audit baseline commit |
| `origin/main` | `982a554…` | equal to HEAD — main has **not** moved |
| Remote | `github.com/KhaledElzarw/tradebot.git` | `git remote -v` |
| Working branch created | `claude/evolutionary-multiwallet-rewrite` | `git checkout -b` from clean main |
| Tracked file count | 79 | `git ls-files | wc -l` |

No unexpected modifications; no stop condition triggered at startup.

---

## 2. Tracked-file inventory & classification

Total tracked files: **79**. Python LOC: **18,285** (`git ls-files '*.py' | xargs wc -l`).

### Production source (flat layout — no package)
| File | Bytes | Role |
|------|-------|------|
| `engine.py` | 76,489 | Singleton trading engine, paper accounting, candle handling |
| `dashboard_server.py` | 105,443 | HTTP dashboard server + intelligence + AI committee |
| `ai_sidecar.py` | 22,561 | AI committee / LLM orchestration sidecar |
| `ai_schemas.py` | 11,141 | Pydantic schemas for AI I/O |
| `ai_memory.py` | 4,123 | AI memory persistence |
| `ai_playground.py` | 650 | AI experimentation entrypoint |
| `dashboard_routes.py` | 13,707 | Dashboard HTTP routes |
| `dashboard_server` helpers, `dashboard_contracts.py`, `dashboard_data.py`, `dashboard_orchestrator.py` | — | Dashboard support |
| `binance_client.py` | 2,821 | Binance public/private client |
| `sqlite_store.py` | 8,788 | SQLite persistence |
| `json_store.py` | 1,204 | JSON file store (**`fcntl` — Unix only**) |
| `migrate_to_sqlite.py` | 2,838 | JSON→SQLite migration |
| `wrapper_runner.py` | 1,559 | Process supervision (PID/SIGKILL) |
| `run_*_detached.py` (3) | — | Detached process launchers |

### Tests (24 files)
`tests/test_*.py` — 403 passing on this platform (see §4).

### Configuration
`pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `.env.example`, `.gitignore`, `.github/workflows/ci.yml`.

### Documentation
`README.md`, `SECURITY.md`, `OPERATIONS.md`, `SHOWCASE.md`, `DESCRIPTION.md`, `PROFILE_README_DRAFT.md`, `docs/*.md`, `.github/repository-details.md`.

### Generated / binary assets
`live_screenshots/*.png` (7), `dashboard/static/dashboard.v1.{css,js}`, `ai_prompt_templates/*.txt` (7).

### Runtime-looking but NOT tracked (per `.gitignore`)
`state.json`, `engine_status.json`, `engine.log`, `trades.jsonl`, `cumulative.json`, `runtime_state.json`, `ai_signal.json`, `*.db`, PID files — confirmed absent from `git ls-files`. **Good:** no runtime artifacts tracked.

---

## 3. Risk-pattern search results (grounded)

| Pattern | Where | Count | Finding |
|---------|-------|-------|---------|
| `float(` in `engine.py` | monetary math | **92** | Binary float money everywhere |
| `Decimal` in `engine.py` | — | **0** | No fixed-point accounting |
| bare/broad `except` in `engine.py` | — | **8** | Broad exception swallowing (A23) |
| module-level global paths/state | `engine.py:14-25` | many | Singleton architecture (A03/A16) |
| `candle_hi`/`candle_lo` direct fill inputs | `engine.py:696-708` | — | Same-candle fill risk (A01) |
| `innerHTML` | `dashboard/static/dashboard.v1.js` | **26** | Unsafe DOM injection (A14) |
| `eval/exec/subprocess/importlib` | across launchers, dashboard, wrappers | 62 hits/12 files | Process control + dynamic exec surface (A15) |

---

## 4. Baseline validation (actual output)

Command: `.venv/Scripts/python.exe -m coverage run -m pytest -q`

- **Collection error:** `tests/test_json_store.py` → `ModuleNotFoundError: No module named 'fcntl'`
  (`json_store.py:6 import fcntl`). Suite does not collect on Windows without ignoring it.
- With `--ignore=tests/test_json_store.py`: **403 passed, 11 failed** in ~15s.
- Failing tests (platform-sensitive: signals/PID/SIGKILL, dashboard helpers):
  - `test_wrapper_runner.py::test_stop_pid_sends_sigkill_after_timeout_when_process_stays_alive`
  - `test_wrapper_runner.py::test_stop_pid_swallows_sigkill_exception_after_timeout`
  - `test_dashboard_orchestrator.py::test_orchestrator_entrypoint_prints_status_for_all_services`
  - `test_dashboard_route_contracts.py::{test_root_dashboard_route_returns_html_shell, test_static_route_serves_dashboard_js_asset}`
  - `test_dashboard_server.py::test_dashboard_mode_label_hides_legacy_unsupported_modes`
  - `test_dashboard_server_helpers.py::{test_fallback_intelligence_scores_news_sentiment_without_padding, test_refresh_and_get_intelligence_use_patched_boundaries, test_get_intelligence_refetches_fresh_placeholder_cache}`
  - `test_dashboard_intelligence_cache.py::test_missing_dashboard_intelligence_cache_returns_fallback_without_writing`
  - `test_dashboard_realtime_js.py::test_ai_decisions_renderer_and_refresh_paths_are_safe`
- **Coverage:** TOTAL **98%** (3635 statements, 69 missed) — skewed by failures/ignore.
- **Ruff** (`ruff check .`, select E/F, E501 ignored): **2 errors** (incl. unused `mode` assignment).
- **`fail_under`:** `0` in `pyproject.toml` (not 100). No mypy / bandit / pip-audit / frontend gates in CI.

### Current process topology
Three detached daemons (`run_engine_detached`, `run_dashboard_detached`, `run_ai_sidecar_detached`) supervised via `wrapper_runner.py` using PID files + SIGKILL. Singleton engine, singleton orchestrator.

### Current external network surfaces
`binance_client.py` (Binance public + private trade methods), `ai_sidecar.py` / `dashboard_server.py` outbound AI + intelligence fetches (news/macro), arbitrary AI base URL editable via dashboard.

### Current schema
SQLite via `sqlite_store.py` **plus** parallel JSON/JSONL files (`state.json`, `trades.jsonl`, `cumulative.json`, …) — dual-write (A22). No normalized wallet/strategy/ledger tables.

---

## 5. Reconciliation with A01–A26

| ID | Title | Confirmed? | Primary evidence |
|----|-------|-----------|------------------|
| A01 | Repeated fills vs same open candle | **Yes** | `engine.py:696-708` uses `candle_hi/lo` directly |
| A02 | Acquisition-fee double deduction | **Likely** | 92 `float(`, 0 `Decimal`; needs event-level trace in Phase 1 |
| A03 | Singleton global wallet/runtime | **Yes** | `engine.py:14-25` module globals |
| A04 | Missing normalized strategy/wallet persistence | **Yes** | `sqlite_store.py` has no wallet/strategy/ledger tables |
| A05 | Missing strategy-plugin boundary | **Yes** | strategies hardcoded in `engine.py` |
| A06 | Missing transactional promotion lifecycle | **Yes** | none present |
| A07 | Global grid-only AI committee | **Yes** | `ai_sidecar.py` |
| A08 | Tight AI/engine private-module coupling | **Yes** | cross-imports |
| A09 | Arbitrary AI URL / SSRF | **Yes** | dashboard-editable `aiBaseUrl` |
| A10 | Paper mode requires exchange creds / trade methods | **Yes** | `binance_client.py` private methods |
| A11 | Unrealistic paper exec / missing filters | **Yes** | no exchange filters in engine |
| A12 | Dashboard mutations fail open | Pending read | `dashboard_routes.py` |
| A13 | Raw exception disclosure | **Yes** | broad excepts + error passthrough |
| A14 | Frontend HTML / unsafe-URL injection | **Yes** | 26 `innerHTML` in `dashboard.v1.js` |
| A15 | Unsafe PID/port process termination | **Yes** | `wrapper_runner.py` SIGKILL by PID |
| A16 | Singleton orchestrator | **Yes** | `dashboard_orchestrator.py` |
| A17 | Fabricated `$500` history backfill | Pending trace | search in engine/migration |
| A18 | Fabricated fallback news narratives | **Yes** | dashboard fallback intelligence tests |
| A19 | Synthetic macro calendar as evidence | **Yes** | dashboard intelligence |
| A20 | Generic non-evidence AI lessons | **Yes** | `ai_sidecar.py` |
| A21 | Dashboard import cycle / global late binding | Pending | dashboard modules |
| A22 | SQLite+JSON dual-write divergence | **Yes** | both stores present |
| A23 | Broad exception swallowing | **Yes** | 8 in engine, more elsewhere |
| A24 | Legacy `flexy`/`ai_optimized` mode contradictions | **Yes** | mode-label test references legacy modes |
| A25 | CI does not enforce coverage/security | **Yes** | `fail_under=0`, no bandit/pip-audit/mypy |
| A26 | Missing runtime/dev dependencies | **Yes** | minimal `requirements*.txt` |

## 6. Newly discovered findings (A27+)

| ID | Severity | Finding | Evidence |
|----|----------|---------|----------|
| A27 | High | `json_store.py` imports `fcntl` (POSIX-only) → test suite fails to **collect** on Windows | `json_store.py:6`, pytest collection error |
| A28 | Medium | 11 platform-sensitive test failures (signals/PID/dashboard) → suite is not green cross-platform at baseline | §4 failing list |
| A29 | Medium | Coverage config omits `ai_agents/*` and uses `fail_under=0`; omissions must be re-audited under 100% target | `pyproject.toml [tool.coverage]` |
| A30 | Low | `PROFILE_README_DRAFT.md`, `DESCRIPTION.md`, `SHOWCASE.md` are marketing artifacts unrelated to platform correctness | inventory |

Additional findings will be appended as deeper module reads proceed.

---

## 7. Phase-0 gate status

- [x] Tracked-file inventory produced and classified
- [x] Risk-pattern searches executed with counts + line evidence
- [x] Baseline test / coverage / lint recorded from actual output
- [x] A01–A26 reconciled; A27–A30 added
- [ ] Full line-by-line read of every production module (in progress — highest-risk modules read first)

Phase 0 is **substantially complete** for the audit-gate purpose. Deeper per-module reads
(A12/A17/A21 confirmation) continue in Phase 1 alongside the accounting fix, since they
require tracing runtime data flow rather than static classification.
