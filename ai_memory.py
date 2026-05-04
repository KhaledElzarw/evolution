import json
from pathlib import Path
from typing import Any

from ai_schemas import utc_now


BASE_DIR = Path(__file__).resolve().parent
AI_DECISIONS_PATH = BASE_DIR / "ai_decisions.jsonl"
AI_MEMORY_PATH = BASE_DIR / "ai_memory.json"
TRADES_PATH = BASE_DIR / "trades.jsonl"


def _read_json(path: Path, default):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def append_decision(decision: dict, *, prompts: dict | None = None, reports: dict | None = None) -> None:
    row = {
        "tsUtc": utc_now(),
        "decisionId": decision.get("decisionId"),
        "symbol": decision.get("symbol"),
        "model": decision.get("model"),
        "quickModel": decision.get("quickModel"),
        "deepModel": decision.get("deepModel"),
        "promptVersion": decision.get("promptVersion"),
        "promptHash": decision.get("promptHash"),
        "latencySeconds": decision.get("latencySeconds"),
        "decision": decision,
        "reports": reports or decision.get("reports", {}),
        "promptHashes": {k: decision.get("promptHash") for k in (prompts or {}).keys()},
    }
    with AI_DECISIONS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def load_memory() -> dict:
    return _read_json(AI_MEMORY_PATH, {"lessons": [], "resolvedTradeEventKeys": []})


def save_memory(memory: dict) -> None:
    lessons = memory.get("lessons", [])[-200:]
    memory["lessons"] = lessons
    _write_json(AI_MEMORY_PATH, memory)


def recent_lessons(symbol: str, limit: int = 6) -> str:
    memory = load_memory()
    lessons = [x for x in memory.get("lessons", []) if x.get("symbol") == symbol]
    if not lessons:
        return "No prior local AI lessons yet."
    out = []
    for item in lessons[-limit:]:
        pnl = float(item.get("realizedPnlUsdt") or 0.0)
        out.append(
            f"- {item.get('tsUtc')}: decision={item.get('decisionId')} action={item.get('riskAction')} "
            f"pnl={pnl:.4f} note={item.get('lesson')}"
        )
    return "\n".join(out)


def _read_trade_events() -> list[dict]:
    if not TRADES_PATH.exists():
        return []
    rows = []
    with TRADES_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def update_lessons_from_trades() -> int:
    memory = load_memory()
    seen = set(memory.get("resolvedTradeEventKeys", []))
    lessons = list(memory.get("lessons", []))
    added = 0
    for event in _read_trade_events():
        if event.get("event") != "EXIT":
            continue
        decision_id = event.get("aiDecisionId")
        if not decision_id:
            continue
        key = f"{event.get('tsUtc')}|{decision_id}|{event.get('qtyBtc')}"
        if key in seen:
            continue
        pnl = float(event.get("realizedPnlUsdt") or 0.0)
        lesson = "AI-aligned exit was profitable; keep similar risk posture." if pnl >= 0 else "AI-aligned exit lost money; prefer smaller exposure or wider spacing in similar conditions."
        lessons.append({
            "tsUtc": utc_now(),
            "symbol": event.get("symbol", "BTCUSDT"),
            "decisionId": decision_id,
            "riskAction": event.get("aiRiskAction"),
            "realizedPnlUsdt": pnl,
            "lesson": lesson,
            "event": event,
        })
        seen.add(key)
        added += 1
    if added:
        memory["lessons"] = lessons
        memory["resolvedTradeEventKeys"] = sorted(seen)[-500:]
        save_memory(memory)
    return added
