import json

import ai_memory


def test_load_memory_defaults_when_missing_or_invalid(monkeypatch, tmp_path):
    memory_path = tmp_path / "ai_memory.json"
    monkeypatch.setattr(ai_memory, "AI_MEMORY_PATH", memory_path)

    assert ai_memory.load_memory() == {"lessons": [], "resolvedTradeEventKeys": []}

    memory_path.write_text("{not valid json", encoding="utf-8")

    assert ai_memory.load_memory() == {"lessons": [], "resolvedTradeEventKeys": []}


def test_save_memory_trims_lessons_and_preserves_future_keys(monkeypatch, tmp_path):
    memory_path = tmp_path / "ai_memory.json"
    monkeypatch.setattr(ai_memory, "AI_MEMORY_PATH", memory_path)
    memory = {
        "lessons": [{"idx": idx} for idx in range(205)],
        "resolvedTradeEventKeys": ["k1"],
        "agentMemories": {"future-role": {"notes": ["keep me"]}},
    }

    ai_memory.save_memory(memory)

    saved = json.loads(memory_path.read_text(encoding="utf-8"))
    assert [item["idx"] for item in saved["lessons"]] == list(range(5, 205))
    assert saved["resolvedTradeEventKeys"] == ["k1"]
    assert saved["agentMemories"] == {"future-role": {"notes": ["keep me"]}}


def test_recent_lessons_filters_by_symbol_limits_and_formats(monkeypatch):
    monkeypatch.setattr(
        ai_memory,
        "load_memory",
        lambda: {
            "lessons": [
                {
                    "tsUtc": "2026-05-05T00:00:00+00:00",
                    "symbol": "BTCUSDT",
                    "decisionId": "old",
                    "riskAction": "allow_grid",
                    "realizedPnlUsdt": "1.0",
                    "lesson": "old lesson",
                },
                {
                    "tsUtc": "2026-05-05T00:01:00+00:00",
                    "symbol": "ETHUSDT",
                    "decisionId": "eth",
                    "riskAction": "hold",
                    "realizedPnlUsdt": "9.0",
                    "lesson": "other symbol",
                },
                {
                    "tsUtc": "2026-05-05T00:02:00+00:00",
                    "symbol": "BTCUSDT",
                    "decisionId": "recent-1",
                    "riskAction": "sells_only",
                    "realizedPnlUsdt": "-2.5",
                    "lesson": "loss lesson",
                },
                {
                    "tsUtc": "2026-05-05T00:03:00+00:00",
                    "symbol": "BTCUSDT",
                    "decisionId": "recent-2",
                    "riskAction": "reduce_exposure",
                    "realizedPnlUsdt": 3,
                    "lesson": "profit lesson",
                },
            ],
        },
    )

    text = ai_memory.recent_lessons("BTCUSDT", limit=2)

    assert "old lesson" not in text
    assert "other symbol" not in text
    assert (
        "- 2026-05-05T00:02:00+00:00: decision=recent-1 "
        "action=sells_only pnl=-2.5000 note=loss lesson"
    ) in text
    assert (
        "- 2026-05-05T00:03:00+00:00: decision=recent-2 "
        "action=reduce_exposure pnl=3.0000 note=profit lesson"
    ) in text
    assert ai_memory.recent_lessons("SOLUSDT") == "No prior local AI lessons yet."


