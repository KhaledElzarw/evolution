import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from ai_memory import append_decision, recent_lessons, update_lessons_from_trades
from ai_schemas import (
    PROMPT_VERSION,
    decision_from_parsed,
    deterministic_policy,
    parse_json_object,
    prompt_hash,
    report_from_parsed,
    utc_now,
)
from engine import (
    LOG_PATH,
    RUNTIME_PATH,
    STATE_PATH,
    _read_ai_signal,
    _read_json,
    _write_ai_signal,
)


BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "ai_prompt_templates"
STATUS_PATH = BASE_DIR / "engine_status.json"


class AiDisabled(RuntimeError):
    pass


def _log(msg: str) -> None:
    line = f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] AI_SIDECAR {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _build_payload(state: dict, runtime: dict) -> dict | None:
    market = runtime.get("market") or {}
    grid = runtime.get("grid") or {}
    stats = runtime.get("stats") or {}
    paper = runtime.get("paper") or {}
    price = market.get("price")
    candle = market.get("candle") or {}

    status = {}
    if price is None:
        try:
            status = _read_json(str(STATUS_PATH))
        except Exception:
            status = {}
        price = status.get("price")
        if not candle:
            candle = {
                "open": price,
                "high": price,
                "low": price,
                "close": price,
            }
        if not stats:
            stats = status.get("stats") or {}
        if not paper:
            paper = {
                "usdt": status.get("usdt", 0.0),
                "btc": status.get("btc", 0.0),
            }
        if not grid:
            grid = ((runtime.get("grid") or {}) if isinstance(runtime.get("grid"), dict) else {})

    if price is None:
        return None

    open_px = _safe_float(candle.get("open"), _safe_float(price))
    close_px = _safe_float(candle.get("close"), _safe_float(price))
    high_px = _safe_float(candle.get("high"), _safe_float(price))
    low_px = _safe_float(candle.get("low"), _safe_float(price))
    price_f = _safe_float(price)
    usdt = _safe_float(paper.get("usdt"))
    btc = _safe_float(paper.get("btc"))
    equity = usdt + (btc * price_f)
    orders = grid.get("orders") or []
    position = (status.get("position") if status else None) or {}
    avg_cost = _safe_float(position.get("entryPrice")) if position else 0.0
    if not avg_cost and btc > 0 and _safe_float(grid.get("cost_basis_usdt")) > 0:
        avg_cost = _safe_float(grid.get("cost_basis_usdt")) / btc
    unreal_pct = ((price_f / avg_cost) - 1.0) if avg_cost else 0.0

    return {
        "symbol": state.get("symbol", "BTCUSDT"),
        "interval": state.get("interval", "1m"),
        "price": price_f,
        "candle": {
            "open": open_px,
            "high": high_px,
            "low": low_px,
            "close": close_px,
            "volumeBase": _safe_float(candle.get("volumeBase")),
            "volumeUsdt": _safe_float(candle.get("volumeUsdt")),
            "openTimeMs": candle.get("openTimeMs"),
            "closeTimeMs": candle.get("closeTimeMs"),
        },
        "atrPct": ((high_px - low_px) / price_f) if price_f else 0.0,
        "trendStrength": abs(close_px - open_px) / price_f if price_f else 0.0,
        "priceChangePct20": ((close_px / open_px) - 1.0) if open_px else 0.0,
        "equityUsdt": equity,
        "usdt": usdt,
        "btc": btc,
        "hasOpenPosition": bool(btc > 0),
        "avgCost": avg_cost,
        "unrealizedPnlPct": unreal_pct,
        "gridActive": bool(grid.get("active")),
        "gridSpacingPct": grid.get("spacing_pct"),
        "gridLevels": grid.get("levels"),
        "gridMaxExposurePct": grid.get("max_exposure_pct"),
        "gridReservedUsdt": grid.get("reserved_usdt"),
        "gridCostBasisUsdt": grid.get("cost_basis_usdt"),
        "openOrders": len(orders),
        "openBuyOrders": len([o for o in orders if o.get("side") == "BUY"]),
        "openSellOrders": len([o for o in orders if o.get("side") == "SELL"]),
        "dayTrades": stats.get("trades", 0),
        "closedTrades": stats.get("closedTrades", 0),
        "maxDrawdownPct": stats.get("max_drawdown_pct", stats.get("maxDrawdownPct", 0.0)),
        "fees": {
            "feeBps": _safe_float(state.get("feeBps", 10)),
            "paperLimitSlipBps": _safe_float(state.get("paperLimitSlipBps", 3.0)),
            "paperMarketSlipBps": _safe_float(state.get("paperMarketSlipBps", 12.0)),
        },
        "riskLimits": {
            "maxDailyLossPct": _safe_float(state.get("maxDailyLossPct", 0.10)),
            "maxTradesPerDay": _safe_int(state.get("maxTradesPerDay", 200)),
            "gridMaxExposurePct": _safe_float(state.get("gridMaxExposurePct", 0.35)),
            "gridMinSpacingPctScalpy": _safe_float(state.get("gridMinSpacingPctScalpy", 0.008)),
            "gridMinSpacingPctFatty": _safe_float(state.get("gridMinSpacingPctFatty", 0.010)),
            "gridTrendEscapeStrength": _safe_float(state.get("gridTrendEscapeStrength", 0.004)),
        },
    }


def _model_config(state: dict) -> dict:
    provider = str(state.get("aiProvider") or os.getenv("TRADEBOT_AI_PROVIDER") or "ollama").strip()
    host = str(
        state.get("aiBaseUrl")
        or os.getenv("TRADEBOT_AI_BASE_URL")
        or "http://127.0.0.1:11434/v1"
    ).rstrip("/")
    primary = str(state.get("aiModel") or os.getenv("TRADEBOT_AI_MODEL") or "qwen2.5:3b").strip()
    quick = str(state.get("aiQuickModel") or primary).strip()
    deep = str(state.get("aiDeepModel") or state.get("aiFallbackModel") or primary).strip()
    fallback = str(state.get("aiFallbackModel") or "").strip()
    timeout_s = max(2.0, _safe_float(state.get("aiTimeoutSeconds"), 30.0))
    return {
        "provider": provider,
        "host": host,
        "model": deep or quick or primary,
        "quick_model": quick or primary,
        "deep_model": deep or quick or primary,
        "fallback_model": fallback,
        "timeout_s": timeout_s,
    }


def _load_template(name: str) -> str:
    path = TEMPLATE_DIR / f"{name}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return (
        "Return strict JSON only. Analyze this trading snapshot for role={role}.\n"
        "Snapshot:\n{payload_json}\n\nLessons:\n{lessons}\n\nPrior reports:\n{reports_json}"
    )


