import ai_schemas


def _decision(parsed, state=None):
    return ai_schemas.decision_from_parsed(
        state={"aiMinConfidence": 0.55, "gridMaxExposurePct": 0.35} | (state or {}),
        payload={
            "symbol": "BTCUSDT",
            "interval": "1m",
            "price": 100.0,
            "atrPct": 0.001,
            "trendStrength": 0.001,
            "maxDrawdownPct": 0.0,
        },
        parsed=parsed,
        provider="local",
        model="deep",
        quick_model="quick",
        deep_model="deep",
        reports={},
        prompt_hash_value="abc123",
        latency_seconds=0.1,
    )


def test_decision_low_confidence_cannot_force_risk_off_action():
    decision = ai_schemas.decision_from_parsed(
        state={"aiMinConfidence": 0.8, "gridMaxExposurePct": 0.35},
        payload={"symbol": "BTCUSDT", "interval": "1m", "price": 100.0, "atrPct": 0.001, "trendStrength": 0.001},
        parsed={
            "riskAction": "flatten",
            "confidence": 0.2,
            "gridAllowed": False,
            "recommendedSpacingPct": 0.01,
            "recommendedLevels": 12,
            "recommendedMaxExposurePct": 0.35,
            "recommendedMode": "scalpy",
        },
        provider="local",
        model="deep",
        quick_model="quick",
        deep_model="deep",
        reports={},
        prompt_hash_value="abc123",
        latency_seconds=0.1,
    )

    assert decision["riskAction"] == "allow_grid"
    assert decision["flattenRecommended"] is False


def test_decision_preserves_zero_risk_budget_for_reduce_exposure():
    decision = _decision(
        {
            "riskAction": "reduce_exposure",
            "confidence": 0.9,
            "riskBudgetPct": 0.0,
            "recommendedMaxExposurePct": 0.35,
        }
    )

    assert decision["riskAction"] == "reduce_exposure"
    assert decision["gridAllowed"] is False
    assert decision["pauseNewBuys"] is True
    assert decision["reduceExposure"] is True
    assert decision["riskBudgetPct"] == 0.0
    assert decision["recommendedMaxExposurePct"] == 0.0


def test_decision_preserves_zero_risk_budget_for_flatten():
    decision = _decision(
        {
            "riskAction": "flatten",
            "confidence": 0.9,
            "riskBudgetPct": 0.0,
            "recommendedMaxExposurePct": 0.35,
        }
    )

    assert decision["riskAction"] == "flatten"
    assert decision["gridAllowed"] is False
    assert decision["flattenRecommended"] is True
    assert decision["reduceExposure"] is True
    assert decision["riskBudgetPct"] == 0.0
    assert decision["recommendedMaxExposurePct"] == 0.0


def test_decision_keeps_allow_grid_minimum_when_risk_budget_is_zero():
    decision = _decision(
        {
            "riskAction": "allow_grid",
            "confidence": 0.9,
            "riskBudgetPct": 0.0,
            "recommendedMaxExposurePct": 0.35,
        }
    )

    assert decision["riskAction"] == "allow_grid"
    assert decision["gridAllowed"] is True
    assert decision["riskBudgetPct"] == 0.05
    assert decision["recommendedMaxExposurePct"] == 0.05


def test_decision_low_confidence_risk_off_does_not_preserve_zero_risk_budget():
    decision = _decision(
        {
            "riskAction": "reduce_exposure",
            "confidence": 0.2,
            "riskBudgetPct": 0.0,
            "recommendedMaxExposurePct": 0.35,
        },
        state={"aiMinConfidence": 0.8},
    )

    assert decision["riskAction"] == "allow_grid"
    assert decision["gridAllowed"] is True
    assert decision["reduceExposure"] is False
    assert decision["riskBudgetPct"] == 0.05
    assert decision["recommendedMaxExposurePct"] == 0.05


def test_decision_preserves_shadow_and_dry_run_flags():
    decision = ai_schemas.decision_from_parsed(
        state={"aiDryRun": True, "aiShadowMode": True},
        payload={"symbol": "BTCUSDT", "interval": "1m", "price": 100.0},
        parsed={"confidence": 0.9, "riskAction": "allow_grid"},
        provider="local",
        model="model",
        quick_model="model",
        deep_model="model",
        reports={},
        prompt_hash_value="abc123",
        latency_seconds=0.1,
    )

    assert decision["dryRun"] is True
    assert decision["shadowMode"] is True