def test_update_lessons_from_trades_adds_only_new_ai_exit_lessons(
    monkeypatch,
    tmp_path,
):
    memory_path = tmp_path / "ai_memory.json"
    trades_path = tmp_path / "trades.jsonl"
    monkeypatch.setattr(ai_memory, "AI_MEMORY_PATH", memory_path)
    monkeypatch.setattr(ai_memory, "TRADES_PATH", trades_path)
    monkeypatch.setattr(ai_memory, "utc_now", lambda: "2026-05-05T12:00:00+00:00")

    seen_key = "2026-05-05T00:00:00+00:00|seen-decision|0.1"
    memory_path.write_text(
        json.dumps(
            {
                "lessons": [{"symbol": "BTCUSDT", "decisionId": "existing"}],
                "resolvedTradeEventKeys": [seen_key],
                "agentMemories": {"future-role": {"notes": ["keep me"]}},
            },
        ),
        encoding="utf-8",
    )
    profit_event = {
        "event": "EXIT",
        "tsUtc": "2026-05-05T00:01:00+00:00",
        "symbol": "ETHUSDT",
        "aiDecisionId": "profit-decision",
        "aiRiskAction": "allow_grid",
        "qtyBtc": "0.2",
        "realizedPnlUsdt": "1.25",
    }
    loss_event = {
        "event": "EXIT",
        "tsUtc": "2026-05-05T00:02:00+00:00",
        "aiDecisionId": "loss-decision",
        "aiRiskAction": "flatten",
        "qtyBtc": "0.3",
        "realizedPnlUsdt": "-0.50",
    }
    trades_path.write_text(
        "\n".join(
            [
                "",
                "{bad json",
                json.dumps({"event": "ENTRY", "aiDecisionId": "entry-decision"}),
                json.dumps({"event": "EXIT", "aiDecisionId": ""}),
                json.dumps(
                    {
                        "event": "EXIT",
                        "tsUtc": "2026-05-05T00:00:00+00:00",
                        "aiDecisionId": "seen-decision",
                        "qtyBtc": "0.1",
                    },
                ),
                json.dumps(profit_event),
                json.dumps(loss_event),
            ],
        ),
        encoding="utf-8",
    )

    assert ai_memory.update_lessons_from_trades() == 2

    saved = json.loads(memory_path.read_text(encoding="utf-8"))
    assert saved["agentMemories"] == {"future-role": {"notes": ["keep me"]}}
    assert len(saved["lessons"]) == 3
    assert saved["lessons"][1] == {
        "tsUtc": "2026-05-05T12:00:00+00:00",
        "symbol": "ETHUSDT",
        "decisionId": "profit-decision",
        "riskAction": "allow_grid",
        "realizedPnlUsdt": 1.25,
        "lesson": "AI-aligned exit was profitable; keep similar risk posture.",
        "event": profit_event,
    }
    assert saved["lessons"][2]["symbol"] == "BTCUSDT"
    assert saved["lessons"][2]["lesson"] == (
        "AI-aligned exit lost money; prefer smaller exposure or wider spacing in similar conditions."
    )
    assert set(saved["resolvedTradeEventKeys"]) == {
        seen_key,
        "2026-05-05T00:01:00+00:00|profit-decision|0.2",
        "2026-05-05T00:02:00+00:00|loss-decision|0.3",
    }


def test_read_trade_events_returns_empty_when_trade_log_is_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_memory, "TRADES_PATH", tmp_path / "missing-trades.jsonl")

    assert ai_memory._read_trade_events() == []


def test_append_decision_writes_current_jsonl_envelope(monkeypatch, tmp_path):
    decisions_path = tmp_path / "ai_decisions.jsonl"
    monkeypatch.setattr(ai_memory, "AI_DECISIONS_PATH", decisions_path)
    monkeypatch.setattr(ai_memory, "utc_now", lambda: "2026-05-05T12:00:00+00:00")
    decision = {
        "decisionId": "decision-1",
        "symbol": "BTCUSDT",
        "model": "deep-model",
        "quickModel": "quick-model",
        "deepModel": "deep-model",
        "promptVersion": "local-multi-agent-v1",
        "promptHash": "hash-main",
        "latencySeconds": 0.25,
        "reports": {"embedded": {"risk_score": 0.9}},
    }

    ai_memory.append_decision(
        decision,
        prompts={"market_regime": "template text", "portfolio_manager": "template text"},
        reports={"market_regime": {"risk_score": 0.1}},
    )

    row = json.loads(decisions_path.read_text(encoding="utf-8"))
    assert row["tsUtc"] == "2026-05-05T12:00:00+00:00"
    assert row["decisionId"] == "decision-1"
    assert row["symbol"] == "BTCUSDT"
    assert row["decision"] == decision
    assert row["reports"] == {"market_regime": {"risk_score": 0.1}}
    assert row["promptHashes"] == {
        "market_regime": "hash-main",
        "portfolio_manager": "hash-main",
    }
