import sqlite3

import sqlite_store


def test_schema_initializes_with_wal(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    sqlite_store.init_db(db)

    with sqlite3.connect(db) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        schema_version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]

    assert journal_mode.lower() == "wal"
    assert schema_version == sqlite_store.SCHEMA_VERSION


def test_snapshot_key_for_path_maps_runtime_snapshot_filenames(tmp_path):
    assert sqlite_store.snapshot_key_for_path(tmp_path / "state.json") == "state"
    assert sqlite_store.snapshot_key_for_path("runtime_state.json") == "runtime_state"
    assert sqlite_store.snapshot_key_for_path("/tmp/engine_status.json") == "engine_status"
    assert sqlite_store.snapshot_key_for_path("cumulative.json") == "cumulative"
    assert sqlite_store.snapshot_key_for_path("ai_signal.json") == "ai_signal"
    assert sqlite_store.snapshot_key_for_path("unknown.json") is None


def test_read_snapshot_defaults_and_presence_are_distinct(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    assert sqlite_store.has_snapshot("missing", path=db) is False
    assert sqlite_store.read_snapshot("missing", {"enabled": True}, path=db) == {"enabled": True}

    sqlite_store.write_snapshot("empty", {}, path=db)

    assert sqlite_store.has_snapshot("empty", path=db) is True
    assert sqlite_store.read_snapshot("empty", {"fallback": True}, path=db) == {"fallback": True}


def test_read_snapshot_falls_back_for_empty_corrupt_or_non_dict_payloads(tmp_path):
    db = tmp_path / "tradebot.sqlite3"
    sqlite_store.init_db(db)

    with sqlite_store.connect(db) as conn:
        conn.executemany(
            "INSERT INTO kv(key, payload_json, updated_at_utc) VALUES(?, ?, ?)",
            [
                ("empty_raw", "", "2026-01-01T00:00:00+00:00"),
                ("corrupt", "{", "2026-01-01T00:00:00+00:00"),
                ("non_dict", "[1, 2, 3]", "2026-01-01T00:00:00+00:00"),
            ],
        )

    assert sqlite_store.read_snapshot("empty_raw", {"fallback": 1}, path=db) == {"fallback": 1}
    assert sqlite_store.read_snapshot("corrupt", {"fallback": 2}, path=db) == {"fallback": 2}
    assert sqlite_store.read_snapshot("non_dict", {"fallback": 3}, path=db) == {"fallback": 3}


def test_update_snapshot_starts_from_empty_payload(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    updated = sqlite_store.update_snapshot(
        "runtime_state",
        lambda current: {"seen": current, "enginePid": 123},
        path=db,
    )

    assert updated == {"seen": {}, "enginePid": 123}
    assert sqlite_store.read_snapshot("runtime_state", path=db) == updated


def test_snapshot_round_trip_and_atomic_patch(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    sqlite_store.write_snapshot("state", {"paused": False, "count": 1}, path=db)
    patched = sqlite_store.update_snapshot(
        "state",
        lambda current: {**current, "paused": True, "count": current["count"] + 1},
        path=db,
    )

    assert patched == {"paused": True, "count": 2}
    assert sqlite_store.read_snapshot("state", path=db) == patched


def test_events_append_and_read_in_insertion_order(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    sqlite_store.append_event({"tsUtc": "2026-01-01T00:00:02+00:00", "event": "EXIT"}, path=db)
    sqlite_store.append_event({"tsUtc": "2026-01-01T00:00:01+00:00", "event": "ENTER"}, path=db)

    assert [row["event"] for row in sqlite_store.list_events(path=db)] == ["EXIT", "ENTER"]
    assert [row["event"] for row in sqlite_store.list_events(limit=1, path=db)] == ["ENTER"]


def test_list_events_can_include_database_ids_with_limited_results(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    first_id = sqlite_store.append_event(
        {"ts_utc": "2026-01-01T00:00:00+00:00", "event": "A"},
        path=db,
    )
    second_id = sqlite_store.append_event(
        {"tsUtc": "2026-01-01T00:00:01+00:00", "event": "B"},
        path=db,
    )
    third_id = sqlite_store.append_event({"event": "C"}, path=db)

    assert [event["_eventId"] for event in sqlite_store.list_events(path=db, include_ids=True)] == [
        first_id,
        second_id,
        third_id,
    ]
    assert sqlite_store.list_events(limit=2, path=db, include_ids=True) == [
        {"tsUtc": "2026-01-01T00:00:01+00:00", "event": "B", "_eventId": second_id},
        {"event": "C", "_eventId": third_id},
    ]


def test_import_history_items_accepts_timestamp_aliases_and_skips_missing_ts(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    count = sqlite_store.import_history_items(
        [
            {"ts": "2026-01-01T00:00:00+00:00", "price": 100.0, "equity": 1000.0},
            {"tsUtc": "2026-01-01T00:00:01+00:00", "price": 101.0, "equity": 1001.0},
            {"ts_utc": "2026-01-01T00:00:02+00:00", "price": 102.0, "equity": 1002.0},
            {"price": 103.0, "equity": 1003.0},
        ],
        path=db,
    )

    assert count == 3
    assert [item["ts"] for item in sqlite_store.read_history(path=db)["items"]] == [
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:01+00:00",
        "2026-01-01T00:00:02+00:00",
    ]


def test_history_upsert_keeps_latest_value(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    sqlite_store.upsert_history_item("2026-01-01T00:00:00+00:00", 100.0, 101.0, path=db)
    sqlite_store.upsert_history_item("2026-01-01T00:00:00+00:00", 102.0, 103.0, path=db)

    assert sqlite_store.read_history(path=db)["items"] == [
        {"ts": "2026-01-01T00:00:00+00:00", "price": 102.0, "equity": 103.0}
    ]


def test_read_history_limit_returns_recent_items_in_ascending_order(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    for idx in range(4):
        sqlite_store.upsert_history_item(
            f"2026-01-01T00:00:0{idx}+00:00",
            idx,
            idx,
            path=db,
        )

    assert sqlite_store.read_history(limit=2, path=db)["items"] == [
        {"ts": "2026-01-01T00:00:02+00:00", "price": 2.0, "equity": 2.0},
        {"ts": "2026-01-01T00:00:03+00:00", "price": 3.0, "equity": 3.0},
    ]


def test_compact_retains_recent_events_and_history(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    for idx in range(5):
        sqlite_store.append_event({"tsUtc": f"2026-01-01T00:00:0{idx}+00:00", "event": f"E{idx}"}, path=db)
        sqlite_store.upsert_history_item(f"2026-01-01T00:00:0{idx}+00:00", 100.0 + idx, 1000.0 + idx, path=db)

    result = sqlite_store.compact(event_keep=2, history_keep=3, path=db)

    assert result == {"eventsDeleted": 3, "historyDeleted": 2}
    assert [row["event"] for row in sqlite_store.list_events(path=db)] == ["E3", "E4"]
    assert [row["price"] for row in sqlite_store.read_history(path=db)["items"]] == [102.0, 103.0, 104.0]


def test_compact_with_zero_keep_removes_all_events_and_history(tmp_path):
    db = tmp_path / "tradebot.sqlite3"

    for idx in range(2):
        sqlite_store.append_event({"tsUtc": f"2026-01-01T00:00:0{idx}+00:00", "event": f"E{idx}"}, path=db)
        sqlite_store.upsert_history_item(
            f"2026-01-01T00:00:0{idx}+00:00",
            100.0 + idx,
            1000.0 + idx,
            path=db,
        )

    result = sqlite_store.compact(event_keep=0, history_keep=0, path=db)

    assert result == {"eventsDeleted": 2, "historyDeleted": 2}
    assert sqlite_store.list_events(path=db) == []
    assert sqlite_store.read_history(path=db)["items"] == []
