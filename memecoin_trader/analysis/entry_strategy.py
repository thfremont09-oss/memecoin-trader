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


def effective_score(signal: SocialSignal, corroborating_sources: int, config: EntryConfig) -> float:
    """A token independently flagged by 2+ distinct signal sources within
    entry.corroboration_window_minutes is stronger evidence than any one
    source's score alone, so it gets a score bonus -- this is what lets a
    signal too weak on its own (e.g. a middling Reddit mention) still clear
    the bar when it's corroborated by, say, Birdeye's trending list too."""
    if corroborating_sources >= 2:
        return min(100.0, signal.score + config.corroboration_bonus_score)
    return signal.score


def evaluate_entry(
    signal: SocialSignal,
    market: PairInfo | None,
    ctx: EntryContext,
    config: EntryConfig,
    rug_report: RugRiskReport | None = None,
    ml_confidence: float | None = None,
    corroborating_sources: int = 1,
) -> EntryDecision | None:
    """Returns an EntryDecision if we should buy, otherwise None.

    Every rejection reason is meaningful for tuning the strategy later, so
    keep them specific rather than collapsing to a bare False.
    """
    if effective_score(signal, corroborating_sources, config) < config.mention_score_threshold:
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

    buy_sell_ratio = market.buys_24h / (market.sells_24h + 1)
    if buy_sell_ratio < config.min_buy_sell_ratio:
        return None  # more selling than buying in the last 24h — distribution/dumping, not accumulation

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
            if len(rug_report.warning_flags) > config.rug_check.max_warning_flags:
                return None  # no single dealbreaker, but too many smaller red flags stacked up
            if (
                rug_report.lp_locked_pct is not None
                and rug_report.lp_locked_pct < config.rug_check.min_lp_locked_pct
            ):
                return None
            # Checked directly rather than relying solely on RugCheck's own
            # danger/warning labeling for these two: an un-renounced mint
            # authority (unlimited new supply) or an active freeze authority
            # (deployer can freeze your wallet's tokens) are unambiguous
            # rug vectors regardless of how RugCheck classifies them.
            if rug_report.mint_authority_renounced is False:
                return None
            if rug_report.freeze_authority_renounced is False:
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
