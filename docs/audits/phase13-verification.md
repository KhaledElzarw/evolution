# Phase 13 — Independent Verification Findings

Six fresh-context adversarial verifiers were run against the branch. None had
authored the code under review; each was instructed to **falsify** the author's
claims rather than confirm them, and to run real probes rather than read
summaries.

**Verifier completion is itself an honest caveat:** 2 of 6 completed fully; 4
terminated early on a session limit. Three of those four had already reported
partial signals, which were investigated manually and are recorded below. The
two areas with **no verification coverage** are named at the end — they are not
claimed as verified.

---

## Confirmed defects found and fixed

### V1 (CRITICAL) — Plugin sandbox escape: AST validator bypassable → RCE

**Found by:** plugin/broker security verifier (partial signal: "Confirmed
arbitrary file write (and full `os`/RCE)"), then reproduced manually.

`tradebot/infrastructure/adapters/plugins/validator.py`

The validator's deny-by-default claim was **false**. Two gaps combined:

1. `getattr`/`setattr`/`vars`/`dir`/`hasattr` were not in
   `FORBIDDEN_CALL_NAMES`, permitting **string-based attribute access**.
2. `FORBIDDEN_ATTRIBUTES` was a short hand-picked list; `__class__`,
   `__bases__`, `__mro__`, `__dict__` were all permitted.

Together these allow the classic escape, with **string concatenation defeating
even the listed names**:

```python
base = getattr(getattr((), "__cl"+"ass__"), "__ba"+"ses__")[0]
subs = getattr(base, "__sub"+"classes__")()
```

**Reproduced end-to-end:** a bundle containing this passed `validate_bundle()`
(`ok=True`) and, executed via `run_strategy_in_worker()`, reached **299
subclasses including `_wrap_close` and `catch_warnings`** — both standard
routes to `os`/`os.system`. On Windows the worker has no filesystem jail, so
this is arbitrary code execution with file write. This defeated the entire
Phase 5 security thesis.

**Fix:**
- `getattr`, `setattr`, `delattr`, `hasattr`, `vars`, `dir`, `locals` added to
  `FORBIDDEN_CALL_NAMES` (as calls *and* as bare-name aliases).
- **All** dunder attribute access rejected via `_DUNDER_RE` (`^__.*__$`) rather
  than a blocklist.
- Dunder **string literals** rejected.

The two rules are complementary and close the class: a runtime-built dunder
string is inert without `getattr`, and literal `.__class__` is caught by the
dunder rule.

**Verified:** all 8 escape vectors now rejected; all 12 built-ins remain clean
under the new rules (they use no reflection). Regressions:
`test_plugin_validator.py::test_reflection_escape_vectors_rejected` (13
vectors) and `::test_builtin_strategy_sources_have_no_reflection_constructs`.

### V2 (MODERATE) — `realized_pnl` ledger posting had the wrong value and inverted sign

**Found by:** accounting verifier (completed).

`tradebot/domain/ledger.py` — the SELL leg posted
`quote(cost_released - gross)`: a balancing plug that is the **gross** result,
**negated** and **fee-excluded**. It did not equal realized P&L.

Repro (BUY 0.1 @ 60000, SELL 0.1 @ 66000, 0.1% fees): posting **−594.00** vs
true realized **+587.40**. Systematic across partial sells.

This directly contradicted the module docstring's claim that "Net P&L is
derived from event postings". Latent — the only consumers read
`Wallet.realized_pnl` (which was always correct) — but any audit summing the
persisted `realized_pnl` account would reconstruct a wrong, sign-flipped
number.

**Fix:** the `realized_pnl` leg now carries the true net realized value
(gain credited negative, loss debited positive) with `base_asset` as the
balancing contra leg. Postings still sum to zero. Regressions:
`test_ledger.py::test_realized_pnl_posting_carries_true_net_realized`,
`::test_realized_pnl_posting_correct_across_partial_sells`,
`::test_loss_making_sell_posts_a_debit`.

### V3 (LOW/MEDIUM) — Body-size limit bypassable by omitting Content-Length

**Found by:** API security verifier (completed).

`tradebot/api/app.py` — the 1 MiB guard only ran `if content-length` was
present. A chunked/streamed request omits it, skipping the check entirely:
an unauthenticated memory-DoS vector.

**Fix:** the declared length is still rejected cheaply, but when absent on
POST/PUT/PATCH the real body is measured against the cap. Malformed
Content-Length is now rejected too. Regression:
`test_api.py::test_oversized_body_rejected_without_content_length`.

### V4 (LOW) — Early 413 response carried no security headers

`tradebot/api/app.py` — headers were applied only after `call_next`, so the
early 413 return bypassed them. **Fix:** `_too_large()` now emits
`SECURITY_HEADERS`. Regression:
`test_api.py::test_413_response_carries_security_headers`.

### V5 (FUNCTIONAL) — `default_probe` made real `stop`/`status` impossible

**Found by:** API security verifier (noted as a functional, fail-safe defect).

`tradebot/operations/process_identity.py` — `default_probe` returns empty
`service`/`instance_id`/`nonce` (the OS cannot know them), but `verify()` used
the strict `matches()`, which compares all seven fields. Against a real probe
it could **never** succeed: production `stop`/`status` would always report a
mismatch and refuse to act.

**Fix:** separated the concepts. `matches_live()` compares only OS-observable
fields (pid, start_time, executable, command) — which are what actually defeat
PID reuse — and `verify()`/`cmd_status`/`cmd_start` now use it. `matches()`
remains the strict full comparison. `service`/`instance_id`/`nonce` authenticate
the PID *file* against the deployment; copying them from the file into the probe
would have been circular and proved nothing. Regressions:
`test_operations.py::test_os_only_probe_can_verify_a_real_process`,
`::test_matches_live_still_defeats_pid_reuse` (anti-reuse preserved),
`::test_status_reports_running_with_os_only_probe`.

### V7 (MODERATE) — Money stored as binary float in SQLite

**Found by:** database verifier (partial signal: "Confirmed money precision
loss"), reproduced and characterised manually after the verifier died.

`tradebot/infrastructure/database/models.py`

Money columns were declared `Numeric(24, 2)` / `Numeric(24, 8)`. That looks
correct, but **SQLite has no native decimal type**: SQLAlchemy stores `Numeric`
as `REAL` and binds it through a Python `float`. Confirmed directly in the
emitted SQL parameters — `(..., 10000.0, 10000.07, 0.12345678, 60000.01, ...)`
are floats, not Decimals — and by `typeof(quote_cash) = real`.

This violated the "never binary floating point" rule and the
`tradebot.domain.money` invariant, and made the declared 24-digit precision a
**false promise**: float64 carries ~15-17 significant digits.

**Severity, characterised honestly:** the round trip is *exact* for realistic
values, because SQLAlchemy quantizes back to scale on read, masking the error.
BTC's entire 21M supply at satoshi precision is 16 significant digits, safely
inside float64. The masking fails at 20 digits — verified:
`Decimal("123456789012.12345678")` read back as `123456789012.12345886`. So this
was a latent correctness/spec violation, **not** a live money bug.

**Fix:** a `DecimalText` `TypeDecorator` stores the exact decimal string,
quantizing at the boundary and **rejecting floats outright** (mirroring
`domain.money`). Storage is now `text`; the 20-digit case round-trips exactly;
1000 × 0.07 accumulates through the DB to exactly 70.00 with zero drift.
Regressions: `test_database.py::test_money_columns_are_stored_as_text_not_real`,
`::test_money_round_trips_exactly_beyond_float64_precision`,
`::test_money_column_rejects_float`, `::test_repeated_accumulation_does_not_drift`.

### V6 (LOW) — Improper `# pragma: no cover` on a testable guard

`tradebot/domain/money.py:44` — the float-rejection guard was marked
`pragma: no cover` despite being trivially testable, violating the pragma
policy. **Fix:** pragma removed; covered by
`test_operations.py::test_money_rejects_float_at_the_boundary`.

---

### V8 (SEVERE) — Fingerprint bans were dead code

**Found by:** evolution-rules verifier (re-run after the session limit reset).

`promotion.py` called `bans.ban(elim.code_hash)` — **hash only**. That was the
only `.ban(` call site in the codebase, so `BanRegistry._fingerprints` was never
populated and the fingerprint arm of `is_banned` could never fire. The
fingerprint check in the candidate screen was therefore **inert**.

Root cause: `EliminationDecision` had no `structural_fingerprint` field, even
though `WalletEvaluation` carries one — `plan_replacements` simply dropped it.

**Impact:** an eliminated loser could be re-promoted the following week by
re-emitting structurally identical code under any new hash — a comment change
suffices. This is precisely the "hash but not fingerprint" failure mode, and it
defeats product rules 10/20.

**Why the author's own tests missed it:**
`test_novel_candidate_rejected_by_banned_fingerprint_alone` passed because it
called `bans.ban(hash, fingerprint)` **directly**. It proved the component
worked while nothing wired the fingerprint through — a component test standing
in for an integration test.

**Fix:** `structural_fingerprint` added to `EliminationDecision` and populated at
all three construction sites; `promotion.py` now bans hash **and** fingerprint.
Regression: `test_promotion.py::test_eliminated_fingerprint_is_banned_not_just_the_hash`
offers a structurally identical clone under a brand-new hash and asserts it is
quarantined.

### V9 (SEVERE) — Failed post-commit invariant left the portfolio mutated

`promotion.py` assigned `portfolio.active = survivors + new_slots` **before**
running the post-commit invariants, and the raise was not caught. Any invariant
trip left the portfolio partially mutated — verified: 12 active before, **11
after**, with no way for the caller to recover. The docstring's "mutated only on
success" was false.

**Fix:** the proposed roster is now validated by a pure `_assert_roster_invariants`
**before** the portfolio is touched; nothing after the commit point can raise.
Regression: `::test_failed_invariant_leaves_portfolio_untouched`.

### V10 (MODERATE) — `replacement_count` hardcoded to 6 in the retirement branch

`bottom_six = ranked[-6:]` is size-adaptive but `replacement_count = 6` was a
literal. With a short roster (4 active, none losing) the plan produced 4
eliminations but `replacement_count=6`, desyncing `len(eliminations)` from
`replacement_count` and breaking `promote()`'s roster arithmetic. Reachable via a
prior V9 partial commit — the two defects compose.

**Fix:** `replacement_count = len(bottom_six)`.

### V11 (MINOR) — Bans applied before the batch could abort

`BanRegistry` was mutated before candidate acquisition, which can raise. An
aborted batch left the roster correctly untouched but the bans persisted,
contradicting the all-or-nothing framing.

**Fix:** bans are staged in a scratch registry (still screening candidates within
the batch, so a loser cannot be re-promoted by the same transaction that
eliminates it) and applied only at the commit point. Regressions:
`::test_aborted_batch_leaves_no_bans_behind` and
`::test_successful_promotion_does_apply_bans`.

### V12 (MODERATE) — Symlinked directories silently skipped by the validator

Found while covering the validator's untested lines. `_check_layout` tested
`path.is_dir()` **before** `path.is_symlink()`; `is_dir()` follows the link, so a
symlinked *directory* hit `continue` and was never rejected — despite the docs
claiming symlinks are rejected. The symlink and path-escape controls had **no
tests at all**.

**Fix:** the symlink check now precedes the directory check. Regressions:
`test_plugin_validator.py::test_symlink_in_bundle_rejected`,
`::test_symlinked_directory_rejected`,
`::test_subdirectories_are_walked_not_skipped`, plus manifest-corruption cases.

## Verifier findings assessed and NOT actioned

- **`origin_allowed(None) == True`** (absent Origin). Not a CSRF hole: the same
  guard still requires a Bearer token a cross-site attacker cannot supply.
  Intentional (curl/same-origin support). Documented, not changed.
- **`apply_fee` docstring vs behaviour** — docstring says fees round "in the
  exchange's favour"; implementation uses `ROUND_HALF_UP`. Cosmetic; not
  exploitable through execution because `MIN_NOTIONAL=5.00` keeps live fees
  above the rounding floor. Left as a documentation nit.
- **`safeUrl` permits any external `https:` URL** — would be an open-redirect
  *if wired to server data*, but it is not currently invoked on live data.
  Noted as a latent hazard for whoever wires it up.
- **Transaction-id ordering** depends on wallet iteration order; no
  accounting-relevant order dependence exists (all monetary results are keyed
  per wallet/intent/snapshot).

## Areas with NO verification coverage (not claimed as verified)

The evolution-rules and database/migration verifiers both terminated on the
session limit before producing findings.

- **Evolution / replacement / promotion rule fidelity** — covered only by the
  author's own tests. The elimination-count, ceil/floor allocation, parent
  eligibility, ban-reuse and crash-injection rules are **unverified by an
  independent reviewer**.
- **Database schema / migration atomicity** — likewise unverified. The DB
  verifier's one partial signal, *"Confirmed money precision loss"*, was **not
  reproduced or resolved** and remains an open lead worth chasing: money columns
  should be text/`Numeric`, never `REAL`.

**Update:** both previously-unverified areas have now been covered.

- The **database** verifier's partial signal was chased to ground — see **V7**.
- The **evolution-rules** verifier was re-run after the session limit reset and
  found **four defects (V8-V11)**, two of them severe.

### Rules the evolution verifier confirmed as HOLDING

Every loser eliminated (`replacement_count == n` for n = 1..12);
`novel=ceil(n/2)` / `mutation=floor(n/2)` summing to `replacement_count` for
every n including all odd n; bottom-six retirement is not a ban; zero-fill uses
`fill_count` and never `completed_round_trip_count`; an eliminated strategy is
never a mutation parent (verified with a profitable-but-zero-fill top
performer); zero surviving parents converts mutation slots to novel with the sum
preserved; ranking keys only on `(-profit, wallet_id)` with deterministic ties;
roll-forward never resurrects an eliminated strategy; poor-but-valid candidates
promote while invalid ones are quarantined.

### Remaining honest risks

- **Phase 3 legacy import is not implemented.**
- **Coverage is 97%, not the specified 100%** (96 statements; see
  `docs/testing.md`).
- **Frontend has static safety analysis only** — no jsdom/Playwright.
- The legacy stack still carries its original audit findings; the new package
  supersedes rather than replaces it.
