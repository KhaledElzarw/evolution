import argparse
import json
from pathlib import Path

import sqlite_store


BASE_DIR = Path(__file__).resolve().parent
SNAPSHOT_FILES = {
    "state": BASE_DIR / "state.json",
    "runtime_state": BASE_DIR / "runtime_state.json",
    "engine_status": BASE_DIR / "engine_status.json",
    "cumulative": BASE_DIR / "cumulative.json",
    "ai_signal": BASE_DIR / "ai_signal.json",
}
TRADES_PATH = BASE_DIR / "trades.jsonl"
HISTORY_PATH = BASE_DIR / "dashboard_history.json"


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def migrate(*, db_path: str | Path | None = None) -> dict:
    sqlite_store.init_db(db_path)
    imported = {"snapshots": 0, "events": 0, "history": 0}

    for key, source in SNAPSHOT_FILES.items():
        payload = _read_json(source)
        if payload:
            sqlite_store.write_snapshot(key, payload, path=db_path)
            imported["snapshots"] += 1

    existing_events = {json.dumps(event, sort_keys=True) for event in sqlite_store.list_events(path=db_path)}
    for event in _read_jsonl(TRADES_PATH):
        signature = json.dumps(event, sort_keys=True)
        if signature in existing_events:
            continue
        sqlite_store.append_event(event, path=db_path)
        existing_events.add(signature)
        imported["events"] += 1

    history = _read_json(HISTORY_PATH)
    items = history.get("items") if isinstance(history, dict) else []
    if isinstance(items, list):
        imported["history"] = sqlite_store.import_history_items(items, path=db_path)

    return imported


def main() -> None:
    parser = argparse.ArgumentParser(description="Import existing tradebot runtime JSON/JSONL files into SQLite WAL storage.")
    parser.add_argument("--db", default=None, help="SQLite database path. Defaults to TRADEBOT_DB_PATH or tradebot.sqlite3.")
    args = parser.parse_args()
    result = migrate(db_path=args.db)
    print(json.dumps({"ok": True, "db": str(sqlite_store.db_path() if args.db is None else Path(args.db)), **result}, indent=2, sort_keys=True))


if __name__ == "__main__":  # pragma: no cover - script entrypoint
    main()
