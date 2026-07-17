from decimal import Decimal

from tradebot.application.execution import ExecutionModel
from tradebot.application.liquidation import (
    liquidation_adjusted_equity,
    pre_liquidation_equity,
)
from tradebot.domain.ledger import Side, Wallet
from tradebot.domain.money import base, quote


def test_liquidation_charges_disposal_cost_once():
    w = Wallet("w1")
    w.apply_fill(transaction_id="t", order_id="o", fill_id="f",
                 idempotency_key="k", strategy_version_id="sv",
                 market_snapshot_id="ms", side=Side.BUY, qty=base("0.1"),
                 fill_price=quote("60000"), fee_rate=Decimal("0.001"))
    mark = quote("60000")
    model = ExecutionModel()
    pre = pre_liquidation_equity(w, mark)
    adj = liquidation_adjusted_equity(w, mark, model)
    # Pre-liquidation marks the position at 60000; adjusted subtracts slippage+fee.
    assert pre > adj
    # gross 6000; slipped 5997.00; fee 6.00; proceeds 5991.00; +cash 3994 = 9985.00
    assert adj == Decimal("9985.00")


def test_liquidation_of_flat_wallet_is_cash():
    w = Wallet("w1", quote_cash=quote("10000"))
    assert liquidation_adjusted_equity(w, quote("60000"), ExecutionModel()) == Decimal("10000.00")
