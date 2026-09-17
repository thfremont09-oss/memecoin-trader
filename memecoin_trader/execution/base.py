"""Common interface for turning a buy/sell decision into an actual fill."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal

from memecoin_trader.market.dexscreener import PairInfo


@dataclass(frozen=True)
class FillResult:
    price_usd: Decimal  # actual fill price after slippage
    quantity: Decimal
    amount_usd: Decimal  # gross USD moved (before fee for buys, before fee deduction shown separately)
    fee_usd: Decimal
    tx_id: str | None


class Executor(ABC):
    """Fills orders. PaperExecutor simulates against real market data;
    LiveExecutor (Solana) submits real swaps and is disabled by default."""

    mode: str

    @abstractmethod
    def buy(self, token_address: str, usd_amount: Decimal, market: PairInfo) -> FillResult:
        raise NotImplementedError

    @abstractmethod
    def sell(self, token_address: str, quantity: Decimal, market: PairInfo) -> FillResult:
        raise NotImplementedError
