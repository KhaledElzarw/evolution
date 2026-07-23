"""Versioned atomic JSON snapshot of the live trading state.

The DB schema in ``infrastructure/database`` remains the long-term ledger
store; this snapshot is the pragmatic restart-survival layer: one JSON file,
written atomically (temp + ``os.replace``, the A22 pattern), containing
``TickEngine.snapshot_state()`` plus a schema version and timestamps.

Load is strict: wrong version, corrupt JSON, or a snapshot too old to bridge
with a single klines fetch is REJECTED loudly (returns None with a reason) —
a fresh backfill replay is always safer than trading on misread state.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path

SCHEMA_VERSION = 1

#: A 5m-candle snapshot older than this cannot be bridged by one 1000-bar
#: fetch, so it is discarded in favour of a fresh backfill replay.
MAX_RESUME_GAP_MS = 1000 * 300_000


def save(path: str | Path, engine_state: dict, *, extras: dict | None = None) -> None:
    """Atomically write the snapshot; a crash mid-write leaves the old file."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "saved_at_utc": dt.datetime.now(dt.timezone.utc)
        .replace(tzinfo=None).isoformat() + "Z",
        "engine": engine_state,
        "extras": extras or {},
    }
    fd, tmp = tempfile.mkstemp(dir=str(target.parent),
                               prefix=target.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load(path: str | Path, *,
         now_ms: int | None = None) -> tuple[dict | None, str]:
    """Return ``(payload, reason)`` — payload is None when unusable.

    ``reason`` explains a rejection ("missing", "corrupt: ...",
    "version N != M", "too old: ...") or is "ok".
    """

    target = Path(path)
    if not target.is_file():
        return None, "missing"
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"corrupt: {type(exc).__name__}"
    version = payload.get("schema_version")
    if version != SCHEMA_VERSION:
        return None, f"version {version} != {SCHEMA_VERSION}"
    engine = payload.get("engine")
    if not isinstance(engine, dict) or "wallets" not in engine:
        return None, "corrupt: no engine state"
    if now_ms is not None:
        last = engine.get("last_processed_open_ms", -1)
        if last < 0 or now_ms - last > MAX_RESUME_GAP_MS:
            age_h = (now_ms - last) / 3_600_000 if last >= 0 else float("inf")
            return None, f"too old: {age_h:.1f}h behind (> fetchable gap)"
    return payload, "ok"
