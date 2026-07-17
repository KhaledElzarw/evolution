"""Common-cutoff mark-to-market and simulated liquidation.

Every active and shadow wallet is marked with the SAME cutoff snapshot and the
SAME liquidation assumptions (product rule 9). Liquidation-adjusted equity
simulates selling the remaining BTC at the cutoff mark, applying configured
slippage and the disposal fee — so a wallet that never closed its position is
charged the realistic cost of doing so, exactly once.
"""

from __future__ import annotations

from decimal import Decimal

from ..domain.ledger import Wallet
from ..domain.money import notional, quote
from .execution import ExecutionModel


def pre_liquidation_equity(wallet: Wallet, mark_price: Decimal) -> Decimal:
    """Plain mark-to-market: quote cash + base_qty * mark."""

    return wallet.equity(mark_price)


def liquidation_adjusted_equity(
    wallet: Wallet, mark_price: Decimal, model: ExecutionModel
) -> Decimal:
    """Equity assuming the remaining BTC is sold at the cutoff snapshot.

    The sale realizes ``base_qty * mark * (1 - slippage)`` minus the disposal
    fee on that gross. Cash already banked is untouched.
    """

    if wallet.base_qty <= 0:
        return quote(wallet.quote_cash)
    gross = notional(wallet.base_qty, mark_price)
    slipped = quote(gross * (Decimal("1") - model.slippage_rate))
    fee = quote(slipped * model.taker_fee_rate)
    proceeds = quote(slipped - fee)
    return quote(wallet.quote_cash + proceeds)
