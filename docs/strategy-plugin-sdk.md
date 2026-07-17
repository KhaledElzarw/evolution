# Strategy Plugin SDK (plugin-sdk-v1)

Generated strategy code is **untrusted**. This document defines the contract
and the enforced isolation boundary (audit finding A05).

## Bundle layout

```
bundle/
  manifest.json     # required
  strategy.py       # required — must define create_strategy()
  parameters.json   # optional
  tests/            # optional generated tests
  README.md         # optional
```

Limits: ≤ 16 files, ≤ 256 KiB total. Symlinks, absolute paths, and path
traversal are rejected at validation time.

## Manifest (manifest-v1)

Required fields: `schema_version`, `strategy_id`, `strategy_version_id`,
`name`, `family`, `origin` (`builtin|novel|mutation|dark_horse`),
`required_intervals`, `min_warmup_candles`, `supported_symbol` (**must be
`BTCUSDT`**), `code_hash`.

## Contract

```python
class StrategyPlugin(Protocol):
    def metadata(self) -> StrategyMetadata: ...
    def initialize(self) -> dict: ...                       # opaque JSON state
    def on_market_snapshot(self, context, state) -> StrategyDecision: ...
```

The plugin receives a `StrategyContext` (immutable `MarketSnapshot`, read-only
`WalletView` of **its own wallet only**, trailing closed candles) and returns a
`StrategyDecision` of `IntentSpec`s plus updated opaque state. The core
execution service owns final validation and fill simulation — a plugin cannot
place orders directly.

A plugin never receives: database handles, repositories, other wallets' state,
environment variables, filesystem paths, HTTP clients, credentials, or process
handles.

## Import policy (AST-enforced)

Allowed: `math`, `statistics`, `decimal`, `dataclasses`, `enum`, `typing`,
`collections`, `itertools`, `functools`, and the four SDK domain modules.

Rejected (non-exhaustive; **deny by default**): `os`, `sys`, `subprocess`,
`socket`, `requests`, `httpx`, `urllib`, `pathlib`, `shutil`, `ctypes`,
`multiprocessing`, relative imports, `eval`/`exec`/`compile`/`__import__`/
`open` (including un-called aliases), and reflection escapes
(`__subclasses__`, `__globals__`, `__code__`, …).

## Runtime isolation

Plugins **never load into the engine/API/scheduler process**. Each tick runs in
a fresh subprocess:

- `python -I` (isolated mode; no user site, inherited `PYTHONPATH` ignored —
  the worker bootstraps only the SDK path from its own location),
- sanitized environment (only `SYSTEMROOT`/`SYSTEMDRIVE`/`TEMP`/`TMP` pass
  through; proven by `test_worker_env_is_sanitized`),
- temporary working directory,
- hard wall-clock timeout (kill on expiry),
- typed one-shot JSON-over-stdio IPC; malformed output is a structured failure.

On POSIX, `RLIMIT_CPU` and `RLIMIT_AS` are additionally applied inside the
worker. **On Windows these rlimits are unavailable; the parent timeout is the
enforcement backstop.** AST policy + subprocess isolation is strong
defense-in-depth but is *not* claimed to be a complete malicious-code sandbox;
an optional OS-level sandbox (e.g. bubblewrap on Linux) is the recommended
hardening layer where available.

## Quarantine

Timeouts, crashes, malformed output, and prohibited-access attempts accumulate
strikes (default limit 3). At the limit the strategy version is quarantined:
never scheduled again and treated as technically invalid by promotion.
Quarantine is permanent for that version; a clean run resets strikes but never
un-quarantines.
