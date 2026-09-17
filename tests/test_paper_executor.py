from decimal import Decimal

from memecoin_trader.config import PaperExecutionConfig
from memecoin_trader.execution.paper_executor import PaperExecutor
from tests.conftest import make_pair


def make_executor(base_slippage_bps=50, slippage_impact_factor=2.0, fee_bps=100):
    return PaperExecutor(
        PaperExecutionConfig(
            base_slippage_bps=base_slippage_bps,
            slippage_impact_factor=slippage_impact_factor,
            fee_bps=fee_bps,
        )
    )


def test_buy_applies_positive_slippage_and_fee():
    executor = make_executor()
    market = make_pair(price_usd="1.0", liquidity_usd="100000")
    fill = executor.buy("TOKEN1", Decimal("10"), market)

    assert fill.price_usd > market.price_usd  # buys fill worse than quoted price
    assert fill.fee_usd == Decimal("10") * Decimal("100") / Decimal("10000")
    assert fill.amount_usd == Decimal("10")
    # quantity * fill_price should roughly equal amount minus fee
    assert abs(fill.quantity * fill.price_usd - (fill.amount_usd - fill.fee_usd)) < Decimal("0.0000001")


def test_sell_applies_negative_slippage_and_fee():
    executor = make_executor()
    market = make_pair(price_usd="1.0", liquidity_usd="100000")
    fill = executor.sell("TOKEN1", Decimal("10"), market)

    assert fill.price_usd < market.price_usd  # sells fill worse than quoted price
    assert fill.quantity == Decimal("10")
    assert fill.amount_usd == Decimal("10") * fill.price_usd


def test_slippage_scales_with_trade_size_relative_to_liquidity():
    executor = make_executor()
    thin_market = make_pair(price_usd="1.0", liquidity_usd="1000")
    deep_market = make_pair(price_usd="1.0", liquidity_usd="1000000")

    thin_fill = executor.buy("TOKEN1", Decimal("100"), thin_market)
    deep_fill = executor.buy("TOKEN1", Decimal("100"), deep_market)

    assert thin_fill.price_usd > deep_fill.price_usd


def test_zero_liquidity_does_not_crash_and_is_penalized():
    executor = make_executor()
    market = make_pair(price_usd="1.0", liquidity_usd="0")
    fill = executor.buy("TOKEN1", Decimal("10"), market)
    assert fill.price_usd > market.price_usd
