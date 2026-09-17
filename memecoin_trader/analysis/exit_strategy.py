"""Decides when to pull out of an open position.

Checks run in a fixed priority order each tick: an emergency liquidity-rug
exit and a hard stop-loss both come before profit-taking, because capital
preservation matters more than optimizing an exit price.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from memecoin_trader.config import ExitConfig
from memecoin_trader.portfolio.models import Position


@dataclass(frozen=True)
class ExitDecision:
    reason: str  # "liquidity_rug_sudden" | "liquidity_rug" | "stop_loss" | "take_profit_partial" | "trailing_stop" | "time_exit"
    fraction: Decimal  # fraction of the *current remaining* quantity to sell, 0 < fraction <= 1
    mark_take_profit_taken: bool = False


def evaluate_exit(
    position: Position,
    current_price_usd: Decimal,
    current_liquidity_usd: Decimal,
    config: ExitConfig,
    now: datetime | None = None,
    previous_liquidity_usd: Decimal | None = None,
) -> ExitDecision | None:
    now = now or datetime.now(timezone.utc)
    entry = position.entry_price_usd

    if entry <= 0 or position.quantity <= 0:
        return None

    # Catches an in-progress rug within a single polling interval, rather
    # than waiting for the cumulative decline from entry (below) to cross
    # its threshold — a liquidity pull can easily happen faster than that.
    if previous_liquidity_usd is not None and previous_liquidity_usd > 0:
        drop_ratio = (previous_liquidity_usd - current_liquidity_usd) / previous_liquidity_usd
        if drop_ratio >= Decimal(str(config.sudden_liquidity_drop_pct)):
            return ExitDecision(reason="liquidity_rug_sudden", fraction=Decimal(1))

    if position.entry_liquidity_usd > 0:
        liquidity_ratio = current_liquidity_usd / position.entry_liquidity_usd
        if liquidity_ratio < Decimal(str(config.liquidity_rug_fraction)):
            return ExitDecision(reason="liquidity_rug", fraction=Decimal(1))

    loss_pct = (entry - current_price_usd) / entry
    if loss_pct >= Decimal(str(config.stop_loss_pct)):
        return ExitDecision(reason="stop_loss", fraction=Decimal(1))

    gain_pct = (current_price_usd - entry) / entry
    if not position.take_profit_taken and gain_pct >= Decimal(str(config.take_profit_pct)):
        return ExitDecision(
            reason="take_profit_partial",
            fraction=Decimal(str(config.take_profit_sell_fraction)),
            mark_take_profit_taken=True,
        )

    if position.peak_price_usd > entry:
        drawdown_from_peak = (position.peak_price_usd - current_price_usd) / position.peak_price_usd
        if drawdown_from_peak >= Decimal(str(config.trailing_stop_pct)):
            return ExitDecision(reason="trailing_stop", fraction=Decimal(1))

    held_minutes = (now - position.opened_at).total_seconds() / 60.0
    if held_minutes >= config.max_hold_minutes:
        return ExitDecision(reason="time_exit", fraction=Decimal(1))

    return None
