"""Deterministic Decimal technical indicators for built-in strategies.

Pure functions over sequences of closed candles (oldest first). No floats in
signal math — everything stays in Decimal so replay is bit-reproducible.
"""

from __future__ import annotations

from decimal import Decimal

from ..domain.market import MarketSnapshot

ZERO = Decimal("0")


def closes(candles: tuple[MarketSnapshot, ...]) -> list[Decimal]:
    return [c.close for c in candles]


def sma(values: list[Decimal], period: int) -> Decimal | None:
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:], start=ZERO) / Decimal(period)


def ema_series(values: list[Decimal], period: int) -> list[Decimal]:
    """EMA seeded with SMA of the first `period` values."""

    if len(values) < period or period <= 0:
        return []
    k = Decimal(2) / Decimal(period + 1)
    seed = sum(values[:period], start=ZERO) / Decimal(period)
    out = [seed]
    for v in values[period:]:
        out.append(v * k + out[-1] * (Decimal(1) - k))
    return out


def ema(values: list[Decimal], period: int) -> Decimal | None:
    series = ema_series(values, period)
    return series[-1] if series else None


def stddev(values: list[Decimal], period: int) -> Decimal | None:
    if len(values) < period or period < 2:
        return None
    window = values[-period:]
    mean = sum(window, start=ZERO) / Decimal(period)
    var = sum(((v - mean) ** 2 for v in window), start=ZERO) / Decimal(period)
    return var.sqrt()


def zscore(values: list[Decimal], period: int) -> Decimal | None:
    mean = sma(values, period)
    sd = stddev(values, period)
    if mean is None or sd is None or sd == 0:
        return None
    return (values[-1] - mean) / sd


def true_range(candle: MarketSnapshot, prev_close: Decimal) -> Decimal:
    return max(
        candle.high - candle.low,
        abs(candle.high - prev_close),
        abs(candle.low - prev_close),
    )


def atr(candles: tuple[MarketSnapshot, ...], period: int) -> Decimal | None:
    if len(candles) < period + 1:
        return None
    trs = [
        true_range(candles[i], candles[i - 1].close)
        for i in range(len(candles) - period, len(candles))
    ]
    return sum(trs, start=ZERO) / Decimal(period)


def rsi(values: list[Decimal], period: int) -> Decimal | None:
    if len(values) < period + 1:
        return None
    gains = ZERO
    losses = ZERO
    for i in range(len(values) - period, len(values)):
        change = values[i] - values[i - 1]
        if change > 0:
            gains += change
        else:
            losses -= change
    if losses == 0:
        return Decimal(100)
    rs = gains / losses
    return Decimal(100) - Decimal(100) / (Decimal(1) + rs)


def stochastic_k(candles: tuple[MarketSnapshot, ...], period: int) -> Decimal | None:
    if len(candles) < period:
        return None
    window = candles[-period:]
    hi = max(c.high for c in window)
    lo = min(c.low for c in window)
    if hi == lo:
        return Decimal(50)
    return (candles[-1].close - lo) / (hi - lo) * Decimal(100)


def macd_histogram(values: list[Decimal], fast: int = 12, slow: int = 26,
                   signal: int = 9) -> list[Decimal]:
    """Histogram series (MACD - signal); empty if insufficient data."""

    if len(values) < slow + signal:
        return []
    fast_s = ema_series(values, fast)
    slow_s = ema_series(values, slow)
    n = min(len(fast_s), len(slow_s))
    macd_line = [fast_s[-n + i] - slow_s[-n + i] for i in range(n)]
    signal_s = ema_series(macd_line, signal)
    m = min(len(macd_line), len(signal_s))
    return [macd_line[-m + i] - signal_s[-m + i] for i in range(m)]


def donchian_high(candles: tuple[MarketSnapshot, ...], period: int,
                  *, exclude_last: bool = True) -> Decimal | None:
    pool = candles[:-1] if exclude_last else candles
    if len(pool) < period:
        return None
    return max(c.high for c in pool[-period:])


def donchian_low(candles: tuple[MarketSnapshot, ...], period: int,
                 *, exclude_last: bool = True) -> Decimal | None:
    pool = candles[:-1] if exclude_last else candles
    if len(pool) < period:
        return None
    return min(c.low for c in pool[-period:])


def rolling_vwap(candles: tuple[MarketSnapshot, ...], period: int) -> Decimal | None:
    if len(candles) < period:
        return None
    window = candles[-period:]
    vol = sum((c.volume for c in window), start=ZERO)
    if vol == 0:
        return None
    typical = [(c.high + c.low + c.close) / Decimal(3) for c in window]
    pv = sum((t * c.volume for t, c in zip(typical, window)), start=ZERO)
    return pv / vol


def obv_series(candles: tuple[MarketSnapshot, ...]) -> list[Decimal]:
    out = [ZERO]
    for i in range(1, len(candles)):
        if candles[i].close > candles[i - 1].close:
            out.append(out[-1] + candles[i].volume)
        elif candles[i].close < candles[i - 1].close:
            out.append(out[-1] - candles[i].volume)
        else:
            out.append(out[-1])
    return out


def relative_volume(candles: tuple[MarketSnapshot, ...], period: int) -> Decimal | None:
    if len(candles) < period + 1:
        return None
    avg = sum((c.volume for c in candles[-period - 1:-1]), start=ZERO) / Decimal(period)
    if avg == 0:
        return None
    return candles[-1].volume / avg


def efficiency_ratio(values: list[Decimal], period: int) -> Decimal | None:
    """Kaufman efficiency ratio: |net change| / sum |candle changes|."""

    if len(values) < period + 1:
        return None
    window = values[-period - 1:]
    net = abs(window[-1] - window[0])
    path = sum((abs(window[i] - window[i - 1]) for i in range(1, len(window))), start=ZERO)
    if path == 0:
        return None
    return net / path
