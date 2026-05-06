import json

import ai_sidecar
import pytest


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _StopSidecarLoop(Exception):
    def __init__(self, seconds):
        super().__init__(seconds)
        self.seconds = seconds


class _StopAfterContinue(Exception):
    pass


def _minimal_payload():
    return {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "price": 100.0,
        "atrPct": 0.001,
        "trendStrength": 0.001,
    }


def _fake_agent_report(role):
    return {
        "role": role,
        "summary": f"{role} summary",
        "confidence": 0.5,
        "risk_score": 0.1,
        "recommendation": "hold",
        "evidence": [],
        "raw": {},
    }


def _patch_multi_agent_boundaries(monkeypatch):
    monkeypatch.setattr(ai_sidecar, "_log", lambda msg: None)
    monkeypatch.setattr(ai_sidecar, "_ensure_ai_enabled", lambda: None)
    monkeypatch.setattr(ai_sidecar, "update_lessons_from_trades", lambda: 0)
    monkeypatch.setattr(ai_sidecar, "recent_lessons", lambda symbol: "LESSONS")
    monkeypatch.setattr(
        ai_sidecar,
        "_load_template",
        lambda name: "{role} {payload_json} {lessons} {reports_json}",
    )

    def fake_run_agent(*, role, template, state, payload, model, lessons, reports=None):
        return _fake_agent_report(role)

    monkeypatch.setattr(ai_sidecar, "_run_agent", fake_run_agent)


def _patch_main_loop_io(monkeypatch, *, state, runtime=None):
    logs = []
    written = []

    def fake_read_json(path):
        if path == ai_sidecar.STATE_PATH:
            return state
        if path == ai_sidecar.RUNTIME_PATH:
            return runtime or {}
        raise AssertionError(f"unexpected read: {path}")

    def stop_sleep(seconds):
        raise _StopSidecarLoop(seconds)

    monkeypatch.setattr("sys.argv", ["ai_sidecar.py"])
    monkeypatch.setattr(ai_sidecar, "_read_json", fake_read_json)
    monkeypatch.setattr(ai_sidecar, "_log", logs.append)
    monkeypatch.setattr(ai_sidecar, "_write_ai_signal", written.append)
    monkeypatch.setattr(ai_sidecar.time, "sleep", stop_sleep)
    return logs, written


def test_safe_number_helpers_use_defaults():
    assert ai_sidecar._safe_float("1.25") == 1.25
    assert ai_sidecar._safe_float("bad", default=9.5) == 9.5
    assert ai_sidecar._safe_int("7.9") == 7
    assert ai_sidecar._safe_int(None, default=4) == 4


def test_model_config_prefers_state_and_clamps_timeout(monkeypatch):
    monkeypatch.setenv("TRADEBOT_AI_PROVIDER", "env-provider")
    monkeypatch.setenv("TRADEBOT_AI_BASE_URL", "http://env-host/v1")
    monkeypatch.setenv("TRADEBOT_AI_MODEL", "env-model")

    cfg = ai_sidecar._model_config(
        {
            "aiProvider": "state-provider",
            "aiBaseUrl": "http://state-host/v1/",
            "aiModel": "primary-model",
            "aiQuickModel": "quick-model",
            "aiFallbackModel": "fallback-model",
            "aiTimeoutSeconds": "0.1",
        }
    )

    assert cfg == {
        "provider": "state-provider",
        "host": "http://state-host/v1",
        "model": "fallback-model",
        "quick_model": "quick-model",
        "deep_model": "fallback-model",
        "fallback_model": "fallback-model",
        "timeout_s": 2.0,
    }


def test_load_template_reads_file_and_falls_back(monkeypatch, tmp_path):
    template = tmp_path / "custom.txt"
    template.write_text("hello {role}", encoding="utf-8")
    monkeypatch.setattr(ai_sidecar, "TEMPLATE_DIR", tmp_path)

    assert ai_sidecar._load_template("custom") == "hello {role}"

    fallback = ai_sidecar._load_template("missing")
    assert "Return strict JSON only" in fallback
    assert "{payload_json}" in fallback


