import json
import os
from pathlib import Path
from typing import Callable

# fcntl is Unix-only; on Windows the equivalent byte-range lock lives in
# msvcrt. Resolving this at import time keeps update_json_locked working (and
# importable) on both platforms.
try:
    import fcntl

    def _lock_exclusive(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)

    def _unlock(f) -> None:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
except ImportError:  # Windows
    import msvcrt

    def _lock_exclusive(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)

    def _unlock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


def atomic_write_json(path: str | Path, payload: dict) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(target)


def update_json_locked(path: str | Path, updater: Callable[[dict], dict]) -> dict:
    target = Path(path)
    lock_path = target.with_suffix(target.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock:
        _lock_exclusive(lock)
        try:
            try:
                current = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
            except Exception:
                current = {}
            updated = updater(dict(current))
            atomic_write_json(target, updated)
            return updated
        finally:
            _unlock(lock)