def _ensure_ai_enabled() -> None:
    state = _read_json(STATE_PATH)
    if not bool(state.get("aiEnabled", False)):
        raise AiDisabled("AI assist disabled")


def _chat_json(*, state: dict, model: str, messages: list[dict], max_tokens: int = 500) -> dict:
    _ensure_ai_enabled()
    cfg = _model_config(state)
    started = time.time()
    if "ollama" in str(cfg.get("provider", "")).lower() or "11434" in str(cfg.get("host", "")):
        base = cfg["host"]
        if base.endswith("/v1"):
            base = base[:-3]
        response = requests.post(
            base + "/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
                "think": False,
                "options": {
                    "temperature": _safe_float(state.get("aiTemperature"), 0.1),
                    "num_predict": max_tokens,
                },
            },
            timeout=cfg["timeout_s"],
        )
        response.raise_for_status()
        data = response.json()
        content = ((data.get("message") or {}).get("content") or "").strip()
        parsed = parse_json_object(content)
        parsed["_latencySeconds"] = time.time() - started
        return parsed

    response = requests.post(
        cfg["host"] + "/chat/completions",
        json={
            "model": model,
            "messages": messages,
            "temperature": _safe_float(state.get("aiTemperature"), 0.1),
            "max_tokens": max_tokens,
            "stream": False,
        },
        headers={"Authorization": "Bearer local", "Content-Type": "application/json"},
        timeout=cfg["timeout_s"],
    )
    response.raise_for_status()
    data = response.json()
    content = data["choices"][0]["message"]["content"]
    parsed = parse_json_object(content)
    parsed["_latencySeconds"] = time.time() - started
    return parsed


