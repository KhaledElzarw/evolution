# Threat Model

Scope: the evolutionary multi-wallet paper-trading platform. This is a research
simulator — it sends **no real exchange orders** and holds no real funds.

## Trust boundaries

| Zone | Trust | Boundary control |
|------|-------|------------------|
| Core services (engine, API, scheduler, evolution, promotion) | trusted | authored + reviewed code |
| Generated strategy plugins | **untrusted** | AST validation + subprocess sandbox |
| Local LLM (Qwen via llama.cpp) | **untrusted output** | schema-validated, degrade-not-raise |
| External data sources | **untrusted content** | DataBroker allowlist + sanitization |
| Operator (dashboard/CLI) | authenticated | token, fail-closed mutations |

## Adversaries and mitigations

### 1. Malicious generated strategy code (primary threat)

The local model autonomously writes strategy plugins. Treated as hostile.

- **Never imported into a core process.** Runs only in a `python -I -E`
  subprocess with sanitized environment, temp cwd, hard timeout, POSIX rlimits
  where available. ([strategy-plugin-sdk.md](strategy-plugin-sdk.md))
- AST deny-by-default import policy; `eval`/`exec`/`compile`/`__import__`/`open`
  and reflection escapes rejected before execution.
- No handle to DB, network, credentials, filesystem, other wallets, or core
  objects exists in the plugin's context by construction.
- Timeout/crash/malformed-output/prohibited-access → structured failure →
  quarantine after strikes.
- **Residual risk (documented):** AST + subprocess is strong defense-in-depth,
  not a proven-complete malicious-code sandbox. On Windows rlimits are
  unavailable and the wall-clock timeout is the backstop. An OS sandbox
  (bubblewrap on Linux) is the recommended hardening layer.

### 2. SSRF via the model's data requests (A09)

The model names a `source_id`; it never supplies a raw URL and cannot mutate
the allowlist. Every URL and every redirect is revalidated: exact
scheme/host/port/method/path, HTTPS-only (bar the one local-llm HTTP
exception), userinfo rejected, DNS-resolved private/link-local/loopback/
multicast/metadata IPs blocked (defeats rebinding). ([data-broker.md](data-broker.md))

### 3. Prompt injection via external content (A18–A20)

External text is **data, never instructions**. HTML/XML is sanitized (scripts,
forms, comments, tags stripped); secondary news is flagged and requires primary
confirmation. LLM analytical claims must cite stored evidence ids or be labelled
hypotheses (schema-enforced). Fabricated news/macro/equity history is
structurally prevented on the new path.

### 4. Dashboard/API attacker (A12–A14)

- Mutations fail **closed** (token required; wrong/missing → 401, cross-origin
  → 403, token-in-query → 400).
- Errors redacted to a generic message + correlation id; internals only in
  structured logs (A13).
- Frontend renders untrusted data via `textContent`; links vetted with `URL`
  (https/loopback-http only) + `noopener noreferrer` (A14). No model-endpoint
  or allowlist editing in the UI (A09).
- Loopback bind by default; remote bind refuses startup without a ≥32-char
  token. CSP/nosniff/DENY/no-store on every response.

### 5. Operator tooling stopping the wrong process (A15)

Process supervision signals only on a full identity match (pid + OS start time
+ executable + command + service + instance id + nonce). Recycled PIDs and
stale PID files are never signalled; escalation re-verifies after the grace
window.

### 6. Accounting corruption

Fixed-point Decimal throughout; balanced double-entry journal; per-wallet
isolation makes cross-wallet postings structurally impossible; A01/A02
regressions locked by tests.

## Non-goals

- Not hardened against a compromised host OS or a malicious operator.
- Not a custody system — no real keys, funds, or exchange credentials in the
  paper path.
- The subprocess sandbox is not certified against a determined native-code
  escape; see residual risk above.

## Secrets

Broker/API credentials come from environment/secret storage, never dashboard
state, never model prompts, never logs (redacted by key pattern). No secrets are
tracked in git (CI Gate 1 + Gate 3 scan).
