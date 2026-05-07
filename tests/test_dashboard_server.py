import json
from urllib.parse import urlparse

import dashboard_server


class DummyHandler:
    def __init__(self, headers):
        self.headers = headers


def test_coerce_state_patch_validates_bounds():
    patch = dashboard_server.coerce_state_patch({"maxDailyLossPct": "0.25", "gridLevels": "12"})

    assert patch == {"gridLevels": 12, "maxDailyLossPct": 0.25}


def test_coerce_state_patch_accepts_local_ai_fields():
    patch = dashboard_server.coerce_state_patch({
        "aiProvider": "ollama",
        "aiBaseUrl": "http://127.0.0.1:11434/v1",
        "aiQuickModel": "qwen2.5:3b",
        "aiDeepModel": "qwen3.6:35b",
        "aiDryRun": "true",
        "aiShadowMode": "false",
        "aiTimeoutSeconds": "120",
        "aiTemperature": "0.2",
    })

    assert patch["aiProvider"] == "ollama"
    assert patch["aiDryRun"] is True
    assert patch["aiShadowMode"] is False
    assert patch["aiTimeoutSeconds"] == 120.0


def test_coerce_state_patch_applies_named_ai_endpoint():
    patch = dashboard_server.coerce_state_patch({
        "aiEndpointKey": "battlestation_gpu",
        "aiProvider": "openai",
        "aiBaseUrl": "http://stale-host:9999/v1",
    })

    assert patch["aiEndpointKey"] == "battlestation_gpu"
    assert patch["aiProvider"] == "ollama"
    assert patch["aiBaseUrl"] == "http://192.168.1.20:11435/v1"


def test_coerce_state_patch_keeps_custom_ai_endpoint_url():
    patch = dashboard_server.coerce_state_patch({
        "aiEndpointKey": "custom",
        "aiBaseUrl": "http://10.0.0.5:11434/v1",
    })

    assert patch["aiEndpointKey"] == "custom"
    assert patch["aiProvider"] == "ollama"
    assert patch["aiBaseUrl"] == "http://10.0.0.5:11434/v1"


def test_infer_ai_endpoint_treats_stale_preset_key_as_custom():
    endpoint_key = dashboard_server.infer_ai_endpoint_key({
        "aiEndpointKey": "local",
        "aiBaseUrl": "http://10.0.0.5:11434/v1",
    })

    assert endpoint_key == "custom"


def test_get_ai_endpoint_models_lists_all_named_endpoints(monkeypatch):
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    def fake_get(url, timeout):
        if "11434" in url:
            return Response({"data": [{"id": "qwen3.5:9b"}]})
        if "11435" in url:
            return Response({"models": [{"name": "qwen3.6:35b"}]})
        if "11436" in url:
            return Response({"models": [{"model": "llama3.2:3b"}]})
        raise RuntimeError(url)

    monkeypatch.setattr(dashboard_server.requests, "get", fake_get)
    dashboard_server._ai_model_cache.clear()

    models = dashboard_server.get_ai_endpoint_models({
        "aiEndpointKey": "local",
        "aiBaseUrl": "http://127.0.0.1:11434/v1",
    })

    assert models["local"] == ["qwen3.5:9b"]
    assert models["battlestation_gpu"] == ["qwen3.6:35b"]
    assert models["battlestation_cpu"] == ["llama3.2:3b"]


def test_coerce_state_patch_rejects_bad_choice():
    try:
        dashboard_server.coerce_state_patch({"gridMode": "chaos"})
    except ValueError as exc:
        assert "gridMode must be one of" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_coerce_state_patch_rejects_flexy_grid_mode():
    try:
        dashboard_server.coerce_state_patch({"gridMode": "flexy"})
    except ValueError as exc:
        assert "gridMode must be one of" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_dashboard_mode_label_hides_legacy_unsupported_modes():
    assert (
        dashboard_server.dashboard_mode_label({"gridMode": "scalpy", "aiEnabled": True})
        == "Scalpy + Local AI"
    )
    assert (
        dashboard_server.dashboard_mode_label({"gridMode": "fatty", "aiEnabled": False})
        == "Fatty + Rules"
    )
    assert (
        dashboard_server.dashboard_mode_label({"gridMode": "flexy", "aiEnabled": True})
        == "Optimized AI"
    )
    assert dashboard_server.dashboard_mode_label({"gridMode": "ai_optimized", "aiEnabled": False}) == "Rules"
    assert (
        dashboard_server.dashboard_mode_label({"gridMode": "legacy", "aiEnabled": True})
        == "Grid + Local AI"
    )


