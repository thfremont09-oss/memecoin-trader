"""Decides whether a social signal should turn into a paper/live buy."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from memecoin_trader.config import EntryConfig
from memecoin_trader.market.dexscreener import PairInfo
from memecoin_trader.market.rugcheck import RugRiskReport
from memecoin_trader.signals.base import SocialSignal


@dataclass(frozen=True)
class EntryDecision:
    amount_usd: Decimal
    reason: str


@dataclass(frozen=True)
class EntryContext:
    cash_usd: Decimal
    open_position_count: int
    already_holds_token: bool
    token_on_cooldown: bool


def evaluate_entry(
    signal: SocialSignal,
    market: PairInfo | None,
    ctx: EntryContext,
    config: EntryConfig,
    rug_report: RugRiskReport | None = None,
    ml_confidence: float | None = None,
) -> EntryDecision | None:
    """Returns an EntryDecision if we should buy, otherwise None.

    Every rejection reason is meaningful for tuning the strategy later, so
    keep them specific rather than collapsing to a bare False.
    """
    if signal.score < config.mention_score_threshold:
        return None
    if ctx.already_holds_token:
        return None
    if ctx.token_on_cooldown:
        return None
    if ctx.open_position_count >= config.max_open_positions:
        return None
    if market is None:
        return None
    if market.liquidity_usd < Decimal(str(config.min_liquidity_usd)):
        return None
    if market.volume_24h_usd < Decimal(str(config.min_volume_24h_usd)):
        return None

    age_minutes = market.age_minutes
    if age_minutes is not None:
        if age_minutes < config.min_pair_age_minutes:
            return None
        if age_minutes > config.max_pair_age_hours * 60:
            return None

    if market.price_change_5m_pct > config.max_price_change_5m_pct:
        return None  # already pumped hard in the last 5 minutes — chasing the top

    if market.fdv_usd is not None and market.fdv_usd > 0:
        liquidity_to_fdv_pct = float(market.liquidity_usd / market.fdv_usd * 100)
        if liquidity_to_fdv_pct < config.min_liquidity_to_fdv_pct:
            return None  # liquidity is a razor-thin sliver of the reported valuation

    if config.rug_check.enabled:
        if rug_report is None:
            if config.rug_check.fail_closed:
                return None  # couldn't verify safety and we default to caution
        else:
            if len(rug_report.danger_flags) > config.rug_check.max_danger_flags:
                return None
            if (
                rug_report.lp_locked_pct is not None
                and rug_report.lp_locked_pct < config.rug_check.min_lp_locked_pct
            ):
                return None

    if config.ml.enabled and ml_confidence is not None:
        if ml_confidence < config.ml.min_confidence:
            return None

    if ctx.cash_usd < Decimal(str(config.min_trade_usd)):
        return None

    target = ctx.cash_usd * Decimal(str(config.position_size_pct_of_cash))
    amount = max(Decimal(str(config.min_trade_usd)), target)
    amount = min(amount, Decimal(str(config.max_trade_usd)), ctx.cash_usd)

    if amount < Decimal(str(config.min_trade_usd)):
        return None

    return EntryDecision(amount_usd=amount, reason="signal_entry")
