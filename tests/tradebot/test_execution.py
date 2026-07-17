from decimal import Decimal

from tradebot.application.execution import (
    ExecutionService,
    OrderIntent,
    OrderType,
    RejectReason,
)
from tradebot.domain.ledger import Side, Wallet
from tradebot.domain.market import MarketSnapshot
from tradebot.domain.money import base, quote


def snap(sid="s1", open_ms=1000, closed=True, close="60000", hi="60500", lo="59500"):
    return MarketSnapshot(
        snapshot_id=sid, source="test", symbol="BTCUSDT", interval="1m",
        open_time_ms=open_ms, close_time_ms=open_ms + 60000, is_closed=closed,
        open=Decimal("60000"), high=Decimal(hi), low=Decimal(lo),
        close=Decimal(close), volume=Decimal("10"),
        retrieved_at_ms=open_ms, source_time_ms=open_ms,
    )


def market_buy(wid="w1", qty="0.01", iid="i1"):
    return OrderIntent(iid, wid, "sv", Side.BUY, OrderType.MARKET, base(qty))


def test_market_buy_fills_with_slippage():
    svc = ExecutionService()
    w = Wallet("w1")
    [res] = svc.process_tick(snap(), [(w, market_buy())])
    assert res.accepted
    # slippage 0.05% above close 60000 -> 60030.00
    assert res.fill_price == Decimal("60030.00")


def test_a01_same_candle_cannot_fill_twice():
    """A01: two intents against the same candle -> second is DUPLICATE_CANDLE."""
    svc = ExecutionService()
    w = Wallet("w1")
    s = snap()
    r1 = svc.process_tick(s, [(w, market_buy(iid="i1"))])[0]
    r2 = svc.process_tick(s, [(w, market_buy(iid="i2"))])[0]
    assert r1.accepted is True
    assert r2.accepted is False
    assert r2.reason is RejectReason.DUPLICATE_CANDLE


def test_a01_new_candle_allows_new_fill():
    svc = ExecutionService()
    w = Wallet("w1")
    svc.process_tick(snap(sid="s1", open_ms=1000), [(w, market_buy(iid="i1"))])
    r = svc.process_tick(snap(sid="s2", open_ms=61000), [(w, market_buy(iid="i2"))])[0]
    assert r.accepted is True


def test_unclosed_candle_rejected():
    svc = ExecutionService()
    w = Wallet("w1")
    r = svc.process_tick(snap(closed=False), [(w, market_buy())])[0]
    assert r.reason is RejectReason.CANDLE_NOT_CLOSED


def test_min_notional_rejected():
    svc = ExecutionService()
    w = Wallet("w1")
    tiny = OrderIntent("i1", "w1", "sv", Side.BUY, OrderType.MARKET, base("0.00001"))
    r = svc.process_tick(snap(close="100"), [(w, tiny)])[0]
    assert r.reason is RejectReason.MIN_NOTIONAL


def test_limit_buy_not_marketable():
    svc = ExecutionService()
    w = Wallet("w1")
    # limit far below candle low -> not marketable
    intent = OrderIntent("i1", "w1", "sv", Side.BUY, OrderType.LIMIT, base("0.01"),
                         limit_price=quote("50000"))
    r = svc.process_tick(snap(lo="59500"), [(w, intent)])[0]
    assert r.reason is RejectReason.LIMIT_NOT_MARKETABLE


def test_shared_snapshot_iteration_order_independence():
    """All wallets see the identical snapshot; results are order-independent."""
    svc_a = ExecutionService()
    svc_b = ExecutionService()
    wallets_a = [Wallet(f"w{i}") for i in range(5)]
    wallets_b = [Wallet(f"w{i}") for i in range(5)]
    s = snap()
    intents_a = [(w, market_buy(wid=w.wallet_id, iid=f"i{i}")) for i, w in enumerate(wallets_a)]
    intents_b = list(reversed([(w, market_buy(wid=w.wallet_id, iid=f"i{i}"))
                               for i, w in enumerate(wallets_b)]))
    res_a = {r.wallet_id: r.fill_price for r in svc_a.process_tick(s, intents_a)}
    res_b = {r.wallet_id: r.fill_price for r in svc_b.process_tick(s, intents_b)}
    assert res_a == res_b  # same fills regardless of order
