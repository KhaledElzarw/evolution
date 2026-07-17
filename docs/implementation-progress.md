# Evolutionary Multi-Wallet Platform — Implementation Progress

Branch: `claude/evolutionary-multiwallet-rewrite` (from clean `main` @ `982a554`)

This log records **actual, evidence-backed** progress. A phase is marked complete only
when its acceptance gate passes with recorded command output.

| Phase | Title | Status | Evidence |
|-------|-------|--------|----------|
| 0 | Full local audit & baseline | **Done (audit gate)** | `docs/audits/evolutionary-platform-baseline.md`; baseline 403 pass / 11 fail / 98% cov / ruff 2 |
| 1 | Correct accounting & candle-fill defects (A01/A02) | **Core done** | `tradebot/domain/ledger.py`, `application/execution.py`; `test_ledger.py::test_a02_fee_not_double_counted_over_roundtrip`, `test_execution.py::test_a01_same_candle_cannot_fill_twice` |
| 2 | Package & configuration foundation | **Core done** | `tradebot/{domain,application,...}` skeleton; `tradebot/domain/money.py` Decimal primitives; `test_architecture.py` import-direction gate |
| 3 | Database v2 & legacy migration | In progress | — |
| 4 | Shared market clock & execution simulator | **Core done** | `domain/market.py` immutable snapshot; `application/execution.py` filters + watermark + iteration-order independence (`test_execution.py`) |

### Phase 1/2/4 core — evidence (actual)
- `pytest tests/tradebot -q` → **21 passed**
- `ruff check tradebot tests/tradebot` → clean
- Full suite `pytest -q --ignore=tests/test_json_store.py` → **424 passed, 11 failed** (the same 11 pre-existing platform-specific failures from baseline; no new regressions)
- A02 proof: flat buy+sell at same price yields realized P&L of exactly `-(buy_fee+sell_fee)` — fees counted once.
- A01 proof: a second intent against the same `open_time_ms` returns `RejectReason.DUPLICATE_CANDLE`.

> Scope note: "Core done" means the **new canonical Decimal path** correctly implements the
> invariant with regression tests. Retrofitting the legacy float `engine.py` in place is
> deliberately superseded by this path (legacy remains the migration *source*, per Phase 3).
| 5 | Plugin SDK & isolation | Not started | — |
| 6 | Initial 12 strategies & shadow pool | Not started | — |
| 7 | DataBroker & local llama.cpp client | Not started | — |
| 8 | Daily & weekly learning | Not started | — |
| 9 | Evolution, novelty & promotion | Not started | — |
| 10 | Dark Horse | Not started | — |
| 11 | API & dashboard rewrite | Not started | — |
| 12 | Operations, observability & CI | Not started | — |
| 13 | Independent verification & cleanup | Not started | — |

## Baseline metrics (Phase 0, actual)
- Tests: 403 passed, 11 failed, 1 collection error (`fcntl`), on Windows/Python 3.11.9
- Coverage: 98% (3635 stmts / 69 missed), `fail_under = 0`
- Ruff: 2 errors (select E/F, E501 ignored)
- No mypy / bandit / pip-audit / frontend gates in CI

## Notes
- No production source modified during Phase 0.
- venv created at `.venv` (untracked) for validation only.