def test_build_payload_includes_grid_and_risk_context():
    payload = ai_sidecar._build_payload(
        {
            "symbol": "BTCUSDT",
            "interval": "1m",
            "feeBps": 10,
            "gridMaxExposurePct": 0.35,
            "maxDailyLossPct": 0.1,
        },
        {
            "market": {
                "price": 100.0,
                "candle": {"open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0},
            },
            "paper": {"usdt": 50.0, "btc": 0.5},
            "grid": {
                "active": True,
                "spacing_pct": 0.01,
                "levels": 12,
                "max_exposure_pct": 0.35,
                "cost_basis_usdt": 45.0,
                "orders": [{"side": "BUY"}, {"side": "SELL"}],
            },
            "stats": {"trades": 2, "max_drawdown_pct": 0.03},
        },
    )

    assert payload["equityUsdt"] == 100.0
    assert payload["openBuyOrders"] == 1
    assert payload["openSellOrders"] == 1
    assert payload["riskLimits"]["maxDailyLossPct"] == 0.1


def test_build_payload_falls_back_to_status_without_runtime_market(monkeypatch):
    def fake_read_json(path):
        assert path == str(ai_sidecar.STATUS_PATH)
        return {
            "price": "120.0",
            "usdt": "80.0",
            "btc": "0.25",
            "position": {"entryPrice": "100.0"},
            "stats": {"closedTrades": 3, "maxDrawdownPct": 0.04},
        }

    monkeypatch.setattr(ai_sidecar, "_read_json", fake_read_json)

    payload = ai_sidecar._build_payload(
        {"symbol": "ETHUSDT", "maxTradesPerDay": "12.9"},
        {"market": {}, "grid": {"orders": [{"side": "BUY"}]}},
    )

    assert payload["symbol"] == "ETHUSDT"
    assert payload["price"] == 120.0
    assert payload["equityUsdt"] == 110.0
    assert payload["avgCost"] == 100.0
    assert payload["unrealizedPnlPct"] == pytest.approx(0.2)
    assert payload["closedTrades"] == 3
    assert payload["maxDrawdownPct"] == 0.04
    assert payload["openBuyOrders"] == 1
    assert payload["riskLimits"]["maxTradesPerDay"] == 12


def test_build_payload_returns_none_when_no_price(monkeypatch):
    monkeypatch.setattr(ai_sidecar, "_read_json", lambda path: {})

    assert ai_sidecar._build_payload({}, {"market": {}}) is None


def test_build_payload_returns_none_when_status_read_fails(monkeypatch):
    def fail_read_json(path):
        assert path == str(ai_sidecar.STATUS_PATH)
        raise OSError("status unavailable")

    monkeypatch.setattr(ai_sidecar, "_read_json", fail_read_json)

    assert ai_sidecar._build_payload({}, {"market": {}}) is None


def test_fallback_decision_is_not_stale_and_has_source():
    payload = {
        "symbol": "BTCUSDT",
        "interval": "1m",
        "price": 100.0,
        "atrPct": 0.001,
        "trendStrength": 0.001,
    }
    cfg = {"provider": "local", "model": "m", "quick_model": "q", "deep_model": "d"}

    decision = ai_sidecar._fallback_decision(
        state={"gridMaxExposurePct": 0.35},
        payload=payload,
        cfg=cfg,
        started=0.0,
        error=RuntimeError("down"),
        persist=False,
    )

    assert decision["source"] == "deterministic_fallback"
    assert decision["enabled"] is True
    assert "stale" not in decision


def test_fallback_decision_persist_false_does_not_append(monkeypatch):
    appended = []
    monkeypatch.setattr(
        ai_sidecar,
        "append_decision",
        lambda *args, **kwargs: appended.append((args, kwargs)),
    )

    decision = ai_sidecar._fallback_decision(
        state={"gridMaxExposurePct": 0.35},
        payload={
            "symbol": "BTCUSDT",
            "interval": "1m",
            "price": 100.0,
            "atrPct": 0.001,
            "trendStrength": 0.01,
        },
        cfg={"provider": "local", "model": "m", "quick_model": "q", "deep_model": "d"},
        started=0.0,
        error=RuntimeError("model offline"),
        persist=False,
    )

    assert appended == []
    assert decision["source"] == "deterministic_fallback"
    assert decision["error"] == "model offline"
    assert decision["keyRisks"] == ["model offline"]


def test_fallback_decision_persist_true_appends(monkeypatch):
    appended = []

    def fake_append(decision, *, prompts, reports):
        appended.append({"decision": decision, "prompts": prompts, "reports": reports})

    monkeypatch.setattr(ai_sidecar, "append_decision", fake_append)

    decision = ai_sidecar._fallback_decision(
        state={"gridMaxExposurePct": 0.35},
        payload=_minimal_payload(),
        cfg={"provider": "local", "model": "m", "quick_model": "q", "deep_model": "d"},
        started=0.0,
        error=RuntimeError("model offline"),
        persist=True,
    )

    assert appended == [
        {
            "decision": decision,
            "prompts": {"fallback": decision["promptHash"]},
            "reports": {},
        },
    ]


def test_synthetic_case_report_is_deterministic_for_bull_and_bear_cases():
    payload = {
        "trendStrength": 0.02,
        "atrPct": 0.01,
        "hasOpenPosition": True,
        "openOrders": 5,
    }

    bull = ai_sidecar._synthetic_case_report(
        "bull_case",
        payload,
        {"grid_risk": {}, "market_regime": {}},
    )
    bear = ai_sidecar._synthetic_case_report("bear_case", payload, {"bull_case": {}})

    assert bull["role"] == "bull_case"
    assert bull["recommendation"] == "reduce_exposure"
    assert bull["risk_score"] == pytest.approx(0.45)
    assert bull["raw"]["reportsSeen"] == ["grid_risk", "market_regime"]
    assert "hasOpenPosition=True" in bull["evidence"]

    assert bear["role"] == "bear_case"
    assert bear["recommendation"] == "sells_only"
    assert bear["risk_score"] == 1.0
    assert bear["raw"]["reportsSeen"] == ["bull_case"]
    assert "openOrders=5" in bear["evidence"]


def test_ensure_ai_enabled_raises_when_state_disables_ai(monkeypatch):
    monkeypatch.setattr(ai_sidecar, "_read_json", lambda path: {"aiEnabled": False})

    with pytest.raises(ai_sidecar.AiDisabled, match="AI assist disabled"):
        ai_sidecar._ensure_ai_enabled()


def test_ensure_ai_enabled_allows_enabled_state(monkeypatch):
    monkeypatch.setattr(ai_sidecar, "_read_json", lambda path: {"aiEnabled": True})

    ai_sidecar._ensure_ai_enabled()


def test_log_writes_to_patched_log_path(monkeypatch, tmp_path, capsys):
    log_path = tmp_path / "ai_sidecar.log"
    monkeypatch.setattr(ai_sidecar, "LOG_PATH", str(log_path))

    ai_sidecar._log("HELLO")

    stdout = capsys.readouterr().out.strip()
    assert "AI_SIDECAR HELLO" in stdout
    assert log_path.read_text(encoding="utf-8").strip() == stdout


def test_chat_json_uses_ollama_native_chat_endpoint(monkeypatch):
    calls = []
    messages = [{"role": "user", "content": "hi"}]
    monkeypatch.setattr(ai_sidecar, "_ensure_ai_enabled", lambda: None)

    def fake_post(url, *, json, timeout, headers=None):
        calls.append({"url": url, "json": json, "timeout": timeout, "headers": headers})
        return _FakeResponse({
            "message": {"content": 'prefix {"ok": true, "value": 3} suffix'},
        })

    monkeypatch.setattr(ai_sidecar.requests, "post", fake_post)

    result = ai_sidecar._chat_json(
        state={
            "aiProvider": "ollama",
            "aiBaseUrl": "http://ollama.local:11434/v1",
            "aiTemperature": "0.25",
            "aiTimeoutSeconds": 3,
        },
        model="qwen",
        messages=messages,
        max_tokens=123,
    )

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "http://ollama.local:11434/api/chat"
    assert call["headers"] is None
    assert call["timeout"] == 3.0
    assert call["json"]["model"] == "qwen"
    assert call["json"]["messages"] == messages
    assert call["json"]["stream"] is False
    assert call["json"]["think"] is False
    assert call["json"]["options"] == {"temperature": 0.25, "num_predict": 123}
    assert result["ok"] is True
    assert result["value"] == 3
    assert result["_latencySeconds"] >= 0


def test_chat_json_uses_openai_compatible_chat_completions(monkeypatch):
    calls = []
    messages = [{"role": "user", "content": "hi"}]
    monkeypatch.setattr(ai_sidecar, "_ensure_ai_enabled", lambda: None)

    def fake_post(url, *, json, headers, timeout):
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _FakeResponse({
            "choices": [{"message": {"content": '{"riskAction": "hold"}'}}],
        })

    monkeypatch.setattr(ai_sidecar.requests, "post", fake_post)

    result = ai_sidecar._chat_json(
        state={
            "aiProvider": "openai",
            "aiBaseUrl": "http://model.local/v1",
            "aiTemperature": "0.4",
            "aiTimeoutSeconds": 4,
        },
        model="gpt-local",
        messages=messages,
        max_tokens=77,
    )

    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "http://model.local/v1/chat/completions"
    assert call["timeout"] == 4.0
    assert call["headers"] == {
        "Authorization": "Bearer local",
        "Content-Type": "application/json",
    }
    assert call["json"] == {
        "model": "gpt-local",
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 77,
        "stream": False,
    }
    assert result["riskAction"] == "hold"
    assert result["_latencySeconds"] >= 0


@pytest.mark.parametrize(
    ("state", "payload"),
    [
        (
            {"aiProvider": "ollama", "aiBaseUrl": "http://host:11434/v1"},
            {"message": {"content": ""}},
        ),
        (
            {"aiProvider": "openai", "aiBaseUrl": "http://host/v1"},
            {"choices": [{"message": {"content": "no json here"}}]},
        ),
    ],
)
def test_chat_json_raises_when_response_has_no_json_object(
    monkeypatch,
    state,
    payload,
):
    monkeypatch.setattr(ai_sidecar, "_ensure_ai_enabled", lambda: None)
    monkeypatch.setattr(
        ai_sidecar.requests,
        "post",
        lambda *args, **kwargs: _FakeResponse(payload),
    )

    with pytest.raises(ValueError, match="No JSON object"):
        ai_sidecar._chat_json(
            state=state,
            model="model",
            messages=[{"role": "user", "content": "hi"}],
        )


def test_run_agent_formats_prompt_and_returns_report(monkeypatch):
    captured = {}

    def fake_chat_json(*, state, model, messages, max_tokens=500):
        captured.update(
            {
                "state": state,
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
            },
        )
        return {
            "summary": "ok",
            "confidence": 0.7,
            "riskScore": 0.3,
            "recommendation": "hold",
            "evidence": ["fee risk"],
        }

    monkeypatch.setattr(ai_sidecar, "_chat_json", fake_chat_json)
    state = {"marker": "state"}

    report = ai_sidecar._run_agent(
        role="grid_risk",
        template=(
            "role={role}; payload={payload_json}; "
            "lessons={lessons}; reports={reports_json}"
        ),
        state=state,
        payload={"b": 2, "a": 1},
        model="quick",
        lessons="LESSON TEXT",
        reports={"market_regime": {"risk_score": 0.2}},
    )

    assert captured["state"] is state
    assert captured["model"] == "quick"
    assert captured["max_tokens"] == 420
    assert captured["messages"][0]["role"] == "system"
    assert "Return one JSON object only" in captured["messages"][0]["content"]
    prompt = captured["messages"][1]["content"]
    assert "role=grid_risk" in prompt
    assert 'payload={"a": 1, "b": 2}' in prompt
    assert "lessons=LESSON TEXT" in prompt
    assert 'reports={"market_regime": {"risk_score": 0.2}}' in prompt
    assert report["role"] == "grid_risk"
    assert report["confidence"] == 0.7
    assert report["risk_score"] == 0.3
    assert report["raw"]["summary"] == "ok"


def test_run_once_uses_persist_flag_and_write_signal_boundary(monkeypatch):
    state = {"symbol": "BTCUSDT"}
    runtime = {
        "market": {
            "price": 100.0,
            "candle": {
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.5,
            },
        }
    }
    calls = {}
    written = []

    def fake_read_json(path):
        if path == ai_sidecar.STATE_PATH:
            return state
        if path == ai_sidecar.RUNTIME_PATH:
            return runtime
        raise AssertionError(f"unexpected read: {path}")

    def fake_run_multi_agent_decision(read_state, payload, *, persist):
        calls["state"] = read_state
        calls["payload"] = payload
        calls["persist"] = persist
        return {"decisionId": "d1", "riskAction": "allow_grid"}

    monkeypatch.setattr(ai_sidecar, "_read_json", fake_read_json)
    monkeypatch.setattr(
        ai_sidecar,
        "_run_multi_agent_decision",
        fake_run_multi_agent_decision,
    )
    monkeypatch.setattr(
        ai_sidecar,
        "_write_ai_signal",
        lambda decision: written.append(decision),
    )

    decision = ai_sidecar._run_once(persist=False, write_signal=True)

    assert decision == {"decisionId": "d1", "riskAction": "allow_grid"}
    assert calls["state"] is state
    assert calls["persist"] is False
    assert calls["payload"]["price"] == 100.0
    assert written == [decision]


def test_run_once_raises_when_no_payload(monkeypatch):
    def fake_read_json(path):
        if path == ai_sidecar.STATE_PATH:
            return {"symbol": "BTCUSDT"}
        if path == ai_sidecar.RUNTIME_PATH:
            return {"market": {}}
        if path == str(ai_sidecar.STATUS_PATH):
            return {}
        raise AssertionError(f"unexpected read: {path}")

    monkeypatch.setattr(ai_sidecar, "_read_json", fake_read_json)
    monkeypatch.setattr(
        ai_sidecar,
        "_run_multi_agent_decision",
        lambda *args, **kwargs: pytest.fail("unexpected model decision"),
    )

    with pytest.raises(RuntimeError, match="No runtime payload"):
        ai_sidecar._run_once(persist=False, write_signal=False)


def test_run_once_skips_signal_write_when_not_requested(monkeypatch):
    def fake_read_json(path):
        if path == ai_sidecar.STATE_PATH:
            return {"symbol": "BTCUSDT"}
        if path == ai_sidecar.RUNTIME_PATH:
            return {"market": {"price": 100.0}}
        raise AssertionError(f"unexpected read: {path}")

    monkeypatch.setattr(ai_sidecar, "_read_json", fake_read_json)
    monkeypatch.setattr(
        ai_sidecar,
        "_run_multi_agent_decision",
        lambda state, payload, *, persist: {"decisionId": "d1", "persist": persist},
    )
    monkeypatch.setattr(
        ai_sidecar,
        "_write_ai_signal",
        lambda decision: pytest.fail("unexpected signal write"),
    )

    decision = ai_sidecar._run_once(persist=True, write_signal=False)

    assert decision == {"decisionId": "d1", "persist": True}


def test_query_model_delegates_with_persist_true(monkeypatch):
    calls = []

    def fake_run_multi_agent_decision(state, payload, *, persist):
        calls.append({"state": state, "payload": payload, "persist": persist})
        return {"decisionId": "d1"}

    monkeypatch.setattr(
        ai_sidecar,
        "_run_multi_agent_decision",
        fake_run_multi_agent_decision,
    )
    state = {"symbol": "BTCUSDT"}
    payload = _minimal_payload()

    assert ai_sidecar._query_model(state, payload) == {"decisionId": "d1"}
    assert calls == [{"state": state, "payload": payload, "persist": True}]


def test_main_once_runs_one_review_and_prints_json(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("sys.argv", ["ai_sidecar.py", "--once", "--write-signal"])
    monkeypatch.setattr(
        ai_sidecar,
        "_run_once",
        lambda **kwargs: calls.append(kwargs) or {"decisionId": "d1"},
    )

    ai_sidecar.main()

    assert calls == [{"persist": True, "write_signal": True}]
    assert json.loads(capsys.readouterr().out) == {"decisionId": "d1"}


def test_main_disabled_loop_writes_disabled_signal_and_sleeps(monkeypatch):
    logs, written = _patch_main_loop_io(monkeypatch, state={"aiEnabled": False})

    with pytest.raises(_StopSidecarLoop) as exc:
        ai_sidecar.main()

    assert exc.value.seconds == 2
    assert logs == ["BOOT", "DISABLED"]
    assert written[0]["enabled"] is False
    assert written[0]["source"] == "disabled"


def test_main_no_payload_logs_and_sleeps_without_signal_write(monkeypatch):
    logs, written = _patch_main_loop_io(monkeypatch, state={"aiEnabled": True})
    monkeypatch.setattr(ai_sidecar, "_build_payload", lambda state, runtime: None)

    with pytest.raises(_StopSidecarLoop) as exc:
        ai_sidecar.main()

    assert exc.value.seconds == 2
    assert logs == ["BOOT", "NO_PAYLOAD"]
    assert written == []


def test_main_disabled_loop_continue_restarts_state_read(monkeypatch):
    logs = []
    written = []
    sleeps = []
    state_reads = 0

    def fake_read_json(path):
        nonlocal state_reads
        if path == ai_sidecar.STATE_PATH:
            state_reads += 1
            if state_reads == 1:
                return {"aiEnabled": False}
            raise _StopAfterContinue
        raise AssertionError(f"unexpected read: {path}")

    def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("sys.argv", ["ai_sidecar.py"])
    monkeypatch.setattr(ai_sidecar, "_read_json", fake_read_json)
    monkeypatch.setattr(ai_sidecar, "_log", logs.append)
    monkeypatch.setattr(ai_sidecar, "_write_ai_signal", written.append)
    monkeypatch.setattr(ai_sidecar.time, "sleep", fake_sleep)

    with pytest.raises(_StopAfterContinue):
        ai_sidecar.main()

    assert state_reads == 2
    assert sleeps == [2]
    assert logs == ["BOOT", "DISABLED"]
    assert written[0]["enabled"] is False
    assert written[0]["source"] == "disabled"


def test_main_no_payload_loop_continue_restarts_state_read(monkeypatch):
    logs = []
    written = []
    sleeps = []
    state_reads = 0

    def fake_read_json(path):
        nonlocal state_reads
        if path == ai_sidecar.STATE_PATH:
            state_reads += 1
            if state_reads == 1:
                return {"aiEnabled": True}
            raise _StopAfterContinue
        if path == ai_sidecar.RUNTIME_PATH:
            return {}
        raise AssertionError(f"unexpected read: {path}")

    def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("sys.argv", ["ai_sidecar.py"])
    monkeypatch.setattr(ai_sidecar, "_read_json", fake_read_json)
    monkeypatch.setattr(ai_sidecar, "_build_payload", lambda state, runtime: None)
    monkeypatch.setattr(ai_sidecar, "_log", logs.append)
    monkeypatch.setattr(ai_sidecar, "_write_ai_signal", written.append)
    monkeypatch.setattr(ai_sidecar.time, "sleep", fake_sleep)

    with pytest.raises(_StopAfterContinue):
        ai_sidecar.main()

    assert state_reads == 2
    assert sleeps == [2]
    assert logs == ["BOOT", "NO_PAYLOAD"]
    assert written == []


def test_main_success_writes_signal_and_uses_configured_poll(monkeypatch):
    state = {"aiEnabled": True, "aiPollSeconds": 3}
    payload = _minimal_payload()
    result = {"model": "m", "riskAction": "allow_grid", "gridAllowed": True}
    logs, written = _patch_main_loop_io(monkeypatch, state=state)
    monkeypatch.setattr(ai_sidecar, "_build_payload", lambda state, runtime: payload)
    monkeypatch.setattr(ai_sidecar, "_query_model", lambda state, payload: result)

    with pytest.raises(_StopSidecarLoop) as exc:
        ai_sidecar.main()

    assert exc.value.seconds == 3.0
    assert written == [result]
    assert logs == [
        "BOOT",
        "SIGNAL_WRITTEN model=m action=allow_grid stale=False gridAllowed=True",
    ]


def test_main_ai_disabled_exception_writes_disabled_signal(monkeypatch):
    logs, written = _patch_main_loop_io(
        monkeypatch,
        state={"aiEnabled": True, "aiPollSeconds": 4},
    )
    monkeypatch.setattr(ai_sidecar, "_build_payload", lambda state, runtime: _minimal_payload())

    def disabled(state, payload):
        raise ai_sidecar.AiDisabled("off")

    monkeypatch.setattr(ai_sidecar, "_query_model", disabled)

    with pytest.raises(_StopSidecarLoop) as exc:
        ai_sidecar.main()

    assert exc.value.seconds == 4.0
    assert logs == ["BOOT", "DISABLED"]
    assert written[0]["enabled"] is False
    assert written[0]["source"] == "disabled"


def test_main_generic_exception_marks_existing_signal_stale(monkeypatch):
    state = {
        "aiEnabled": True,
        "aiPollSeconds": "2.5",
        "aiProvider": "test-provider",
        "aiModel": "test-model",
    }
    logs, written = _patch_main_loop_io(monkeypatch, state=state)
    monkeypatch.setattr(ai_sidecar, "_build_payload", lambda state, runtime: _minimal_payload())
    monkeypatch.setattr(ai_sidecar, "_read_ai_signal", lambda: {"decisionId": "old"})

    def fail_query(state, payload):
        raise RuntimeError("model down")

    monkeypatch.setattr(ai_sidecar, "_query_model", fail_query)

    with pytest.raises(_StopSidecarLoop) as exc:
        ai_sidecar.main()

    assert exc.value.seconds == 2.5
    assert written[0]["decisionId"] == "old"
    assert written[0]["enabled"] is True
    assert written[0]["provider"] == "test-provider"
    assert written[0]["model"] == "test-model"
    assert written[0]["error"] == "model down"
    assert written[0]["stale"] is True
    assert written[0]["tsUtc"]
    assert logs == ["BOOT", "REQUEST_ERROR model=test-model error=model down"]


def test_run_multi_agent_decision_refreshes_and_injects_lessons(monkeypatch):
    lesson_calls = []
    agent_calls = []
    portfolio_prompts = []
    appended = []

    monkeypatch.setattr(ai_sidecar, "_log", lambda msg: None)
    monkeypatch.setattr(ai_sidecar, "_ensure_ai_enabled", lambda: None)
    monkeypatch.setattr(
        ai_sidecar,
        "update_lessons_from_trades",
        lambda: lesson_calls.append("updated") or 2,
    )

    def fake_recent_lessons(symbol):
        lesson_calls.append(symbol)
        return "LESSON TEXT"

    def fake_model_config(state):
        return {
            "provider": "local",
            "host": "http://unused",
            "model": "deep",
            "quick_model": "quick",
            "deep_model": "deep",
            "fallback_model": "",
            "timeout_s": 2.0,
        }

    def fake_load_template(name):
        if name == "portfolio_manager":
            return "portfolio {role} {payload_json} {lessons} {reports_json}"
        return "agent {role} {payload_json} {lessons} {reports_json}"

    def fake_run_agent(*, role, template, state, payload, model, lessons, reports=None):
        agent_calls.append(
            {
                "role": role,
                "template": template,
                "model": model,
                "lessons": lessons,
                "reports_seen": sorted((reports or {}).keys()),
            },
        )
        return {
            "role": role,
            "summary": "",
            "confidence": 0.5,
            "risk_score": 0.1,
            "recommendation": "hold",
            "evidence": [],
            "raw": {},
        }

    def fake_chat_json(*, state, model, messages, max_tokens=500):
        portfolio_prompts.append(messages[-1]["content"])
        return {
            "riskAction": "allow_grid",
            "confidence": 0.9,
            "recommendedSpacingPct": 0.01,
            "recommendedLevels": 12,
            "recommendedMaxExposurePct": 0.25,
            "riskBudgetPct": 0.25,
        }

    monkeypatch.setattr(ai_sidecar, "recent_lessons", fake_recent_lessons)
    monkeypatch.setattr(ai_sidecar, "_model_config", fake_model_config)
    monkeypatch.setattr(ai_sidecar, "_load_template", fake_load_template)
    monkeypatch.setattr(ai_sidecar, "_run_agent", fake_run_agent)
    monkeypatch.setattr(ai_sidecar, "_chat_json", fake_chat_json)
    monkeypatch.setattr(
        ai_sidecar,
        "append_decision",
        lambda *args, **kwargs: appended.append((args, kwargs)),
    )

    decision = ai_sidecar._run_multi_agent_decision(
        {"symbol": "BTCUSDT", "gridMaxExposurePct": 0.35},
        {"symbol": "BTCUSDT", "interval": "1m", "price": 100.0},
        persist=False,
    )

    assert lesson_calls == ["updated", "BTCUSDT"]
    assert [call["role"] for call in agent_calls] == [
        "market_regime",
        "grid_risk",
        "position_risk",
        "execution_guard",
        "bull_case",
        "bear_case",
    ]
    assert all(call["lessons"] == "LESSON TEXT" for call in agent_calls)
    assert agent_calls[0]["reports_seen"] == []
    assert agent_calls[-1]["reports_seen"] == [
        "bull_case",
        "execution_guard",
        "grid_risk",
        "market_regime",
        "position_risk",
    ]
    assert len(portfolio_prompts) == 1
    assert "LESSON TEXT" in portfolio_prompts[0]
    assert decision["source"] == "local_multi_agent"
    assert appended == []


def test_run_multi_agent_decision_uses_synthetic_case_reports_when_templates_missing(
    monkeypatch,
    tmp_path,
):
    _patch_multi_agent_boundaries(monkeypatch)
    monkeypatch.setattr(ai_sidecar, "TEMPLATE_DIR", tmp_path)
    monkeypatch.setattr(
        ai_sidecar,
        "_chat_json",
        lambda **kwargs: {
            "riskAction": "allow_grid",
            "confidence": 0.9,
            "recommendedSpacingPct": 0.01,
            "recommendedLevels": 12,
            "recommendedMaxExposurePct": 0.25,
            "riskBudgetPct": 0.25,
        },
    )

    decision = ai_sidecar._run_multi_agent_decision(
        {"symbol": "BTCUSDT", "gridMaxExposurePct": 0.35},
        _minimal_payload() | {"openOrders": 3, "hasOpenPosition": False},
        persist=False,
    )

    assert decision["reports"]["bull_case"]["raw"]["source"] == "deterministic_case_review"
    assert decision["reports"]["bear_case"]["raw"]["source"] == "deterministic_case_review"


def test_run_multi_agent_decision_persist_true_appends_local_decision(monkeypatch):
    _patch_multi_agent_boundaries(monkeypatch)
    appended = []

    def fake_append(decision, *, prompts, reports):
        appended.append({"decision": decision, "prompts": prompts, "reports": reports})

    monkeypatch.setattr(ai_sidecar, "append_decision", fake_append)
    monkeypatch.setattr(
        ai_sidecar,
        "_chat_json",
        lambda **kwargs: {
            "riskAction": "allow_grid",
            "confidence": 0.9,
            "recommendedSpacingPct": 0.01,
            "recommendedLevels": 12,
            "recommendedMaxExposurePct": 0.25,
            "riskBudgetPct": 0.25,
        },
    )

    decision = ai_sidecar._run_multi_agent_decision(
        {"symbol": "BTCUSDT", "gridMaxExposurePct": 0.35},
        _minimal_payload(),
        persist=True,
    )

    assert len(appended) == 1
    assert appended[0]["decision"] == decision
    assert "portfolio_manager" in appended[0]["prompts"]
    assert "market_regime" in appended[0]["reports"]


def test_run_multi_agent_decision_uses_fallback_model(monkeypatch):
    _patch_multi_agent_boundaries(monkeypatch)
    state = {
        "symbol": "BTCUSDT",
        "aiProvider": "openai",
        "aiBaseUrl": "http://model.local/v1",
        "aiModel": "primary",
        "aiQuickModel": "quick",
        "aiDeepModel": "deep",
        "aiFallbackModel": "fallback",
        "gridMaxExposurePct": 0.35,
    }
    chat_calls = []
    appended = []

    def fake_chat_json(*, state, model, messages, max_tokens=500):
        chat_calls.append({"state": state, "model": model, "max_tokens": max_tokens})
        if model == "deep":
            raise RuntimeError("primary portfolio down")
        assert model == "fallback"
        return {
            "riskAction": "pause_new_buys",
            "regime": "range",
            "directionBias": "neutral",
            "confidence": 0.9,
            "breakoutRisk": 0.2,
            "gridAllowed": False,
            "pauseNewBuys": True,
            "riskBudgetPct": 0.2,
            "recommendedSpacingPct": 0.01,
            "recommendedLevels": 10,
            "recommendedMaxExposurePct": 0.2,
            "recommendedMode": "scalpy",
            "rationale": "fallback ok",
            "keyRisks": [],
        }

    monkeypatch.setattr(ai_sidecar, "_chat_json", fake_chat_json)
    monkeypatch.setattr(
        ai_sidecar,
        "append_decision",
        lambda *args, **kwargs: appended.append((args, kwargs)),
    )

    decision = ai_sidecar._run_multi_agent_decision(
        state,
        _minimal_payload(),
        persist=False,
    )

    assert [call["model"] for call in chat_calls] == ["deep", "fallback"]
    assert chat_calls[1]["state"]["aiQuickModel"] == "fallback"
    assert decision["source"] == "local_ai_fallback_model"
    assert decision["fallbackFrom"] == "deep"
    assert decision["model"] == "fallback"
    assert decision["quickModel"] == "fallback"
    assert decision["deepModel"] == "fallback"
    assert decision["riskAction"] == "pause_new_buys"
    assert appended == []


def test_run_multi_agent_decision_persist_true_appends_fallback_model_decision(
    monkeypatch,
):
    _patch_multi_agent_boundaries(monkeypatch)
    appended = []

    def fake_chat_json(*, state, model, messages, max_tokens=500):
        if model == "deep":
            raise RuntimeError("primary portfolio down")
        return {
            "riskAction": "pause_new_buys",
            "confidence": 0.9,
            "gridAllowed": False,
            "pauseNewBuys": True,
            "riskBudgetPct": 0.2,
            "recommendedSpacingPct": 0.01,
            "recommendedLevels": 10,
            "recommendedMaxExposurePct": 0.2,
            "recommendedMode": "scalpy",
        }

    def fake_append(decision, *, prompts, reports):
        appended.append({"decision": decision, "prompts": prompts, "reports": reports})

    monkeypatch.setattr(ai_sidecar, "_chat_json", fake_chat_json)
    monkeypatch.setattr(ai_sidecar, "append_decision", fake_append)

    decision = ai_sidecar._run_multi_agent_decision(
        {
            "symbol": "BTCUSDT",
            "aiQuickModel": "quick",
            "aiDeepModel": "deep",
            "aiFallbackModel": "fallback",
            "gridMaxExposurePct": 0.35,
        },
        _minimal_payload(),
        persist=True,
    )

    assert decision["source"] == "local_ai_fallback_model"
    assert len(appended) == 1
    assert appended[0]["decision"] == decision
    assert "portfolio_manager" in appended[0]["prompts"]
    assert "market_regime" in appended[0]["reports"]


def test_run_multi_agent_decision_uses_deterministic_fallback(monkeypatch):
    _patch_multi_agent_boundaries(monkeypatch)
    state = {
        "symbol": "BTCUSDT",
        "aiQuickModel": "quick",
        "aiDeepModel": "deep",
        "aiFallbackModel": "fallback",
        "gridMaxExposurePct": 0.35,
    }
    chat_models = []
    appended = []

    def fake_chat_json(*, state, model, messages, max_tokens=500):
        chat_models.append(model)
        raise RuntimeError(f"{model} down")

    monkeypatch.setattr(ai_sidecar, "_chat_json", fake_chat_json)
    monkeypatch.setattr(
        ai_sidecar,
        "append_decision",
        lambda *args, **kwargs: appended.append((args, kwargs)),
    )

    decision = ai_sidecar._run_multi_agent_decision(
        state,
        _minimal_payload(),
        persist=False,
    )

    assert chat_models == ["deep", "fallback"]
    assert decision["source"] == "deterministic_fallback"
    assert decision["error"] == "fallback down"
    assert decision["keyRisks"] == ["fallback down"]
    assert appended == []


def test_run_multi_agent_decision_reraises_ai_disabled(monkeypatch):
    logs = []

    def disabled():
        raise ai_sidecar.AiDisabled("off")

    monkeypatch.setattr(ai_sidecar, "_log", logs.append)
    monkeypatch.setattr(ai_sidecar, "_ensure_ai_enabled", disabled)
    monkeypatch.setattr(ai_sidecar, "update_lessons_from_trades", lambda: 0)
    monkeypatch.setattr(ai_sidecar, "recent_lessons", lambda symbol: "LESSONS")
    monkeypatch.setattr(
        ai_sidecar,
        "_chat_json",
        lambda **kwargs: pytest.fail("unexpected model call"),
    )

    with pytest.raises(ai_sidecar.AiDisabled, match="off"):
        ai_sidecar._run_multi_agent_decision(
            {"symbol": "BTCUSDT"},
            _minimal_payload(),
            persist=False,
        )

    assert logs == ["ABORT_DISABLED"]
