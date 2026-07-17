"""Unit-of-work, repositories, and idempotent schema migration.

Promotion and multi-row lifecycle operations run inside a single explicit
transaction (all-or-nothing). Migration is idempotent — running it twice on the
same database is a no-op and never drops data.
"""

from __future__ import annotations

import datetime as dt
from types import TracebackType

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ...domain.ledger import LedgerTransaction, Wallet
from .models import (
    SCHEMA_VERSION,
    Base,
    LedgerPostingRow,
    LedgerTransactionRow,
    WalletStrategyAssignment,
)
from .models import Wallet as WalletRow


def make_engine(url: str = "sqlite:///:memory:") -> Engine:
    return create_engine(url, future=True)


def create_schema(engine: Engine) -> str:
    """Create all tables if absent. Idempotent; returns the schema version.

    ``checkfirst=True`` (SQLAlchemy default) means existing tables are left
    intact, so re-running never destroys data.
    """

    Base.metadata.create_all(engine, checkfirst=True)
    return SCHEMA_VERSION


def schema_tables(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


class UnitOfWork:
    """Context manager wrapping one atomic Session transaction."""

    def __init__(self, engine: Engine) -> None:
        self._session_factory = sessionmaker(bind=engine, future=True)
        self.session: Session | None = None

    def __enter__(self) -> "UnitOfWork":
        self.session = self._session_factory()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self.session is not None
        try:
            if exc_type is None:
                self.session.commit()
            else:
                self.session.rollback()
        finally:
            self.session.close()

    # -- repository operations ---------------------------------------------

    def add_wallet(self, wallet: Wallet, *, kind: str, stable_name: str,
                   display_name: str, created_at: dt.datetime) -> None:
        assert self.session is not None
        self.session.add(
            WalletRow(
                wallet_id=wallet.wallet_id,
                wallet_kind=kind,
                stable_name=stable_name,
                display_name=display_name,
                status="active",
                initial_quote_balance=wallet.quote_cash,
                quote_cash=wallet.quote_cash,
                base_qty=wallet.base_qty,
                avg_cost=wallet.avg_cost,
                realized_pnl=wallet.realized_pnl,
                total_fees=wallet.total_fees,
                created_at=created_at,
            )
        )

    def record_transaction(self, txn: LedgerTransaction, wallet: Wallet,
                           created_at: dt.datetime) -> None:
        """Persist a fill transaction + postings and sync wallet balances.

        Raises if the wallet row is missing (cross-wallet safety: we only touch
        the transaction's own wallet).
        """

        assert self.session is not None
        row = self.session.get(WalletRow, txn.wallet_id)
        if row is None:
            raise KeyError(f"unknown wallet {txn.wallet_id}")
        txn_row = LedgerTransactionRow(
            transaction_id=txn.transaction_id,
            wallet_id=txn.wallet_id,
            order_id=txn.order_id,
            fill_id=txn.fill_id,
            idempotency_key=txn.idempotency_key,
            strategy_version_id=txn.strategy_version_id,
            market_snapshot_id=txn.market_snapshot_id,
            side=txn.side.value,
            qty=txn.qty,
            price=txn.price,
            fee=txn.fee,
            created_at=created_at,
        )
        txn_row.postings = [
            LedgerPostingRow(account=p.account, currency=p.currency, amount=p.amount)
            for p in txn.postings
        ]
        self.session.add(txn_row)
        # Keep the wallet projection in sync with the in-memory aggregate.
        row.quote_cash = wallet.quote_cash
        row.base_qty = wallet.base_qty
        row.avg_cost = wallet.avg_cost
        row.realized_pnl = wallet.realized_pnl
        row.total_fees = wallet.total_fees

    def active_assignment_count(self, wallet_id: str) -> int:
        assert self.session is not None
        return (
            self.session.query(WalletStrategyAssignment)
            .filter_by(wallet_id=wallet_id, deactivated_at=None)
            .count()
        )