def test_read_ai_decisions_missing_and_empty_file(monkeypatch, tmp_path):
    decisions_path = tmp_path / "ai_decisions.jsonl"
    monkeypatch.setattr(dashboard_server, "AI_DECISIONS_PATH", decisions_path)

    assert dashboard_server.read_ai_decisions() == []

    decisions_path.write_text("", encoding="utf-8")

    assert dashboard_server.read_ai_decisions() == []


def test_read_ai_decisions_skips_malformed_and_normalizes_nested_and_flat_rows(
    monkeypatch,
    tmp_path,
):
    decisions_path = tmp_path / "ai_decisions.jsonl"
    decisions_path.write_text(
        "\n".join([
            "{malformed",
            json.dumps({
                "decision": {
                    "decisionId": "old-nested",
                    "tsUtc": "2026-05-01T00:00:00+00:00",
                    "riskAction": "allow_grid",
                    "confidence": 0.42,
                    "recommendedSpacingPct": 0.01,
                    "recommendedLevels": 5,
                    "recommendedMaxExposurePct": 0.35,
                    "raw": {"equityUsdt": 90, "price": 100, "closedTrades": 1},
                    "strategyProfile": {
                        "name": "Scalpy",
                        "spacingPct": 0.01,
                        "levels": 5,
                        "secretToken": "not-for-display",
                    },
                    "validationReport": {
                        "passed": False,
                        "mode": "shadow",
                        "sampleCount": 12,
                        "secretToken": "not-for-display",
                    },
                    "reports": {
                        "risk_manager": {
                            "recommendation": "<hold>",
                            "evidence": ["watch drawdown"],
                            "summary": "Keep exposure stable",
                        }
                    },
                }
            }),
            json.dumps({
                "decisionId": "new-flat",
                "tsUtc": "2026-05-01T00:01:00+00:00",
                "riskAction": "pause_new_buys",
                "note": "flat row is displayable",
            }),
        ])
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_server, "AI_DECISIONS_PATH", decisions_path)

    rows = dashboard_server.read_ai_decisions(
        {"equityUsdt": 99, "price": 110},
        {"trades": 3},
        limit=10,
    )

    assert [row["decisionId"] for row in rows] == ["new-flat", "old-nested"]
    nested = rows[1]
    assert nested["agents"][0]["role"] == "risk_manager"
    assert nested["agents"][0]["recommendation"] == "<hold>"
    assert nested["strategyProfile"] == {"name": "Scalpy", "spacingPct": 0.01, "levels": 5}
    assert nested["validationReport"] == {
        "passed": False,
        "mode": "shadow",
        "sampleCount": 12,
    }
    assert nested["realizedImpact"]["equityDeltaUsdt"] == 9.0
    assert nested["realizedImpact"]["tradeDelta"] == 2


