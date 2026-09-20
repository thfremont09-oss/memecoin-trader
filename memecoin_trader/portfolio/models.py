"""Plain data models shared by the ledger, strategies and dashboard."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass
class Position:
    id: int
    token_address: str
    symbol: str
    chain_id: str
    entry_price_usd: Decimal
    quantity: Decimal  # remaining quantity (shrinks on partial take-profit)
    original_quantity: Decimal
    cost_basis_usd: Decimal  # remaining cost basis matching `quantity`
    fees_paid_usd: Decimal
    peak_price_usd: Decimal
    entry_liquidity_usd: Decimal
    take_profit_taken: bool
    opened_at: datetime
    signal_source: str
    signal_score: float
    status: str  # "open" | "closed"

    def unrealized_pnl_usd(self, current_price_usd: Decimal) -> Decimal:
        return self.quantity * current_price_usd - self.cost_basis_usd

    def unrealized_pnl_pct(self, current_price_usd: Decimal) -> float:
        if self.entry_price_usd == 0:
            return 0.0
        return float((current_price_usd - self.entry_price_usd) / self.entry_price_usd * 100)


@dataclass
class Trade:
    id: int
    position_id: int | None
    token_address: str
    symbol: str
    side: str  # "buy" | "sell"
    price_usd: Decimal
    quantity: Decimal
    amount_usd: Decimal
    fee_usd: Decimal
    realized_pnl_usd: Decimal | None
    reason: str
    executed_at: datetime
    mode: str


@dataclass
class PortfolioState:
    cash_usd: Decimal
    realized_pnl_usd: Decimal
    starting_balance_usd: Decimal
    updated_at: datetime
    trading_enabled: bool
    caution_level: int
