import json

import dashboard_data
import sqlite_store


def _isolate_sqlite_db(monkeypatch, tmp_path):
    monkeypatch.setenv("TRADEBOT_DB_PATH", str(tmp_path / "tradebot.sqlite3"))


def test_read_json_prefers_sqlite_snapshot_over_json_mirror(tmp_path, monkeypatch):
    _isolate_sqlite_db(monkeypatch, tmp_path)
    mirror = tmp_path / "state.json"
    mirror.write_text(json.dumps({"paused": False}), encoding="utf-8")
    sqlite_store.write_snapshot("state", {"paused": True})

    payload = dashboard_data.DashboardDataAdapter().read_json(mirror)

    assert payload == {"paused": True}


def test_read_json_falls_back_to_json_mirror_and_ignores_bad_payloads(tmp_path, monkeypatch):
    _isolate_sqlite_db(monkeypatch, tmp_path)
    adapter = dashboard_data.DashboardDataAdapter()
    valid = tmp_path / "runtime_state.json"
    non_dict = tmp_path / "engine_status.json"
    bad_json = tmp_path / "cumulative.json"
    missing = tmp_path / "ai_signal.json"
    valid.write_text(json.dumps({"enginePid": 123}), encoding="utf-8")
    non_dict.write_text(json.dumps(["not", "a", "dict"]), encoding="utf-8")
    bad_json.write_text("{", encoding="utf-8")

    assert adapter.read_json(valid) == {"enginePid": 123}
    assert adapter.read_json(non_dict) == {}
    assert adapter.read_json(bad_json) == {}
    assert adapter.read_json(missing) == {}


def test_write_json_updates_sqlite_snapshot_and_json_mirror(tmp_path, monkeypatch):
    _isolate_sqlite_db(monkeypatch, tmp_path)
    mirror = tmp_path / "state.json"
    payload = {"count": 2, "paused": False}

    dashboard_data.DashboardDataAdapter().write_json(mirror, payload)

    assert sqlite_store.read_snapshot("state") == payload
    assert json.loads(mirror.read_text(encoding="utf-8")) == payload


def test_update_state_bootstraps_existing_state_mirror_and_persists_update(tmp_path, monkeypatch):
    _isolate_sqlite_db(monkeypatch, tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({"count": 1, "paused": False}), encoding="utf-8")
    seen = []

    def updater(current):
        seen.append(dict(current))
        return {**current, "count": current["count"] + 1, "paused": True}

    updated = dashboard_data.DashboardDataAdapter().update_state(state_path, updater)

    assert seen == [{"count": 1, "paused": False}]
    assert updated == {"count": 2, "paused": True}
    assert sqlite_store.read_snapshot("state") == updated
    assert json.loads(state_path.read_text(encoding="utf-8")) == updated


def test_update_state_ignores_corrupt_state_mirror_when_bootstrapping(tmp_path, monkeypatch):
    _isolate_sqlite_db(monkeypatch, tmp_path)
    state_path = tmp_path / "state.json"
    state_path.write_text("{", encoding="utf-8")

    updated = dashboard_data.DashboardDataAdapter().update_state(
        state_path,
        lambda current: {"seen": current},
    )

    assert updated == {"seen": {}}
    assert sqlite_store.read_snapshot("state") == updated


def test_read_events_prefers_sqlite_events_over_jsonl_mirror(tmp_path, monkeypatch):
    _isolate_sqlite_db(monkeypatch, tmp_path)
    trades_path = tmp_path / "trades.jsonl"
    trades_path.write_text(json.dumps({"event": "JSONL"}) + "\n", encoding="utf-8")
    event_id = sqlite_store.append_event({
        "tsUtc": "2026-01-01T00:00:00+00:00",
        "event": "SQLITE",
    })

    events = dashboard_data.DashboardDataAdapter().read_events(trades_path, limit=50)

    assert events == [{
        "tsUtc": "2026-01-01T00:00:00+00:00",
        "event": "SQLITE",
        "_eventId": event_id,
    }]


def test_read_events_falls_back_to_jsonl_with_line_number_ids(tmp_path, monkeypatch):
    _isolate_sqlite_db(monkeypatch, tmp_path)
    trades_path = tmp_path / "trades.jsonl"
    trades_path.write_text(
        "\n".join([
            json.dumps({"tsUtc": "2026-01-01T00:00:00+00:00", "event": "A"}),
            "",
            "{bad-json",
            json.dumps(["not", "an", "event"]),
            json.dumps({"tsUtc": "2026-01-01T00:00:01+00:00", "event": "B"}),
            json.dumps({"tsUtc": "2026-01-01T00:00:02+00:00", "event": "C"}),
        ])
        + "\n",
        encoding="utf-8",
    )

    events = dashboard_data.DashboardDataAdapter().read_events(trades_path, limit=2)

    assert events == [
        {"tsUtc": "2026-01-01T00:00:01+00:00", "event": "B", "_eventId": 5},
        {"tsUtc": "2026-01-01T00:00:02+00:00", "event": "C", "_eventId": 6},
    ]


def test_read_events_returns_empty_list_for_missing_or_unopenable_jsonl(tmp_path, monkeypatch):
    _isolate_sqlite_db(monkeypatch, tmp_path)
    trades_dir = tmp_path / "trades.jsonl"
    trades_dir.mkdir()
    adapter = dashboard_data.DashboardDataAdapter()

    assert adapter.read_events(tmp_path / "missing.jsonl") == []
    assert adapter.read_events(trades_dir) == []


def test_compact_delegates_to_sqlite_store(tmp_path, monkeypatch):
    _isolate_sqlite_db(monkeypatch, tmp_path)
    for idx in range(3):
        ts = f"2026-01-01T00:00:0{idx}+00:00"
        sqlite_store.append_event({"tsUtc": ts, "event": f"E{idx}"})
        sqlite_store.upsert_history_item(ts, 100.0 + idx, 1000.0 + idx)

    result = dashboard_data.DashboardDataAdapter().compact(event_keep=1, history_keep=2)

    assert result == {"eventsDeleted": 2, "historyDeleted": 1}
    assert [event["event"] for event in sqlite_store.list_events()] == ["E2"]
    assert [item["price"] for item in sqlite_store.read_history()["items"]] == [101.0, 102.0]
