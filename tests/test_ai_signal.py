from datetime import datetime, timedelta, timezone

import engine


def test_read_ai_decision_marks_expired_signal_stale(monkeypatch):
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
    monkeypatch.setattr(
        engine,
        "_read_ai_signal",
        lambda: {
            "tsUtc": old_ts,
            "confidence": "0.7",
            "breakoutRisk": "0.2",
            "recommendedSpacingPct": "0.5",
            "recommendedLevels": "999",
            "recommendedMaxExposurePct": "-1",
            "recommendedMode": "nonsense",
            "gridAllowed": True,
        },
    )

    signal = engine._read_ai_decision_for_engine(
        {"aiEnabled": True, "aiPollSeconds": 60, "gridSpacingPct": 0.008, "gridLevels": 12, "gridMode": "scalpy"}
    )

    assert signal["stale"] is True
    assert signal["source"] == "expired_signal"
    assert signal["recommendedSpacingPct"] == 0.03
    assert signal["recommendedLevels"] == 24
    assert signal["recommendedMaxExposurePct"] == 0.05
    assert signal["recommendedMode"] == "scalpy"


def test_read_ai_decision_disabled():
    assert engine._read_ai_decision_for_engine({"aiEnabled": False}) == {"enabled": False, "source": "disabled"}


def test_read_ai_decision_missing_signal_is_stale(monkeypatch):
    monkeypatch.setattr(engine, "_read_ai_signal", lambda: {})

    assert engine._read_ai_decision_for_engine({"aiEnabled": True}) == {
        "enabled": True,
        "stale": True,
        "source": "missing_signal",
    }


def test_read_ai_decision_invalid_timestamp_marks_signal_stale(monkeypatch):
    monkeypatch.setattr(
        engine,
        "_read_ai_signal",
        lambda: {"enabled": True, "tsUtc": "not-a-date", "riskAction": "allow_grid"},
    )

    signal = engine._read_ai_decision_for_engine({"aiEnabled": True})

    assert signal["stale"] is True
    assert signal["source"] == "invalid_signal_ts"


def test_read_ai_decision_normalizes_new_risk_fields(monkeypatch):
    now_ts = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(
        engine,
        "_read_ai_signal",
        lambda: {
            "enabled": True,
            "tsUtc": now_ts,
            "confidence": "0.9",
            "riskAction": "sells_only",
            "pauseNewBuys": "true",
            "allowSellsOnly": "true",
            "flattenRecommended": "false",
            "reduceExposure": "true",
            "riskBudgetPct": "0.2",
            "dryRun": "false",
            "shadowMode": "false",
        },
    )

    signal = engine._read_ai_decision_for_engine(
        {"aiEnabled": True, "aiPollSeconds": 60, "gridSpacingPct": 0.008, "gridLevels": 12, "gridMode": "scalpy"}
    )

    assert signal["riskAction"] == "sells_only"
    assert signal["pauseNewBuys"] is True
    assert signal["allowSellsOnly"] is True
    assert signal["reduceExposure"] is True
    assert signal["riskBudgetPct"] == 0.2
    assert engine._ai_controls_active(signal) is True