def _run_agent(
    *,
    role: str,
    template: str,
    state: dict,
    payload: dict,
    model: str,
    lessons: str,
    reports: dict | None = None,
) -> dict:
    reports_json = json.dumps(reports or {}, sort_keys=True)
    payload_json = json.dumps(payload, sort_keys=True)
    prompt = template.format(
        role=role,
        payload_json=payload_json,
        lessons=lessons,
        reports_json=reports_json,
    )
    parsed = _chat_json(
        state=state,
        model=model,
        max_tokens=420,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a local trading risk agent. Return one JSON object only. "
                    "No markdown, no prose outside JSON."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    )
    report = report_from_parsed(role, parsed)
    return report.model_dump(mode="json")


def _synthetic_case_report(role: str, payload: dict, reports: dict[str, dict]) -> dict:
    trend = _safe_float(payload.get("trendStrength"))
    atr = _safe_float(payload.get("atrPct"))
    has_position = bool(payload.get("hasOpenPosition"))
    if role == "bull_case":
        evidence = [
            f"trendStrength={trend:.5f}",
            f"atrPct={atr:.5f}",
            f"hasOpenPosition={has_position}",
        ]
        risk_score = min(1.0, max(0.0, atr * 45.0))
        recommendation = "allow_grid" if trend < 0.01 else "reduce_exposure"
        summary = "Bull case favors range capture if volatility stays controlled."
    else:
        evidence = [
            f"trendStrength={trend:.5f}",
            f"atrPct={atr:.5f}",
            f"openOrders={payload.get('openOrders')}",
        ]
        risk_score = min(1.0, max(trend * 90.0, atr * 60.0))
        recommendation = "sells_only" if risk_score >= 0.6 else "hold"
        summary = "Bear case highlights breakout and fee-drag risk."
    return {
        "role": role,
        "summary": summary,
        "confidence": 0.55,
        "risk_score": risk_score,
        "recommendation": recommendation,
        "evidence": evidence,
        "raw": {"source": "deterministic_case_review", "reportsSeen": sorted(reports.keys())},
    }


def _fallback_decision(
    *,
    state: dict,
    payload: dict,
    cfg: dict,
    started: float,
    error: Exception | None = None,
    persist: bool = True,
) -> dict:
    policy = deterministic_policy(state, payload)
    parsed = {
        "riskAction": policy["riskAction"],
        "regime": "range",
        "directionBias": "neutral",
        "confidence": 0.0,
        "breakoutRisk": min(1.0, _safe_float(payload.get("trendStrength")) * 80.0),
        "gridAllowed": policy["riskAction"] == "allow_grid",
        "recommendedSpacingPct": policy["recommendedSpacingPct"],
        "recommendedLevels": policy["recommendedLevels"],
        "recommendedMaxExposurePct": policy["recommendedMaxExposurePct"],
        "recommendedMode": policy["recommendedMode"],
        "rationale": "Local AI unavailable; using deterministic risk policy.",
        "keyRisks": [str(error)] if error else [],
    }
    p_hash = prompt_hash([PROMPT_VERSION, payload, parsed, "deterministic_fallback"])
    decision = decision_from_parsed(
        state=state,
        payload=payload,
        parsed=parsed,
        provider=cfg["provider"],
        model=cfg["model"],
        quick_model=cfg["quick_model"],
        deep_model=cfg["deep_model"],
        reports={},
        prompt_hash_value=p_hash,
        latency_seconds=time.time() - started,
    )
    decision["source"] = "deterministic_fallback"
    decision["error"] = str(error) if error else ""
    if persist:
        append_decision(decision, prompts={"fallback": p_hash}, reports={})
    return decision


