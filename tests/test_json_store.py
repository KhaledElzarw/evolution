import json

from json_store import update_json_locked


def test_update_json_locked_round_trip(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"paused": False, "count": 1}), encoding="utf-8")

    result = update_json_locked(path, lambda current: {**current, "paused": True, "count": current["count"] + 1})

    assert result == {"paused": True, "count": 2}
    assert json.loads(path.read_text(encoding="utf-8")) == result


def test_update_json_locked_recovers_from_corrupt_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{bad json", encoding="utf-8")

    result = update_json_locked(path, lambda current: {"recoveredFrom": current})

    assert result == {"recoveredFrom": {}}
    assert json.loads(path.read_text(encoding="utf-8")) == result
