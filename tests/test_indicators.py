from decimal import Decimal

from memecoin_trader.analysis.indicators import (
    drawdown_from_peak_pct,
    momentum_pct,
    relative_strength_index,
    simple_moving_average,
)


def dec_list(values):
    return [Decimal(str(v)) for v in values]


def test_sma_basic():
    prices = dec_list([1, 2, 3, 4, 5])
    assert simple_moving_average(prices, 3) == Decimal("4")


def test_sma_insufficient_data_returns_none():
    prices = dec_list([1, 2])
    assert simple_moving_average(prices, 5) is None


def test_momentum_pct_positive():
    prices = dec_list([10, 10, 10, 20])
    assert momentum_pct(prices, 3) == 100.0


def test_momentum_pct_insufficient_data():
    prices = dec_list([10, 20])
    assert momentum_pct(prices, 5) is None


def test_drawdown_from_peak():
    prices = dec_list([1, 2, 10, 5])
    assert drawdown_from_peak_pct(prices) == 50.0


def test_drawdown_from_peak_empty():
    assert drawdown_from_peak_pct([]) == 0.0


def test_rsi_all_gains_is_100():
    prices = dec_list([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15])
    assert relative_strength_index(prices, period=14) == 100.0


def test_rsi_all_losses_is_0():
    prices = dec_list(list(range(15, 0, -1)))
    assert relative_strength_index(prices, period=14) == 0.0


def test_rsi_insufficient_data():
    assert relative_strength_index(dec_list([1, 2, 3]), period=14) is None


def test_rsi_bounded_between_0_and_100():
    prices = dec_list([1, 2, 1.5, 3, 2.5, 4, 3.5, 5, 4.5, 6, 5.5, 7, 6.5, 8, 7.5])
    rsi = relative_strength_index(prices, period=14)
    assert rsi is not None
    assert 0.0 <= rsi <= 100.0
