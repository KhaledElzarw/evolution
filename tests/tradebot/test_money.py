from decimal import Decimal

import pytest

from tradebot.domain import money


def test_quote_and_base_quantization():
    assert money.quote("10000") == Decimal("10000.00")
    assert money.quote("1.005") == Decimal("1.01")  # HALF_UP
    assert money.base("0.123456789") == Decimal("0.12345679")


def test_float_rejected_in_money_math():
    with pytest.raises(TypeError):
        money.quote(1.5)  # type: ignore[arg-type]


def test_notional_and_fee():
    assert money.notional(Decimal("0.5"), Decimal("60000")) == Decimal("30000.00")
    assert money.apply_fee(Decimal("30000.00"), Decimal("0.001")) == Decimal("30.00")


def test_exchange_filters():
    f = money.DEFAULT_BTCUSDT_FILTERS
    assert f.round_qty_down(Decimal("0.000123456")) == Decimal("0.00012")
    assert f.passes_min_notional(Decimal("0.001"), Decimal("60000")) is True
    assert f.passes_min_notional(Decimal("0.00001"), Decimal("100")) is False
    assert f.passes_min_qty(Decimal("0.000001")) is False
    assert f.round_price_down(Decimal("60000.019")) == Decimal("60000.01")
