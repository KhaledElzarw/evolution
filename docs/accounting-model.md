# Accounting Model

Implemented in [money.py](../tradebot/domain/money.py) and
[ledger.py](../tradebot/domain/ledger.py).

## Fixed-point money (guaranteed invariant)

All money, price, quantity, fee, and P&L values are `decimal.Decimal`. **Binary
`float` never enters ledger math** — `money._to_decimal` raises `TypeError` if a
float is passed, forcing callers to stringify at the boundary. The decimal
context precision is 40 so intermediate products (`price * qty`) keep
significance before quantizing.

| Currency | Scale | Rounding |
|----------|-------|----------|
| USDT (quote) | 0.01 (cents) | HALF_UP |
| BTC (base) | 0.00000001 (satoshi) | HALF_UP |

## Wallet isolation (guaranteed invariant)

Each `Wallet` is an isolated aggregate that mutates only its own state.
**Cross-wallet postings are structurally impossible** — there is no shared
global balance (closes A03/A04). Proven by
`test_replay_25_wallets.py::test_wallet_isolation_no_shared_state`.

## Lot policy: weighted-average cost

BTCUSDT paper wallets use weighted-average cost basis. A BUY updates
`avg_cost = (old_qty*old_cost + fill_qty*fill_price + acquisition_fee) /
new_qty`. A SELL realizes `(sale_price - avg_cost) * qty - disposal_fee` and
leaves `avg_cost` unchanged.

## Fees counted exactly once (A02 — guaranteed invariant)

- **Acquisition fees** raise the cost basis exactly once (folded into
  `avg_cost` at BUY).
- **Disposal fees** reduce proceeds exactly once (subtracted at SELL).
- Net realized P&L is derived from event postings, **never** by subtracting a
  fee total from a value that already contains fee-inclusive cost basis.

Regression:
`test_ledger.py::test_a02_fee_not_double_counted_over_roundtrip` — a flat
buy→sell at the same price yields realized P&L of exactly
`-(buy_fee + sell_fee)`.

## Balanced journal (guaranteed invariants)

Every fill produces one atomic transaction whose postings sum to zero. Enforced
in code and by property tests:

- Sum of postings == 0.
- Quote balance never negative.
- Base balance never negative.
- SELL quantity never exceeds owned unreserved BTC.
- Reserved never exceeds available.

Preserved figures (all explicit, not re-derived): gross realized P&L, net
realized P&L, unrealized P&L, total fees, total slippage cost, equity.

## Equity and liquidation

- **Mark-to-market equity** = `quote_cash + base_qty * mark_price`.
- **Liquidation-adjusted equity** ([liquidation.py](../tradebot/application/liquidation.py))
  simulates selling remaining BTC at the common cutoff snapshot, applying
  configured slippage and the disposal fee **once**. Both pre-liquidation and
  liquidation-adjusted values are stored.

## Canonical weekly profit

`weekly_net_profit_usdt = liquidation_adjusted_cutoff_equity −
evaluation_start_equity` (fixed-point). Includes realized P&L, unrealized P&L
at the common cutoff, acquisition and disposal fees, and slippage — each once.
See [evolution-policy.md](evolution-policy.md).
