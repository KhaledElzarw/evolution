# Evolution — Product Evidence Showcase

*A personal, hands-on AI/agentic product build: a locally-run trading automation system with a multi-agent AI decisioning layer, human-in-the-loop controls, and full operational telemetry.*

**Author:** Khaled Elzarw · [linkedin.com/in/KhaledElzarw](https://linkedin.com/in/KhaledElzarw)
**Status:** Working system running in a live server environment (paper / Binance Spot testnet workflows)
**Demo deck (screenshots of the running system):** [Feature demo — Google Slides](https://docs.google.com/presentation/d/1NJo0j8dD0W_Zwuj2pnk6_H2K1wMU1QT2GqfkBxr2qZo/)

---

## What this demonstrates in 60 seconds

This is not a tutorial project. It is a running AI-agentic system I designed, built, and operate end to end, demonstrating the product skills that matter for agentic products:

| Product question | How this build answers it |
|---|---|
| **Where do agents apply judgment?** | A six-agent "decisioning committee" (bear case, bull case, execution guard, grid risk, market regime, position risk) deliberates and votes on every strategy decision. Each agent has its own prompt contract (`ai_prompt_templates/`) and structured output schema (`ai_schemas.py`). |
| **Where must a human stay in control?** | AI output is **advisory by default and gated by design**: live orders are off unless explicitly enabled, an AI-confidence threshold gates action, and the operator can pause, override, or reconfigure at any time from the dashboard. |
| **How is non-deterministic behaviour quality-controlled?** | Structured Pydantic schemas validate every AI response; a fallback model and stale-feed detection ("Execution Quality: Watch — AI feed stale or fallback") fail closed; decision logs record the *why* behind every action for post-hoc review. |
| **Is behaviour observable?** | Full telemetry: order history, engine state events, P&L/fees/exposure, AI decision pages with per-agent rationale — designed as product surfaces, not hidden backend detail. |
| **Is it engineered, not just demoed?** | 416 automated tests across engine, persistence, AI schemas, dashboard contracts and repo hygiene; CI workflow; SQLite persistence with a JSON migration path; security posture documented in `SECURITY.md`. |

## The agentic architecture

```
Market data + macro news feed
        │
        ▼
┌─────────────────────────────┐
│  AI Sidecar (ai_sidecar.py) │   local LLM (Ollama), swappable model,
│  6-agent committee vote      │   fallback model, confidence threshold
└──────────────┬──────────────┘
               │  structured, schema-validated advisory decision
               ▼
┌─────────────────────────────┐
│  Trading Engine (engine.py) │   grid strategy, risk caps, fail-closed
│  AI-GATED execution          │   validation, paper/testnet by default
└──────────────┬──────────────┘
               │  state, orders, events
               ▼
┌─────────────────────────────┐
│  Operator Dashboard          │   live KPIs, regime radar, decision log,
│  human-in-the-loop controls  │   chat-with-the-agent, manual override
└─────────────────────────────┘
```

Design choices worth noting:

- **Separation of judgment and execution.** The AI sidecar never places orders. It produces a structured recommendation; the engine applies it only within hard-coded risk bounds (max exposure, daily loss caps, position caps). This is the "where AI belongs and where it does not" decision made concrete.
- **Committee over single-model.** Six adversarial perspectives (including an explicit bear case and an execution guard) reduce single-prompt failure modes and make every decision reviewable line by line.
- **Self-evolving via lessons.** The memory layer (`ai_memory.py`) feeds recent outcome "lessons" back into agent context — e.g. the committee learned that AI-aligned exits in low-volatility drops produced losses and now weighs that pattern.
- **Fail-closed everywhere.** Unsupported modes, stale AI feeds, and schema violations all degrade to the safe state, not the permissive one.

## What this is — and honestly, what it isn't

This is a **local AI assisted trading bot prototype**, single-operator, running against paper/testnet workflows. It is not a commercial product with customers (yet), and the current P&L reflects a live learning environment. What it evidences is the thing that transfers: taking an AI-agentic product from zero to a running, observable, controllable, tested system — the discovery, specification, guardrail design, evaluation and operational discipline that agentic product ownership requires.

## The running system

**Feature #1: Operating Dashboard** - Portfolio and current market tracking price with top news driving price and impact gauge

![1 Live_Operating_Dashboard](live_screenshots/1_Live_Operating_Dashboard.png)

**Feature #2: Portfolio vs Current Market Regime & Sentiment** — Live analysis to the current portfolio and a snapshot of the how portfolio is performing against market regime

![2 Portfolio_vs_Current_Market_Sentiment](live_screenshots/2_Portfolio_vs_Current_Market_Sentiment.png)

**Feature #3: News & Macro Intelligence** — Stay up to date with the upcoming macro economic drivers with impact gauge

![3 News and_Macro Intelligence](live_screenshots/3_New_and_Macro_Intelligence.png)

**Feature #4: AI Decisioning Committee** — 6 AI Agents pre-configured to analyze the market from different perspectives and vote on strategy decisions

![4 AI Committee](live_screenshots/4_AI_Committee.png)

**Feature #5: Human in the Loop** — Configure or Speak directly to the AI Decisioning Committee to understand or influence a decision

![5 AI Decision Agent](live_screenshots/5_AI_Decision_Agent.png)

**Feature #6: Events & Order History** — Keep track of order events and trading history

![6 Order History](live_screenshots/6_Order_History.png)

## Where to look

- `README.md` — operator setup, safety model, architecture
- `ai_prompt_templates/` — the six agent prompt contracts
- `ai_schemas.py` — structured output validation for non-deterministic responses
- `ai_sidecar.py` — committee orchestration, fallback and confidence gating
- `engine.py` — execution engine with hard risk bounds
- `tests/` — 416 tests incl. AI schema, contract and hygiene suites
- `OPERATIONS.md` / `SECURITY.md` — operating and security posture
