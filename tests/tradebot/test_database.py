import datetime as dt
from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from tradebot.domain.ledger import Side, Wallet
from tradebot.domain.money import base, quote
from tradebot.infrastructure.database.models import LedgerTransactionRow
from tradebot.infrastructure.database.unit_of_work import (
    UnitOfWork,
    create_schema,
    make_engine,
    schema_tables,
)

NOW = dt.datetime(2026, 7, 17, tzinfo=dt.timezone.utc).replace(tzinfo=None)


@pytest.fixture()
def engine(tmp_path):
    eng = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    create_schema(eng)
    return eng


def _seed_wallet(engine, wid="w1", cash="10000.00"):
    w = Wallet(wid, quote_cash=quote(cash))
    with UnitOfWork(engine) as uow:
        uow.add_wallet(w, kind="active", stable_name=f"S_{wid}",
                       display_name=f"S_{wid}_0", created_at=NOW)
    return w


def test_migration_is_idempotent(engine):
    before = schema_tables(engine)
    v = create_schema(engine)  # run again
    assert v == "v2.0.0"
    assert "wallets" in before and "ledger_transactions" in before
    assert schema_tables(engine) == before


def test_atomic_fill_persistence(engine):
    w = _seed_wallet(engine)
    txn = w.apply_fill(
        transaction_id="t1", order_id="o1", fill_id="f1", idempotency_key="idem-1",
        strategy_version_id="sv", market_snapshot_id="ms", side=Side.BUY,
        qty=base("0.1"), fill_price=quote("60000"), fee_rate=Decimal("0.001"),
    )
    with UnitOfWork(engine) as uow:
        uow.record_transaction(txn, w, created_at=NOW)
    with UnitOfWork(engine) as uow:
        row = uow.session.get(LedgerTransactionRow, "t1")
        assert row is not None
        assert len(row.postings) == 3  # buy: quote_cash, base_asset USDT, base BTC
        # quote postings net to zero in persisted form
        qsum = sum(p.amount for p in row.postings if p.currency == "USDT")
        assert qsum == Decimal("0")


def test_duplicate_idempotency_key_violates_unique(engine):
    w = _seed_wallet(engine)
    txn = w.apply_fill(
        transaction_id="t1", order_id="o1", fill_id="f1", idempotency_key="dup",
        strategy_version_id="sv", market_snapshot_id="ms", side=Side.BUY,
        qty=base("0.1"), fill_price=quote("60000"), fee_rate=Decimal("0.001"),
    )
    with UnitOfWork(engine) as uow:
        uow.record_transaction(txn, w, created_at=NOW)
    # A second row with the same idempotency key must fail the unique constraint.
    with pytest.raises(IntegrityError):
        with UnitOfWork(engine) as uow:
            row = LedgerTransactionRow(
                transaction_id="t2", wallet_id="w1", order_id="o", fill_id="f",
                idempotency_key="dup", strategy_version_id="sv",
                market_snapshot_id="ms", side="BUY", qty=Decimal("0.1"),
                price=Decimal("60000"), fee=Decimal("6"), created_at=NOW,
            )
            uow.session.add(row)


def test_check_constraint_blocks_negative_cash(engine):
    from tradebot.infrastructure.database.models import Wallet as WRow
    with pytest.raises(IntegrityError):
        with UnitOfWork(engine) as uow:
            uow.session.add(
                WRow(
                    wallet_id="bad", wallet_kind="active", stable_name="bad",
                    display_name="bad", status="active",
                    initial_quote_balance=Decimal("0"), quote_cash=Decimal("-1"),
                    base_qty=Decimal("0"), avg_cost=Decimal("0"),
                    realized_pnl=Decimal("0"), total_fees=Decimal("0"),
                    created_at=NOW,
                )
            )


def test_rollback_on_error_leaves_no_partial_state(engine):
    _seed_wallet(engine, wid="w1")
    with pytest.raises(RuntimeError):
        with UnitOfWork(engine) as uow:
            w2 = Wallet("w2")
            uow.add_wallet(w2, kind="active", stable_name="S_w2",
                           display_name="S_w2_0", created_at=NOW)
            raise RuntimeError("boom before commit")
    # w2 must not exist because the transaction rolled back.
    from tradebot.infrastructure.database.models import Wallet as WRow
    with UnitOfWork(engine) as uow:
        assert uow.session.get(WRow, "w2") is None
        assert uow.session.get(WRow, "w1") is not None
