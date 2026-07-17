from decimal import Decimal

import pytest

from tradebot.domain.ledger import LedgerError, Side, Wallet
from tradebot.domain.money import base, quote


def _buy(w: Wallet, qty, px, fee_rate="0.001", key="k"):
    return w.apply_fill(
        transaction_id="t", order_id="o", fill_id="f", idempotency_key=key,
        strategy_version_id="sv", market_snapshot_id="ms", side=Side.BUY,
        qty=base(qty), fill_price=quote(px), fee_rate=Decimal(fee_rate),
    )


def _sell(w: Wallet, qty, px, fee_rate="0.001", key="k2"):
    return w.apply_fill(
        transaction_id="t", order_id="o", fill_id="f", idempotency_key=key,
        strategy_version_id="sv", market_snapshot_id="ms", side=Side.SELL,
        qty=base(qty), fill_price=quote(px), fee_rate=Decimal(fee_rate),
    )


def test_buy_postings_balance_and_capitalize_fee_once():
    w = Wallet("w1")
    txn = _buy(w, "0.1", "60000")  # gross 6000, fee 6.00 -> debit 6006.00
    assert txn.quote_balance_check() == Decimal("0")
    assert w.quote_cash == Decimal("3994.00")
    assert w.base_qty == Decimal("0.10000000")
    # A02: avg cost is fee-inclusive, counted exactly once.
    assert w.avg_cost == Decimal("60060.00")
    assert w.total_fees == Decimal("6.00")


def test_a02_fee_not_double_counted_over_roundtrip():
    """Buy then sell flat: net P&L must equal exactly -(buy_fee + sell_fee)."""
    w = Wallet("w1")
    _buy(w, "0.1", "60000", key="b")          # fee 6.00 folded into cost
    _sell(w, "0.1", "60000", key="s")         # fee 6.00 off proceeds
    # No price move: realized loss should be exactly the two fees, once each.
    assert w.realized_pnl == Decimal("-12.00")
    assert w.total_fees == Decimal("12.00")
    # Cash back to 10000 - 12 fees.
    assert w.quote_cash == Decimal("9988.00")
    assert w.base_qty == Decimal("0")


def test_sell_postings_balance_with_profit():
    w = Wallet("w1")
    _buy(w, "0.1", "60000", key="b")
    txn = _sell(w, "0.1", "66000", key="s")  # +10% move
    assert txn.quote_balance_check() == Decimal("0")
    # gross 6600, fee 6.60, net 6593.40; cost 6006 -> realized 587.40
    assert w.realized_pnl == Decimal("587.40")


def test_no_overspend():
    w = Wallet("w1", quote_cash=quote("100"))
    with pytest.raises(LedgerError, match="overspend"):
        _buy(w, "1", "60000")


def test_no_oversell_no_short():
    w = Wallet("w1")
    with pytest.raises(LedgerError, match="short"):
        _sell(w, "1", "60000")


def test_duplicate_idempotency_key_rejected():
    w = Wallet("w1")
    _buy(w, "0.01", "60000", key="dup")
    with pytest.raises(LedgerError, match="duplicate"):
        _buy(w, "0.01", "60000", key="dup")


def test_equity_and_unrealized():
    w = Wallet("w1")
    _buy(w, "0.1", "60000", key="b")
    assert w.equity(quote("60000")) == Decimal("9994.00")  # 3994 cash + 6000 mark
    assert w.unrealized_pnl(quote("66000")) == Decimal("594.00")


def test_negative_qty_and_price_rejected():
    w = Wallet("w1")
    with pytest.raises(LedgerError):
        _buy(w, "0", "60000")
    with pytest.raises(LedgerError):
        _buy(w, "0.1", "0")