def test_string_evidence_and_key_risks_are_accepted():
    report = ai_schemas.report_from_parsed(
        "grid_risk",
        {
            "summary": "ok",
            "confidence": 0.7,
            "riskScore": 0.2,
            "recommendation": "allow_grid",
            "evidence": "single evidence sentence",
        },
    )
    decision = ai_schemas.decision_from_parsed(
        state={},
        payload={"symbol": "BTCUSDT", "interval": "1m", "price": 100.0},
        parsed={
            "riskAction": "allow_grid",
            "confidence": 0.8,
            "keyRisks": "single risk sentence",
        },
        provider="ollama",
        model="qwen3.5:9b",
        quick_model="qwen3.5:9b",
        deep_model="qwen3.5:9b",
        reports={},
        prompt_hash_value="abc123",
        latency_seconds=0.1,
    )

    assert report.confidence == 0.7
    assert report.evidence == ["single evidence sentence"]
    assert decision["confidence"] == 0.8
    assert decision["keyRisks"] == ["single risk sentence"]


def test_none_evidence_and_key_risks_normalize_to_empty_lists():
    report = ai_schemas.report_from_parsed(
        "grid_risk",
        {"summary": "ok", "evidence": None},
    )
    decision = ai_schemas.decision_from_parsed(
        state={},
        payload={"symbol": "BTCUSDT", "interval": "1m", "price": 100.0},
        parsed={
            "riskAction": "allow_grid",
            "confidence": 0.8,
            "keyRisks": None,
        },
        provider="local",
        model="deep",
        quick_model="quick",
        deep_model="deep",
        reports={},
        prompt_hash_value="abc123",
        latency_seconds=0.1,
    )

    assert report.evidence == []
    assert decision["keyRisks"] == []


def test_deterministic_policy_pauses_when_drawdown_nears_daily_limit():
    policy = ai_schemas.deterministic_policy(
        {"maxDailyLossPct": 0.10},
        {"atrPct": 0.001, "trendStrength": 0.001, "maxDrawdownPct": 0.08},
    )

    assert policy["riskAction"] == "pause_new_buys"
    assert policy["recommendedMode"] == "scalpy"


def test_deterministic_policy_sells_only_when_trend_escape_is_extreme():
    policy = ai_schemas.deterministic_policy(
        {"gridTrendEscapeStrength": 0.004},
        {"atrPct": 0.001, "trendStrength": 0.013, "maxDrawdownPct": 0.0},
    )

    assert policy["riskAction"] == "sells_only"
    assert policy["recommendedMode"] == "fatty"


def test_report_schema_validation_falls_back_to_raw_report_defaults():
    parsed = {"summary": "ignored", "confidence": 2.0, "evidence": ["x"]}

    report = ai_schemas.report_from_parsed("grid_risk", parsed)

    assert report.role == ai_schemas.AgentRole.GRID_RISK
    assert report.summary == ""
    assert report.confidence == 0.0
    assert report.raw == parsed


def test_decision_schema_validation_uses_deterministic_policy_fallback():
    decision = ai_schemas.decision_from_parsed(
        state={"gridMaxExposurePct": 0.35},
        payload={
            "symbol": "BTCUSDT",
            "interval": "1m",
            "price": 100.0,
            "atrPct": 0.001,
            "trendStrength": 0.001,
            "maxDrawdownPct": 0.0,
        },
        parsed={"riskAction": "not-real", "confidence": 0.9},
        provider="local",
        model="deep",
        quick_model="quick",
        deep_model="deep",
        reports={},
        prompt_hash_value="abc123",
        latency_seconds=0.1,
    )

    assert decision["riskAction"] == "allow_grid"
    assert decision["confidence"] == 0.0
    assert decision["note"] == (
        "Portfolio manager output failed schema validation; "
        "using deterministic fallback."
    )
    assert decision["recommendedSpacingPct"] == 0.008
    assert decision["recommendedLevels"] == 14
    assert decision["deterministicPolicy"]["riskAction"] == "allow_grid"