def _run_multi_agent_decision(state: dict, payload: dict, *, persist: bool = True) -> dict:
    started = time.time()
    cfg = _model_config(state)
    update_lessons_from_trades()
    lessons = recent_lessons(payload.get("symbol", state.get("symbol", "BTCUSDT")))
    reports: dict[str, dict] = {}
    prompts: dict[str, str] = {}

    agent_specs = [
        ("market_regime", "market_regime"),
        ("grid_risk", "grid_risk"),
        ("position_risk", "position_risk"),
        ("execution_guard", "execution_guard"),
    ]
    try:
        for role, template_name in agent_specs:
            _ensure_ai_enabled()
            template = _load_template(template_name)
            prompts[role] = template
            _log(f"AGENT_START role={role} model={cfg['quick_model']}")
            reports[role] = _run_agent(
                role=role,
                template=template,
                state=state,
                payload=payload,
                model=cfg["quick_model"],
                lessons=lessons,
                reports=reports,
            )
            _log(f"AGENT_OK role={role} risk={reports[role].get('risk_score')}")

        for role in ("bull_case", "bear_case"):
            _ensure_ai_enabled()
            template_path = TEMPLATE_DIR / f"{role}.txt"
            if template_path.exists():
                template = _load_template(role)
                prompts[role] = template
                _log(f"AGENT_START role={role} model={cfg['quick_model']}")
                reports[role] = _run_agent(
                    role=role,
                    template=template,
                    state=state,
                    payload=payload,
                    model=cfg["quick_model"],
                    lessons=lessons,
                    reports=reports,
                )
                _log(f"AGENT_OK role={role} risk={reports[role].get('risk_score')}")
            else:
                reports[role] = _synthetic_case_report(role, payload, reports)

        portfolio_template = _load_template("portfolio_manager")
        _ensure_ai_enabled()
        prompts["portfolio_manager"] = portfolio_template
        reports_json = json.dumps(reports, sort_keys=True)
        payload_json = json.dumps(payload, sort_keys=True)
        portfolio_prompt = portfolio_template.format(
            payload_json=payload_json,
            lessons=lessons,
            reports_json=reports_json,
            role="portfolio_manager",
        )
        p_hash = prompt_hash([PROMPT_VERSION, prompts, payload, reports])
        _log(f"PORTFOLIO_START model={cfg['deep_model']}")
        parsed = _chat_json(
            state=state,
            model=cfg["deep_model"],
            max_tokens=650,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a local portfolio manager for a paper grid bot. "
                        "Return one strict JSON object only."
                    ),
                },
                {"role": "user", "content": portfolio_prompt},
            ],
        )
        decision = decision_from_parsed(
            state=state,
            payload=payload,
            parsed=parsed,
            provider=cfg["provider"],
            model=cfg["deep_model"],
            quick_model=cfg["quick_model"],
            deep_model=cfg["deep_model"],
            reports=reports,
            prompt_hash_value=p_hash,
            latency_seconds=time.time() - started,
        )
        decision["source"] = "local_multi_agent"
        if persist:
            append_decision(decision, prompts=prompts, reports=reports)
        _log(
            "PORTFOLIO_OK "
            f"decision={decision.get('decisionId')} action={decision.get('riskAction')} "
            f"gridAllowed={decision.get('gridAllowed')} elapsed={decision.get('latencySeconds'):.3f}s"
        )
        return decision
    except AiDisabled:
        _log("ABORT_DISABLED")
        raise
    except Exception as e:
        _log(f"MULTI_AGENT_ERROR error={e}")
        if cfg.get("fallback_model") and cfg["fallback_model"] not in {cfg["quick_model"], cfg["deep_model"]}:
            try:
                fallback_state = dict(state)
                fallback_state["aiQuickModel"] = cfg["fallback_model"]
                fallback_state["aiDeepModel"] = cfg["fallback_model"]
                fallback_cfg = _model_config(fallback_state)
                template = _load_template("portfolio_manager")
                p_hash = prompt_hash([PROMPT_VERSION, "fallback_model", payload, reports])
                parsed = _chat_json(
                    state=fallback_state,
                    model=fallback_cfg["deep_model"],
                    max_tokens=650,
                    messages=[
                        {"role": "system", "content": "Return strict JSON only."},
                        {
                            "role": "user",
                            "content": template.format(
                                payload_json=json.dumps(payload, sort_keys=True),
                                lessons=lessons,
                                reports_json=json.dumps(reports, sort_keys=True),
                                role="portfolio_manager",
                            ),
                        },
                    ],
                )
                decision = decision_from_parsed(
                    state=state,
                    payload=payload,
                    parsed=parsed,
                    provider=fallback_cfg["provider"],
                    model=fallback_cfg["deep_model"],
                    quick_model=fallback_cfg["quick_model"],
                    deep_model=fallback_cfg["deep_model"],
                    reports=reports,
                    prompt_hash_value=p_hash,
                    latency_seconds=time.time() - started,
                )
                decision["source"] = "local_ai_fallback_model"
                decision["fallbackFrom"] = cfg["deep_model"]
                if persist:
                    append_decision(decision, prompts=prompts, reports=reports)
                return decision
            except Exception as fallback_error:
                _log(f"FALLBACK_MODEL_ERROR model={cfg['fallback_model']} error={fallback_error}")
                e = fallback_error
        return _fallback_decision(state=state, payload=payload, cfg=cfg, started=started, error=e, persist=persist)


