"""Technical indicators computed on price history we've collected ourselves.

DexScreener's free API doesn't hand out full OHLCV candle history, so
"analyzing the graph" here means: we poll real prices on an interval and
build our own time series in price_snapshots, then run standard indicators
on it. With enough history this is exactly as accurate as candle-based
analysis, just built from our own polling cadence instead of pre-baked bars.
"""
from __future__ import annotations

from decimal import Decimal


def simple_moving_average(prices: list[Decimal], window: int) -> Decimal | None:
    if len(prices) < window or window <= 0:
        return None
    subset = prices[-window:]
    return sum(subset, Decimal(0)) / Decimal(window)


def momentum_pct(prices: list[Decimal], window: int) -> float | None:
    """% change comparing the latest price to the price `window` samples ago."""
    if len(prices) < window + 1 or window <= 0:
        return None
    old = prices[-(window + 1)]
    new = prices[-1]
    if old == 0:
        return None
    return float((new - old) / old * 100)


def drawdown_from_peak_pct(prices: list[Decimal]) -> float:
    """How far below its own running peak the latest price sits, as a positive %."""
    if not prices:
        return 0.0
    peak = max(prices)
    if peak == 0:
        return 0.0
    latest = prices[-1]
    return float((peak - latest) / peak * 100)


def relative_strength_index(prices: list[Decimal], period: int = 14) -> float | None:
    """Classic Wilder RSI, 0-100. None until we have `period` + 1 samples."""
    if len(prices) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(-period, 0):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gains.append(change)
        else:
            losses.append(-change)

    avg_gain = (sum(gains, Decimal(0)) / Decimal(period)) if gains else Decimal(0)
    avg_loss = (sum(losses, Decimal(0)) / Decimal(period)) if losses else Decimal(0)

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return float(rsi)
