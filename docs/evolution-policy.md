# Evolution Policy (versioned)

Ranking formula version: `profit-only-v1`.

## Canonical weekly profit

`weekly_net_profit_usdt = liquidation_adjusted_cutoff_equity − evaluation_start_equity`

Fixed-point Decimal, quantized to cents. Includes — each exactly once —
realized P&L, unrealized P&L marked at the common cutoff snapshot, acquisition
fees, disposal fees, and simulated slippage. The ledger enforces fee-once on
realized trades; `liquidation_adjusted_equity` adds the disposal cost
(slippage + taker fee) of the *remaining* position, using the same cutoff
snapshot and assumptions for every active and shadow wallet.

## Ranking

Profitability is the **only** ranking value (product rule 8). No Sharpe,
drawdown, volatility, win rate, trade count, consistency, or committee votes.
Ties break by `wallet_id` — a stable, value-independent tiebreak, not a ranking
factor. Technical invalidity is an execution-eligibility failure handled
outside ranking, never a score penalty.

## Elimination

- **Loss:** every active strategy with `weekly_net_profit_usdt < 0`. Banned.
- **No-trade:** every active or shadow strategy with `fill_count == 0`. Banned.
  Dark Horse is exempt. Rejected intents and canceled orders are not fills.
- Both stored: `fill_count` (drives elimination) and `completed_round_trip_count`.

## Replacement count

- If ≥1 active strategy is eliminated by loss/no-trade → `replacement_count =
  eliminated active count`.
- If all 12 traded and none lost → retire the **bottom six** by profit
  (retirement is *not* a ban).

## Allocation

- `novel_count = ceil(replacement_count / 2)`
- `mutation_count = floor(replacement_count / 2)`
- Mutation parents = top surviving performers, up to 3. An eliminated strategy
  may never be a parent. **No surviving parent → mutation slots convert to
  novel.**

## Bans

Losing and no-trade code hashes (and structural fingerprints) are permanently
banned; they may never be activated or reused. A banned candidate offered to
promotion is quarantined and skipped.

## Atomic promotion

Promotion is all-or-nothing. Replacement wallets are created at exactly
10,000.00 USDT / 0 BTC. A technically invalid or worker-failing candidate is
quarantined and the next valid candidate is used (roll-forward) — the
eliminated strategy is never resurrected. On candidate shortage the batch
raises and rolls back, leaving the active roster unchanged. Post-commit
invariants: exactly 12 active, Dark Horse present and unreset, unique wallet
ids, non-negative balances.

Poor shadow performance does **not** block promotion: a technically valid
candidate may be promoted despite negative profitability (product rule 14).
Technically invalid code may **never** be promoted (product rule 15/22).
