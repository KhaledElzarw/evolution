# Release Checklist

This branch is a **release candidate for the new `tradebot` package**, not a
finished replacement of the legacy stack. Tick the gates honestly; a gate that
does not pass is recorded as not passing.

## Automated gates (CI)

| # | Gate | Status on this branch |
|---|------|----------------------|
| 1 | Repository hygiene — no tracked runtime artifacts/db/secrets | ✅ |
| 2 | Correctness — ruff, mypy, compileall, suite, coverage ratchet | ✅ (coverage **97%**, ratchet — *not* the 100% target) |
| 3 | Security — bandit, pip-audit, plugin/SSRF/API suites, secret scan | ✅ (bandit 0, pip-audit clean) |
| 4 | Database — migration idempotency, FK integrity, atomicity | ✅ (legacy import **not** implemented) |
| 5 | Frontend — `node --check`, DOM-sink and URL-vetting tests | ⚠️ static analysis only; no jsdom/Playwright yet |
| 6 | Deterministic replay — goldens, A01/A02, 25-wallet replay | ✅ |
| 7 | Performance — measured tick budget | ✅ (0.62 ms/tick measured, 10 ms budget) |
| 8 | Release candidate — full suite, clean tree, required docs | ✅ |

## Manual verification before any real run

- [ ] `git status --short` is empty.
- [ ] Only approved files changed; no runtime artifacts tracked.
- [ ] `.env` and credentials are **not** in the repo.
- [ ] Database backed up (see `migration-and-rollback.md`).
- [ ] Rollback procedure understood and tested.
- [ ] `tradebotctl doctor` is green.
- [ ] Operator has read the residual sandbox risk in `threat-model.md`.

## Known blockers to a *full* release

These are real and must not be papered over:

1. **Legacy import not implemented** (Phase 3). The platform can start fresh but
   cannot yet migrate an existing runtime.
2. **Coverage is 97%, not the specified 100%.** 104 statements remain; the exact
   gap and the one documented omission are in `docs/testing.md`.
3. **Evolution rules are unverified by an independent reviewer** — see
   `docs/audits/phase13-verification.md`. They pass the author's own tests only.
4. **Frontend has no behavioural DOM tests** (jsdom/Playwright); only static
   safety analysis.
5. **Legacy stack still present.** `engine.py`, `dashboard_server.py` et al. are
   untouched and still carry their audit findings; the new package supersedes
   rather than replaces them so far.
6. **11 pre-existing test failures** persist from the Phase-0 baseline
   (Windows-specific signal/PID/dashboard issues in legacy modules), plus
   `test_json_store.py` cannot be collected on Windows (A27).
7. **Sandbox residual risk:** AST + subprocess is defense-in-depth, not a
   certified malicious-code sandbox; POSIX rlimits are unavailable on Windows.

## Manual runtime smoke commands (operator only — require explicit approval)

Not executed by CI or by automated tests:

```bash
curl -fsS http://172.29.72.68:18081/health
curl -fsS http://172.29.72.68:18081/v1/models
```

## Non-claims

This is a **paper-trading research platform**. It sends no real exchange orders
and holds no funds. Nothing here claims or implies profitability; model output
is labelled hypothesis unless backed by stored evidence ids.
