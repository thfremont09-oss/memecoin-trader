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
    reason: str  # "liquidity_rug_catastrophic" | "liquidity_rug_sudden" | "liquidity_rug" | "stop_loss" | "take_profit_partial" | "trailing_stop" | "time_exit"
    fraction: Decimal  # fraction of the *current remaining* quantity to sell, 0 < fraction <= 1
    mark_take_profit_taken: bool = False


def _trailing_stop_pct_for(peak_gain_pct: Decimal, config: ExitConfig) -> Decimal:
    """The trailing stop tightens as a position's best-ever gain grows ("run
    away when profits get maximized") -- returns the tightest
    trailing_stop_pct among every tier whose peak_gain_pct threshold the
    position has reached, or the flat baseline if none has."""
    best = Decimal(str(config.trailing_stop_pct))
    for tier in config.trailing_stop_tiers:
        if peak_gain_pct >= Decimal(str(tier.peak_gain_pct)):
            tier_pct = Decimal(str(tier.trailing_stop_pct))
            if tier_pct < best:
                best = tier_pct
    return best


def evaluate_exit(
    position: Position,
    current_price_usd: Decimal,
    current_liquidity_usd: Decimal,
    config: ExitConfig,
    now: datetime | None = None,
    previous_liquidity_usd: Decimal | None = None,
    immediate_previous_liquidity_usd: Decimal | None = None,
) -> ExitDecision | None:
    now = now or datetime.now(timezone.utc)
    entry = position.entry_price_usd

    if entry <= 0 or position.quantity <= 0:
        return None

    # Catches a genuine LP pull the instant it happens, comparing against
    # the literal last poll (however recent) rather than waiting for the
    # windowed check below — a real rug can drain a pool within a single
    # tick. The bar is set high enough (70%+ in one tick) that ordinary
    # thin-pool trade noise essentially never trips it; that's what the
    # windowed check below is for.
    if immediate_previous_liquidity_usd is not None and immediate_previous_liquidity_usd > 0:
        catastrophic_drop_ratio = (
            immediate_previous_liquidity_usd - current_liquidity_usd
        ) / immediate_previous_liquidity_usd
        if catastrophic_drop_ratio >= Decimal(str(config.catastrophic_liquidity_drop_pct)):
            return ExitDecision(reason="liquidity_rug_catastrophic", fraction=Decimal(1))

    # Catches an in-progress rug within a wider window, rather than waiting
    # for the cumulative decline from entry (below) to cross its threshold —
    # a liquidity pull can easily happen faster than that.
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
        peak_gain_pct = (position.peak_price_usd - entry) / entry
        trailing_stop_pct = _trailing_stop_pct_for(peak_gain_pct, config)
        drawdown_from_peak = (position.peak_price_usd - current_price_usd) / position.peak_price_usd
        if drawdown_from_peak >= trailing_stop_pct:
            return ExitDecision(reason="trailing_stop", fraction=Decimal(1))

    held_minutes = (now - position.opened_at).total_seconds() / 60.0
    if held_minutes >= config.max_hold_minutes:
        return ExitDecision(reason="time_exit", fraction=Decimal(1))

    return None
