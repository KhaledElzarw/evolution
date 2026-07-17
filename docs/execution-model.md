# Execution Model

Implemented in [execution.py](../tradebot/application/execution.py) over
immutable [market.py](../tradebot/domain/market.py) snapshots.

## Shared market clock (guaranteed invariant)

One market event stream is fetched once and the **identical immutable
`MarketSnapshot`** is fanned to every eligible wallet. A snapshot carries
snapshot id, source, symbol, interval, open/close time, `is_closed`, OHLCV,
retrieval + source timestamps, and a `content_hash`. No wallet can see another
wallet's decision or fill before producing its own intent for the same
snapshot.

Fairness is proven by
`test_replay_25_wallets.py::test_active_and_shadow_ledgers_evolve_identically_for_same_strategy`
(same strategy, same snapshots, same start → identical ledgers).

## Two-phase tick (guaranteed invariant)

1. **Collect** all intents against the same snapshot.
2. **Validate and execute** all intents against that same snapshot under
   deterministic rules.

Execution does not depend on wallet iteration order
(`test_execution.py` iteration-order independence), and the trading tick never
calls the LLM.

## Same-candle fill prevention (A01 — guaranteed invariant)

A per-wallet **candle watermark** records the last processed `open_time_ms`. A
closed-candle strategy processes each candle once; a second intent against the
same open candle is rejected with `RejectReason.DUPLICATE_CANDLE`. A candle
that is not closed is rejected with `CANDLE_NOT_CLOSED`. This holds regardless
of intent id, side, or wallet — closes A01. Regression:
`test_execution.py::test_a01_same_candle_cannot_fill_twice`.

## Exchange filters (A11 — guaranteed invariant)

Before any fill is simulated, Binance public filters are enforced
(`DEFAULT_BTCUSDT_FILTERS`): PRICE_FILTER (tick), LOT_SIZE (step + min/max
qty), MIN_NOTIONAL, and base/quote precision. Reject reasons include
`LOT_SIZE`, `MIN_NOTIONAL`, `LIMIT_NOT_MARKETABLE`, `NEGATIVE_QTY`,
`LEDGER_REJECTED`. Rejected intents are **not** fills and do not count toward
`fill_count`.

## Simulation assumptions (configurable, not guarantees)

The `ExecutionModel` is versioned and stored with every evaluation. Defaults:

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `taker_fee_rate` | 0.0010 (10 bps) | disposal/acquisition fee |
| `slippage_rate` | 0.0005 (5 bps) | simulated adverse fill |

These assumptions are **identical across all competing wallets** in a tick, so
no wallet gets a structural execution advantage. Market vs limit fills, gaps,
partial fills, and volume participation are simulated deterministically.

## Conservative OHLC path (simulation assumption)

For OHLC-only replay where the intra-bar path is unknown, a documented
conservative path model is used: it prevents a BUY and a SELL on the same bar
from both filling at mutually advantageous prices. The path policy is
configurable and reproducible; pessimistic alternatives belong in stress tests.

## Determinism

Replays are bit-reproducible given the same seed
(`test_replay_25_wallets.py::test_replay_is_bit_reproducible`). No signal or
execution math uses binary float; the market-data *generator* in tests uses
floats only to build candle inputs, never in the ledger path.
