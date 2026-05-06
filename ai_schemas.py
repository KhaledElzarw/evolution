import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator


PROMPT_VERSION = "local-multi-agent-v1"
REGIMES = {"range", "trend", "breakout_risk", "high_vol"}
DIRECTION_BIASES = {"bullish", "bearish", "neutral"}
GRID_MODES = {"scalpy", "fatty"}
RISK_ACTIONS = {"allow_grid", "pause_new_buys", "sells_only", "reduce_exposure", "flatten", "hold"}


class AgentRole(str, Enum):
    MARKET_REGIME = "market_regime"
    GRID_RISK = "grid_risk"
    POSITION_RISK = "position_risk"
    EXECUTION_GUARD = "execution_guard"
    BULL_CASE = "bull_case"
    BEAR_CASE = "bear_case"
    PORTFOLIO_MANAGER = "portfolio_manager"


class AgentReport(BaseModel):
    role: AgentRole
    summary: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    risk_score: float = Field(default=0.0, ge=0.0, le=1.0)
    recommendation: str = "hold"
    evidence: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evidence", mode="before")
    @classmethod
    def _coerce_evidence(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


class PortfolioDecision(BaseModel):
    risk_action: Literal["allow_grid", "pause_new_buys", "sells_only", "reduce_exposure", "flatten", "hold"] = "allow_grid"
    regime: Literal["range", "trend", "breakout_risk", "high_vol"] = "range"
    direction_bias: Literal["bullish", "bearish", "neutral"] = "neutral"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    breakout_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    grid_allowed: bool = True
    pause_new_buys: bool = False
    allow_sells_only: bool = False
    flatten_recommended: bool = False
    reduce_exposure: bool = False
    risk_budget_pct: float = Field(default=0.25, ge=0.0, le=1.0)
    recommended_spacing_pct: float = Field(default=0.008, ge=0.0, le=1.0)
    recommended_levels: int = Field(default=12, ge=1, le=100)
    recommended_max_exposure_pct: float = Field(default=0.25, ge=0.0, le=1.0)
    recommended_mode: Literal["scalpy", "fatty"] = "scalpy"
    rationale: str = ""
    key_risks: list[str] = Field(default_factory=list)

    @field_validator("recommended_spacing_pct", "recommended_max_exposure_pct", "risk_budget_pct", mode="before")
    @classmethod
    def _coerce_float(cls, value):
        return float(value)

    @field_validator("recommended_levels", mode="before")
    @classmethod
    def _coerce_int(cls, value):
        return int(float(value))

    @field_validator("key_risks", mode="before")
    @classmethod
    def _coerce_key_risks(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def prompt_hash(parts: list[dict[str, Any] | str]) -> str:
    payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def parse_json_object(text: str) -> dict[str, Any]:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object in model response")
    return json.loads(text[start:end + 1])


def deterministic_policy(state: dict, payload: dict) -> dict[str, Any]:
    atr_pct = float(payload.get("atrPct") or 0.0)
    trend_strength = float(payload.get("trendStrength") or 0.0)
    max_dd = float(payload.get("maxDrawdownPct") or 0.0)
    mode = "fatty" if atr_pct >= 0.008 or trend_strength >= 0.006 else "scalpy"
    min_scalpy = float(state.get("gridMinSpacingPctScalpy", 0.008) or 0.008)
    min_fatty = float(state.get("gridMinSpacingPctFatty", 0.01) or 0.01)
    spacing = max(min_fatty if mode == "fatty" else min_scalpy, (1.4 if mode == "fatty" else 0.8) * atr_pct)
    action = "allow_grid"
    if max_dd >= float(state.get("maxDailyLossPct", 0.10) or 0.10) * 0.75:
        action = "pause_new_buys"
    if trend_strength >= float(state.get("gridTrendEscapeStrength", 0.004) or 0.004) * 3:
        action = "sells_only"
    return {
        "riskAction": action,
        "recommendedMode": mode,
        "recommendedSpacingPct": clamp(spacing, 0.003, 0.03),
        "recommendedLevels": 8 if mode == "fatty" else 14,
        "recommendedMaxExposurePct": clamp(float(state.get("gridMaxExposurePct", 0.35) or 0.35), 0.05, 0.60),
    }


def report_from_parsed(role: str, parsed: dict[str, Any]) -> AgentReport:
    try:
        return AgentReport.model_validate({
            "role": role,
            "summary": parsed.get("summary") or parsed.get("note") or "",
            "confidence": parsed.get("confidence", 0.0),
            "risk_score": parsed.get("riskScore", parsed.get("risk_score", 0.0)),
            "recommendation": parsed.get("recommendation", parsed.get("riskAction", "hold")),
            "evidence": parsed.get("evidence", []),
            "raw": parsed,
        })
    except ValidationError:
        return AgentReport(role=AgentRole(role), raw=parsed)


def decision_from_parsed(
    *,
    state: dict,
    payload: dict,
    parsed: dict[str, Any],
    provider: str,
    model: str,
    quick_model: str,
    deep_model: str,
    reports: dict[str, dict[str, Any]],
    prompt_hash_value: str,
    latency_seconds: float,
) -> dict[str, Any]:
    policy = deterministic_policy(state, payload)
    min_conf = float(state.get("aiMinConfidence", 0.55) or 0.55)
    try:
        pm = PortfolioDecision.model_validate({
            "risk_action": parsed.get("riskAction", parsed.get("risk_action", policy["riskAction"])),
            "regime": parsed.get("regime", "range"),
            "direction_bias": parsed.get("directionBias", parsed.get("direction_bias", "neutral")),
            "confidence": parsed.get("confidence", 0.0),
            "breakout_risk": parsed.get("breakoutRisk", parsed.get("breakout_risk", 0.0)),
            "grid_allowed": parsed.get("gridAllowed", parsed.get("grid_allowed", True)),
            "pause_new_buys": parsed.get("pauseNewBuys", parsed.get("pause_new_buys", False)),
            "allow_sells_only": parsed.get("allowSellsOnly", parsed.get("allow_sells_only", False)),
            "flatten_recommended": parsed.get("flattenRecommended", parsed.get("flatten_recommended", False)),
            "reduce_exposure": parsed.get("reduceExposure", parsed.get("reduce_exposure", False)),
            "risk_budget_pct": parsed.get("riskBudgetPct", parsed.get("risk_budget_pct", policy["recommendedMaxExposurePct"])),
            "recommended_spacing_pct": parsed.get("recommendedSpacingPct", parsed.get("recommended_spacing_pct", policy["recommendedSpacingPct"])),
            "recommended_levels": parsed.get("recommendedLevels", parsed.get("recommended_levels", policy["recommendedLevels"])),
            "recommended_max_exposure_pct": parsed.get("recommendedMaxExposurePct", parsed.get("recommended_max_exposure_pct", policy["recommendedMaxExposurePct"])),
            "recommended_mode": parsed.get("recommendedMode", parsed.get("recommended_mode", policy["recommendedMode"])),
            "rationale": parsed.get("rationale", parsed.get("note", "")),
            "key_risks": parsed.get("keyRisks", parsed.get("key_risks", [])),
        })
    except ValidationError:
        pm = PortfolioDecision(
            risk_action=policy["riskAction"],
            recommended_spacing_pct=policy["recommendedSpacingPct"],
            recommended_levels=policy["recommendedLevels"],
            recommended_max_exposure_pct=policy["recommendedMaxExposurePct"],
            recommended_mode=policy["recommendedMode"],
            confidence=0.0,
            rationale="Portfolio manager output failed schema validation; using deterministic fallback.",
        )

    confidence = clamp(pm.confidence, 0.0, 1.0)
    spacing = clamp(pm.recommended_spacing_pct, 0.003, 0.03)
    levels = int(clamp(pm.recommended_levels, 4, 24))
    action = pm.risk_action

    # Low-confidence local models may tune spacing, but cannot force a risk-off action.
    if confidence < min_conf and action in {"pause_new_buys", "sells_only", "reduce_exposure", "flatten"}:
        action = "allow_grid"

    min_exposure = 0.0 if action in {"reduce_exposure", "flatten"} else 0.05
    max_exposure = clamp(min(pm.recommended_max_exposure_pct, pm.risk_budget_pct), min_exposure, 0.60)

    pause_new_buys = bool(pm.pause_new_buys or action in {"pause_new_buys", "sells_only", "reduce_exposure", "flatten"})
    allow_sells_only = bool(pm.allow_sells_only or action in {"sells_only", "reduce_exposure", "flatten"})
    flatten_recommended = bool(pm.flatten_recommended or (action == "flatten" and confidence >= max(min_conf, 0.75)))
    reduce_exposure = bool(pm.reduce_exposure or action in {"reduce_exposure", "flatten"})
    grid_allowed = bool(pm.grid_allowed and not pause_new_buys)

    decision_id = prompt_hash([
        PROMPT_VERSION,
        payload.get("symbol"),
        payload.get("interval"),
        payload.get("price"),
        prompt_hash_value,
        utc_now()[:16],
    ])
    return {
        "schemaVersion": PROMPT_VERSION,
        "decisionId": decision_id,
        "enabled": True,
        "provider": provider,
        "model": model,
        "quickModel": quick_model,
        "deepModel": deep_model,
        "tsUtc": utc_now(),
        "symbol": payload.get("symbol", state.get("symbol", "BTCUSDT")),
        "interval": payload.get("interval", state.get("interval", "1m")),
        "regime": pm.regime,
        "directionBias": pm.direction_bias,
        "confidence": confidence,
        "breakoutRisk": clamp(pm.breakout_risk, 0.0, 1.0),
        "modelGridAllowed": bool(pm.grid_allowed),
        "gridAllowed": grid_allowed,
        "pauseNewBuys": pause_new_buys,
        "allowSellsOnly": allow_sells_only,
        "flattenRecommended": flatten_recommended,
        "reduceExposure": reduce_exposure,
        "riskAction": action,
        "riskBudgetPct": max_exposure,
        "recommendedSpacingPct": spacing,
        "recommendedLevels": levels,
        "recommendedMaxExposurePct": max_exposure,
        "recommendedMode": pm.recommended_mode,
        "note": pm.rationale,
        "keyRisks": pm.key_risks,
        "reports": reports,
        "deterministicPolicy": policy,
        "shadowMode": bool(state.get("aiShadowMode", False)),
        "dryRun": bool(state.get("aiDryRun", False)),
        "promptVersion": PROMPT_VERSION,
        "promptHash": prompt_hash_value,
        "latencySeconds": latency_seconds,
        "raw": payload,
    }