def _query_model(state: dict, payload: dict) -> dict:
    return _run_multi_agent_decision(state, payload, persist=True)


def _run_once(*, persist: bool = True, write_signal: bool = False) -> dict:
    state = _read_json(STATE_PATH)
    runtime = _read_json(RUNTIME_PATH)
    payload = _build_payload(state, runtime)
    if not payload:
        raise RuntimeError("No runtime payload available for AI review")
    decision = _run_multi_agent_decision(state, payload, persist=persist)
    if write_signal:
        _write_ai_signal(decision)
    return decision


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local AI decision sidecar.")
    parser.add_argument("--once", action="store_true", help="Run one local AI review and print the decision.")
    parser.add_argument("--no-persist", action="store_true", help="Do not append to ai_decisions.jsonl when using --once.")
    parser.add_argument("--write-signal", action="store_true", help="Write ai_signal.json when using --once.")
    args = parser.parse_args()

    if args.once:
        decision = _run_once(persist=not args.no_persist, write_signal=args.write_signal)
        print(json.dumps(decision, indent=2, sort_keys=True))
        return

    _log("BOOT")
    while True:
        state = _read_json(STATE_PATH)
        poll_s = max(2.0, _safe_float(state.get("aiPollSeconds"), 15.0))
        if not bool(state.get("aiEnabled", False)):
            _write_ai_signal({"enabled": False, "source": "disabled", "tsUtc": utc_now()})
            _log("DISABLED")
            time.sleep(2)
            continue

        runtime = _read_json(RUNTIME_PATH)
        payload = _build_payload(state, runtime)
        if not payload:
            _log("NO_PAYLOAD")
            time.sleep(2)
            continue

        try:
            result = _query_model(state, payload)
            _write_ai_signal(result)
            _log(
                f"SIGNAL_WRITTEN model={result.get('model')} action={result.get('riskAction')} "
                f"stale={result.get('stale', False)} gridAllowed={result.get('gridAllowed')}"
            )
        except AiDisabled:
            _write_ai_signal({"enabled": False, "source": "disabled", "tsUtc": utc_now()})
            _log("DISABLED")
        except Exception as e:
            fallback = _read_ai_signal() or {}
            fallback.update({
                "enabled": True,
                "provider": state.get("aiProvider") or "ollama",
                "model": state.get("aiModel") or "qwen2.5:3b",
                "tsUtc": utc_now(),
                "error": str(e),
                "stale": True,
            })
            _write_ai_signal(fallback)
            _log(f"REQUEST_ERROR model={fallback.get('model')} error={e}")
        time.sleep(poll_s)


if __name__ == "__main__":
    main()