def test_read_ai_decisions_caps_and_orders_newest_first(monkeypatch, tmp_path):
    decisions_path = tmp_path / "ai_decisions.jsonl"
    decisions_path.write_text(
        "\n".join(
            json.dumps({"decisionId": f"decision-{idx}", "riskAction": "allow_grid"})
            for idx in range(60)
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_server, "AI_DECISIONS_PATH", decisions_path)

    rows = dashboard_server.read_ai_decisions(limit=1000)

    assert len(rows) == 50
    assert rows[0]["decisionId"] == "decision-59"
    assert rows[-1]["decisionId"] == "decision-10"


def test_read_ai_decisions_covers_malformed_runtime_edges(monkeypatch, tmp_path):
    decisions_path = tmp_path / "ai_decisions.jsonl"
    decisions_path.write_text(
        "\n".join([
            "",
            "[]",
            json.dumps({"unrelated": True}),
            json.dumps({
                "decisionId": "edge-row",
                "riskAction": "allow_grid",
                "reports": "bad-shape",
                "keyRisks": "single risk",
            }),
        ])
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard_server, "AI_DECISIONS_PATH", decisions_path)

    rows = dashboard_server.read_ai_decisions(limit="not-a-number")

    assert len(rows) == 1
    assert rows[0]["decisionId"] == "edge-row"
    assert rows[0]["agents"] == []
    assert rows[0]["keyRisks"] == ["single risk"]


def test_read_ai_decisions_returns_empty_when_file_read_fails(monkeypatch):
    class BrokenDecisionsPath:
        def exists(self):
            return True

        def is_file(self):
            return True

        def read_text(self, **kwargs):
            raise OSError("cannot read")

    monkeypatch.setattr(dashboard_server, "AI_DECISIONS_PATH", BrokenDecisionsPath())

    assert dashboard_server.read_ai_decisions() == []


def test_normalized_ai_decision_row_rejects_duck_typed_non_dict():
    class RowLike:
        def get(self, key):
            return None

    assert dashboard_server._normalized_ai_decision_row(RowLike(), {}, {}) is None


def test_ai_decision_impact_helpers_tolerate_missing_values():
    realized = dashboard_server._realized_impact({}, {}, {})

    assert dashboard_server._projected_impact({}).startswith("Hold current")
    assert dashboard_server._projected_impact({"riskAction": "sells_only"}).startswith("Let sell-side")
    assert dashboard_server._projected_impact({
        "riskAction": "reduce_exposure",
        "recommendedMaxExposurePct": 0.25,
    }).startswith("Lower exposure budget toward 25.0%")
    assert dashboard_server._projected_impact({"riskAction": "flatten"}).startswith("Recommend exiting")
    assert dashboard_server._pct_delta(10, 0) is None
    assert dashboard_server._safe_num("nan", 7.0) == 7.0
    assert dashboard_server._safe_text("   ") is None
    assert dashboard_server._safe_text_list("single risk") == ["single risk"]
    assert dashboard_server._agent_report_summary("risk_manager", {})["evidence"] == []
    assert realized["equityDeltaUsdt"] is None
    assert realized["priceDeltaPct"] is None


def test_mutation_auth_allows_when_token_unset(monkeypatch):
    monkeypatch.setattr(dashboard_server, "DASHBOARD_TOKEN", "")

    assert dashboard_server.Handler._mutation_authorized(DummyHandler({}), urlparse("/api/config"))


def test_mutation_auth_checks_header_and_query(monkeypatch):
    monkeypatch.setattr(dashboard_server, "DASHBOARD_TOKEN", "secret")

    assert dashboard_server.Handler._mutation_authorized(
        DummyHandler({"X-Tradebot-Token": "secret"}),
        urlparse("/api/config"),
    )
    assert dashboard_server.Handler._mutation_authorized(DummyHandler({}), urlparse("/api/config?token=secret"))
    assert not dashboard_server.Handler._mutation_authorized(
        DummyHandler({"X-Tradebot-Token": "wrong"}),
        urlparse("/api/config"),
    )


def test_market_payload_can_skip_ohlcv_for_status_poll(monkeypatch, tmp_path):
    def fake_read_json(path):
        if path == dashboard_server.STATUS_PATH:
            return {"symbol": "BTCUSDT", "price": 100.0, "tsUtc": "2026-05-01T00:00:00+00:00"}
        if path == dashboard_server.STATE_PATH:
            return {"symbol": "BTCUSDT", "interval": "1s"}
        return {}

    def fail_get_ohlcv(*args, **kwargs):
        raise AssertionError("ohlcv should not be fetched")

    monkeypatch.setattr(dashboard_server, "read_json", fake_read_json)
    monkeypatch.setattr(dashboard_server, "get_ohlcv", fail_get_ohlcv)
    monkeypatch.setattr(dashboard_server, "read_events", lambda: [{"event": "ENTER", "price": 100.0}])
    monkeypatch.setattr(dashboard_server, "freshness_seconds", lambda ts: 0.5)
    monkeypatch.setattr(dashboard_server, "AI_DECISIONS_PATH", tmp_path / "missing.jsonl")

    payload = dashboard_server.build_market_payload({"interval": ["1s"], "ohlcv": ["0"]})

    assert payload["status"]["price"] == 100.0
    assert payload["ohlcv"] == []
    assert payload["events"] == [{"event": "ENTER", "price": 100.0}]
    assert payload["aiDecisions"] == []
    assert payload["chartInterval"] == "1s"
    assert payload["refreshMs"] >= 1000


def test_market_payload_includes_ai_decisions(monkeypatch, tmp_path):
    decisions_path = tmp_path / "ai_decisions.jsonl"
    decisions_path.write_text(
        json.dumps({"decisionId": "payload-decision", "riskAction": "allow_grid"}) + "\n",
        encoding="utf-8",
    )

    def fake_read_json(path):
        if path == dashboard_server.STATUS_PATH:
            return {"symbol": "BTCUSDT", "price": 100.0, "tsUtc": "2026-05-01T00:00:00+00:00"}
        if path == dashboard_server.STATE_PATH:
            return {"symbol": "BTCUSDT", "interval": "1s"}
        return {}

    monkeypatch.setattr(dashboard_server, "read_json", fake_read_json)
    monkeypatch.setattr(dashboard_server, "read_events", lambda: [])
    monkeypatch.setattr(dashboard_server, "freshness_seconds", lambda ts: 0.5)
    monkeypatch.setattr(dashboard_server, "AI_DECISIONS_PATH", decisions_path)

    payload = dashboard_server.build_market_payload({"interval": ["1s"], "ohlcv": ["0"]})

    assert payload["aiDecisions"][0]["decisionId"] == "payload-decision"


def test_market_payload_uses_fresh_status_price_for_active_candle(monkeypatch):
    def fake_read_json(path):
        if path == dashboard_server.STATUS_PATH:
            return {
                "symbol": "BTCUSDT",
                "price": 100.0,
                "usdt": 10.0,
                "btc": 2.0,
                "position": {"qtyBtc": 2.0, "entryPrice": 90.0},
                "tsUtc": "2026-05-01T00:00:00+00:00",
            }
        if path == dashboard_server.STATE_PATH:
            return {"symbol": "BTCUSDT", "interval": "1s"}
        return {}

    def fake_get_ohlcv(*args, **kwargs):
        return [{
            "openTimeMs": 1,
            "open": 99.0,
            "high": 102.0,
            "low": 98.0,
            "close": 101.0,
            "volumeUsdt": 1000.0,
            "symbol": "BTCUSDT",
            "interval": "1s",
        }]

    monkeypatch.setattr(dashboard_server, "read_json", fake_read_json)
    monkeypatch.setattr(dashboard_server, "get_ohlcv", fake_get_ohlcv)
    monkeypatch.setattr(dashboard_server, "read_events", lambda: [])
    monkeypatch.setattr(dashboard_server, "freshness_seconds", lambda ts: 0.5)

    payload = dashboard_server.build_market_payload({"interval": ["1s"], "ohlcv": ["1"]})

    assert payload["ohlcv"][-1]["close"] == 100.0
    assert payload["status"]["price"] == 100.0
    assert payload["status"]["equityUsdt"] == 210.0
    assert payload["status"]["position"]["unrealizedPnlUsdt"] == 20.0


def test_market_payload_applies_fresh_status_price_to_active_candle(monkeypatch):
    def fake_read_json(path):
        if path == dashboard_server.STATUS_PATH:
            return {
                "symbol": "BTCUSDT",
                "price": 105.0,
                "tsUtc": "2026-05-02T00:00:30+00:00",
            }
        if path == dashboard_server.STATE_PATH:
            return {"symbol": "BTCUSDT", "interval": "1m"}
        return {}

    def fake_get_ohlcv(*args, **kwargs):
        return [{
            "openTimeMs": 1777680000000,
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "volumeUsdt": 1000.0,
            "symbol": "BTCUSDT",
            "interval": "1m",
        }]

    monkeypatch.setattr(dashboard_server, "read_json", fake_read_json)
    monkeypatch.setattr(dashboard_server, "get_ohlcv", fake_get_ohlcv)
    monkeypatch.setattr(dashboard_server, "read_events", lambda: [])
    monkeypatch.setattr(dashboard_server, "freshness_seconds", lambda ts: 0.5)

    payload = dashboard_server.build_market_payload({"interval": ["1m"], "ohlcv": ["1"]})

    assert payload["status"]["price"] == 105.0
    assert payload["ohlcv"][-1]["close"] == 105.0
    assert payload["ohlcv"][-1]["high"] == 105.0


def test_ai_endpoint_helpers_normalize_and_resolve_custom_endpoint(monkeypatch):
    assert (
        dashboard_server._normalize_ai_base_url(" http://127.0.0.1:11434/v1/ ")
        == "http://127.0.0.1:11434/v1"
    )
    assert (
        dashboard_server.infer_ai_endpoint_key({
            "aiBaseUrl": " http://127.0.0.1:11434/v1/ ",
        })
        == "local"
    )

    monkeypatch.setenv(
        "TRADEBOT_AI_BASE_URL",
        " http://10.0.0.7:11434/v1/ ",
    )
    endpoint = dashboard_server.active_ai_endpoint({
        "aiEndpointKey": "custom",
        "aiProvider": "openai",
    })

    assert endpoint["key"] == "custom"
    assert endpoint["baseUrl"] == "http://10.0.0.7:11434/v1"
    assert endpoint["provider"] == "openai"


def test_ai_model_candidates_and_name_extraction_cover_payload_shapes():
    assert dashboard_server._ai_model_candidates("") == []
    assert dashboard_server._ai_model_candidates(" http://ai.local/ ") == [
        "http://ai.local/models",
        "http://ai.local/v1/models",
        "http://ai.local/api/tags",
    ]
    assert dashboard_server._ai_model_candidates("http://ai.local/v1") == [
        "http://ai.local/v1/models",
        "http://ai.local/api/tags",
    ]
    assert dashboard_server._extract_ai_model_names({
        "models": [
            "llama3",
            {"name": "qwen"},
            {"model": "mixtral"},
            "llama3",
            None,
        ],
    }) == ["llama3", "qwen", "mixtral"]


def test_fetch_ai_models_uses_candidates_and_cache(monkeypatch):
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    calls = []

    def fake_get(url, timeout):
        calls.append((url, timeout))
        if url == "http://ai.local/models":
            raise RuntimeError("first candidate down")
        return Response({"models": ["qwen3:4b", {"name": "llama3"}]})

    monkeypatch.setattr(dashboard_server.requests, "get", fake_get)
    dashboard_server._ai_model_cache.clear()
    try:
        assert dashboard_server.fetch_ai_models("") == []
        assert dashboard_server.fetch_ai_models(
            "http://ai.local",
            force=True,
        ) == ["qwen3:4b", "llama3"]
        assert [url for url, _timeout in calls] == [
            "http://ai.local/models",
            "http://ai.local/v1/models",
        ]

        calls.clear()
        assert dashboard_server.fetch_ai_models("http://ai.local") == [
            "qwen3:4b",
            "llama3",
        ]
        assert calls == []
    finally:
        dashboard_server._ai_model_cache.clear()


def test_ai_model_payload_helpers_use_active_endpoint(monkeypatch):
    calls = []

    def fake_fetch(base_url, force=False):
        calls.append((base_url, force))
        return [f"model-for-{base_url or 'empty'}"]

    monkeypatch.setattr(dashboard_server, "fetch_ai_models", fake_fetch)
    state = {
        "aiEndpointKey": "custom",
        "aiBaseUrl": "http://custom.local/v1",
        "aiProvider": "openai",
    }

    endpoint, models_by_endpoint = dashboard_server.ai_endpoint_payload(state)

    assert endpoint["key"] == "custom"
    assert endpoint["baseUrl"] == "http://custom.local/v1"
    assert models_by_endpoint["custom"] == [
        "model-for-http://custom.local/v1",
    ]
    assert dashboard_server.get_ai_models(state) == [
        "model-for-http://custom.local/v1",
    ]
    assert any(
        base_url == "http://custom.local/v1"
        for base_url, _force in calls
    )


def test_event_patch_helpers_ignore_bad_event_ids():
    events = [
        {"_eventId": "bad", "event": "skip"},
        {"eventId": 2, "event": "keep"},
    ]

    assert dashboard_server.event_cursor(events) == 2
    patch = dashboard_server.build_event_patch(events, 1)

    assert patch["cursor"] == 2
    assert patch["items"] == [{"eventId": 2, "event": "keep"}]
    assert dashboard_server.build_event_patch(
        events,
        0,
        snapshot=True,
    )["items"] == events


def test_order_patch_helpers_are_stable_and_strip_runtime_orders():
    buy = {
        "side": "BUY",
        "price": 100.0,
        "qty_btc": 0.01,
        "total": 1.0,
        "type": "LIMIT",
    }
    with_id = {**buy, "id": "order-1"}
    explicit_key = dashboard_server.order_patch_key(with_id)

    assert explicit_key != dashboard_server.order_patch_key(buy)
    items = dashboard_server.normalize_order_patch_items([
        {**with_id, "_orderKey": "fixed"},
        buy,
    ])
    assert items[0]["_orderKey"] == "fixed"
    assert "_orderKey" in items[1]
    assert dashboard_server.order_signature([with_id, buy]) == (
        dashboard_server.order_signature([buy, with_id])
    )

    snapshot, previous = dashboard_server.build_order_patch(
        [with_id],
        None,
        snapshot=True,
    )
    assert snapshot["mode"] == "snapshot"
    delta, current = dashboard_server.build_order_patch([with_id], previous)
    assert delta["ops"] == []
    removed, _current = dashboard_server.build_order_patch([], current)
    assert removed["ops"] == [{"op": "remove", "key": explicit_key}]

    runtime = {"grid": {"orders": [with_id], "mode": "scalpy"}, "other": 1}
    stripped = dashboard_server.strip_orders_from_runtime(runtime)

    assert stripped == {"grid": {"mode": "scalpy"}, "other": 1}
    assert "orders" in runtime["grid"]


def test_market_price_helpers_handle_live_price_and_bad_inputs():
    rows = [{"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0}]

    merged = dashboard_server.merge_live_price_into_ohlcv(rows, 98.0)

    assert merged[-1]["close"] == 98.0
    assert merged[-1]["high"] == 102.0
    assert merged[-1]["low"] == 98.0
    assert rows[-1]["close"] == 101.0
    assert dashboard_server.merge_live_price_into_ohlcv([], 98.0) == []
    malformed = dashboard_server.merge_live_price_into_ohlcv(
        [{"high": object(), "low": 1.0, "close": 1.0}],
        2.0,
    )
    assert malformed[-1]["close"] == 2.0
    assert dashboard_server.latest_ohlcv_price([{"close": "bad"}]) is None
    assert dashboard_server.apply_market_price_to_status(
        {"price": 80.0},
        "bad",
    ) == {"price": 80.0}

    priced = dashboard_server.apply_market_price_to_status({
        "usdt": "bad",
        "btc": "1",
        "position": {"qtyBtc": "bad", "entryPrice": "90"},
    }, 100.0)

    assert priced["price"] == 100.0
    assert "equityUsdt" not in priced
    assert "unrealizedPnlUsdt" not in priced["position"]


def test_interval_timestamp_and_status_ohlcv_edge_cases(monkeypatch):
    assert dashboard_server.normalize_interval(None) == "1m"
    assert dashboard_server.normalize_interval("bogus") == "1m"
    assert dashboard_server.interval_duration_ms("bogus") == 60_000
    assert dashboard_server._iso_to_ms(None) is None
    assert dashboard_server._iso_to_ms("1970-01-01T00:00:01Z") == 1000
    assert dashboard_server._iso_to_ms("1970-01-01T00:00:01") == 1000
    assert dashboard_server._iso_to_ms("not a timestamp") is None

    row = {
        "openTimeMs": 120_000,
        "open": 100.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "closeTimeMs": 179_999,
        "symbol": "BTCUSDT",
        "interval": "1m",
    }
    status = {"tsUtc": "1970-01-01T00:01:00+00:00", "price": 105.0}

    assert dashboard_server.apply_status_price_to_ohlcv(
        [],
        status,
        "1m",
    ) == []
    monkeypatch.setattr(dashboard_server, "freshness_seconds", lambda ts: 999.0)
    assert dashboard_server.apply_status_price_to_ohlcv(
        [row],
        status,
        "1m",
    ) == [row]

    monkeypatch.setattr(dashboard_server, "freshness_seconds", lambda ts: 0.1)
    assert dashboard_server.apply_status_price_to_ohlcv(
        [row],
        {**status, "price": "bad"},
        "1m",
    ) == [row]
    assert dashboard_server.apply_status_price_to_ohlcv(
        [row],
        {**status, "price": 0},
        "1m",
    ) == [row]
    assert dashboard_server.apply_status_price_to_ohlcv(
        [row],
        status,
        "1m",
    ) == [row]
