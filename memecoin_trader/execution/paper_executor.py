"""Simulated fills against real market data.

Slippage scales with trade size relative to pool liquidity, the same way it
does on a real AMM — a $40 buy into a $5,000 pool moves the price a lot more
than the same buy into a $500,000 pool. This is what keeps the "accurate
simulation" promise: we're not just taking the quoted price at face value.
"""
from __future__ import annotations

from decimal import Decimal

from memecoin_trader.config import PaperExecutionConfig
from memecoin_trader.execution.base import Executor, FillResult
from memecoin_trader.market.dexscreener import PairInfo


class PaperExecutor(Executor):
    mode = "paper"

    def __init__(self, config: PaperExecutionConfig):
        self._config = config

    def _effective_slippage_bps(self, usd_amount: Decimal, liquidity_usd: Decimal) -> Decimal:
        base = Decimal(str(self._config.base_slippage_bps))
        if liquidity_usd <= 0:
            return base * 10  # unknown/zero liquidity: assume it's brutal
        impact = (usd_amount / liquidity_usd) * Decimal(str(self._config.slippage_impact_factor)) * Decimal(10000)
        return base + impact

    def buy(self, token_address: str, usd_amount: Decimal, market: PairInfo) -> FillResult:
        slippage_bps = self._effective_slippage_bps(usd_amount, market.liquidity_usd)
        fill_price = market.price_usd * (Decimal(1) + slippage_bps / Decimal(10000))
        fee_usd = usd_amount * Decimal(str(self._config.fee_bps)) / Decimal(10000)
        quantity = (usd_amount - fee_usd) / fill_price
        return FillResult(
            price_usd=fill_price,
            quantity=quantity,
            amount_usd=usd_amount,
            fee_usd=fee_usd,
            tx_id=None,
        )

    def sell(self, token_address: str, quantity: Decimal, market: PairInfo) -> FillResult:
        gross_proceeds_estimate = quantity * market.price_usd
        slippage_bps = self._effective_slippage_bps(gross_proceeds_estimate, market.liquidity_usd)
        fill_price = market.price_usd * (Decimal(1) - slippage_bps / Decimal(10000))
        fill_price = max(fill_price, Decimal(0))
        gross_proceeds = quantity * fill_price
        fee_usd = gross_proceeds * Decimal(str(self._config.fee_bps)) / Decimal(10000)
        return FillResult(
            price_usd=fill_price,
            quantity=quantity,
            amount_usd=gross_proceeds,
            fee_usd=fee_usd,
            tx_id=None,
        )
