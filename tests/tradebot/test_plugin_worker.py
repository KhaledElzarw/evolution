import json
from decimal import Decimal
from pathlib import Path

from tradebot.domain.ledger import Wallet
from tradebot.domain.market import MarketSnapshot
from tradebot.infrastructure.adapters.plugins.registry import QuarantineRegistry
from tradebot.infrastructure.adapters.plugins.worker import run_strategy_in_worker

SNAP = MarketSnapshot(
    snapshot_id="s1", source="test", symbol="BTCUSDT", interval="1m",
    open_time_ms=1000, close_time_ms=61000, is_closed=True,
    open=Decimal("60000"), high=Decimal("60500"), low=Decimal("59500"),
    close=Decimal("60000"), volume=Decimal("10"),
    retrieved_at_ms=1000, source_time_ms=1000,
)

BUY_STRATEGY = '''
from decimal import Decimal
from tradebot.domain.ledger import Side
from tradebot.domain.strategies import IntentSpec, StrategyDecision


class S:
    def initialize(self):
        return {"ticks": 0}

    def on_market_snapshot(self, context, state):
        ticks = state.get("ticks", 0) + 1
        intent = IntentSpec(side=Side.BUY, order_type="MARKET",
                            quantity=Decimal("0.01"), reason_code="test")
        return StrategyDecision(intents=(intent,), state={"ticks": ticks})


def create_strategy():
    return S()
'''


def make_bundle(tmp_path: Path, src: str) -> Path:
    bundle = tmp_path / "bundle"
    bundle.mkdir(parents=True)
    (bundle / "manifest.json").write_text(json.dumps({"schema_version": "manifest-v1"}),
                                          encoding="utf-8")
    (bundle / "strategy.py").write_text(src, encoding="utf-8")
    return bundle


def test_worker_runs_valid_strategy(tmp_path):
    bundle = make_bundle(tmp_path, BUY_STRATEGY)
    result = run_strategy_in_worker(bundle, SNAP, Wallet("w1"))
    assert result.ok, result.error
    assert len(result.intents) == 1
    assert result.intents[0]["side"] == "BUY"
    assert result.intents[0]["quantity"] == "0.01"
    assert result.state == {"ticks": 1}


def test_worker_state_round_trip(tmp_path):
    bundle = make_bundle(tmp_path, BUY_STRATEGY)
    r1 = run_strategy_in_worker(bundle, SNAP, Wallet("w1"))
    r2 = run_strategy_in_worker(bundle, SNAP, Wallet("w1"), state=r1.state)
    assert r2.state == {"ticks": 2}


def test_worker_timeout_kills(tmp_path):
    bundle = make_bundle(tmp_path, '''
def create_strategy():
    while True:
        pass
''')
    result = run_strategy_in_worker(bundle, SNAP, Wallet("w1"), timeout_seconds=3.0)
    assert not result.ok
    assert result.error_category == "Timeout"


def test_worker_reports_crash_as_structured_error(tmp_path):
    bundle = make_bundle(tmp_path, '''
def create_strategy():
    raise RuntimeError("boom")
''')
    result = run_strategy_in_worker(bundle, SNAP, Wallet("w1"))
    assert not result.ok
    assert result.error_category == "RuntimeError"
    assert "boom" in (result.error or "")


def test_worker_env_is_sanitized(tmp_path, monkeypatch):
    """Secrets in the parent environment must not reach the strategy worker."""
    monkeypatch.setenv("BINANCE_API_SECRET", "supersecret")
    bundle = make_bundle(tmp_path, '''
from tradebot.domain.strategies import StrategyDecision


class S:
    def initialize(self):
        import os  # noqa -- would be blocked by AST validator; probing here
        return {"leak": os.environ.get("BINANCE_API_SECRET", "ABSENT")}

    def on_market_snapshot(self, context, state):
        return StrategyDecision(state=state)


def create_strategy():
    return S()
''')
    result = run_strategy_in_worker(bundle, SNAP, Wallet("w1"))
    assert result.ok, result.error
    assert result.state == {"leak": "ABSENT"}


def test_quarantine_after_strike_limit():
    reg = QuarantineRegistry(strike_limit=3)
    assert reg.record_failure("sv1", "Timeout") is False
    assert reg.record_failure("sv1", "Timeout") is False
    assert reg.record_failure("sv1", "WorkerCrash") is True
    assert reg.is_quarantined("sv1")
    assert reg.quarantine_reason("sv1") == "WorkerCrash"


def test_quarantine_success_resets_strikes():
    reg = QuarantineRegistry(strike_limit=2)
    reg.record_failure("sv1", "Timeout")
    reg.record_success("sv1")
    assert reg.record_failure("sv1", "Timeout") is False  # counter was reset
    assert not reg.is_quarantined("sv1")


def test_quarantine_is_permanent_and_ignores_noncritical():
    reg = QuarantineRegistry(strike_limit=1)
    assert reg.record_failure("sv1", "SomeBenignCategory") is False
    reg.record_failure("sv1", "MalformedOutput")
    reg.record_success("sv1")  # must NOT un-quarantine
    assert reg.is_quarantined("sv1")
