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


def _confidence(
    signal: SocialSignal, corroborating_sources: int, config: EntryConfig, ml_confidence: float | None
) -> float:
    """How sure we are this is a good trade, 0.0-1.0 -- the trained ML
    model's own predicted P(profitable) when one was passed in, else the
    combined hype score (including any corroboration bonus) normalized to
    0-1, the same "stronger evidence" signal corroboration already uses
    for gating. Used only for position sizing, never for the buy/no-buy
    decision itself."""
    if ml_confidence is not None:
        return max(0.0, min(1.0, ml_confidence))
    return max(0.0, min(1.0, effective_score(signal, corroborating_sources, config) / 100.0))


def _position_amount(
    signal: SocialSignal,
    ctx: EntryContext,
    config: EntryConfig,
    ml_confidence: float | None,
    corroborating_sources: int,
) -> Decimal:
    """Scales with both account size and confidence: the account can afford
    a bigger bet as cash grows (up to max_trade_usd, a hard ceiling
    regardless of balance), and within whatever room that leaves, a more
    confident signal commits more of it -- so a well-funded, high-confidence
    trade approaches the cap, a shaky one on that same account stays
    modest, and a small account's trades barely move even at full
    confidence."""
    min_amount = Decimal(str(config.min_trade_usd))
    max_amount = Decimal(str(config.max_trade_usd))
    cash_target = ctx.cash_usd * Decimal(str(config.position_size_pct_of_cash))
    cash_room = min(cash_target, max_amount)
    if config.confidence_scaled_sizing:
        confidence = Decimal(str(_confidence(signal, corroborating_sources, config, ml_confidence)))
        amount = min_amount + confidence * (cash_room - min_amount)
    else:
        # Big Risk's own entry_config override turns this off: it disables
        # the score gate entirely, so a signal's score here isn't a
        # meaningful confidence reading, and this mode is meant to commit
        # as close to cash_room (position_size_pct_of_cash=0.97, capped at
        # max_position_usd) as it can regardless.
        amount = cash_room
    amount = max(amount, min_amount)
    return min(amount, max_amount, ctx.cash_usd)


def rejection_reason(
    signal: SocialSignal,
    market: PairInfo | None,
    ctx: EntryContext,
    config: EntryConfig,
    rug_report: RugRiskReport | None = None,
    ml_confidence: float | None = None,
    corroborating_sources: int = 1,
) -> str | None:
    """The same checks evaluate_entry runs, but returning a short, specific
    reason string for the first one that fails (or None if every one would
    pass) instead of collapsing straight to a bare buy/no-buy decision --
    exists purely so a poll that buys nothing can log *why*, without ever
    affecting the actual buy decision itself."""
    if effective_score(signal, corroborating_sources, config) < config.mention_score_threshold:
        return "score_below_threshold"
    if ctx.already_holds_token:
        return "already_holds_token"
    if ctx.token_on_cooldown:
        return "token_on_cooldown"
    if ctx.open_position_count >= config.max_open_positions:
        return "max_open_positions"
    if market is None:
        return "no_market_data"
    if market.liquidity_usd < Decimal(str(config.min_liquidity_usd)):
        return "liquidity_too_low"
    if market.volume_24h_usd < Decimal(str(config.min_volume_24h_usd)):
        return "volume_too_low"

    age_minutes = market.age_minutes
    if age_minutes is not None:
        if age_minutes < config.min_pair_age_minutes:
            return "pair_too_young"
        if age_minutes > config.max_pair_age_hours * 60:
            return "pair_too_old"

    if market.price_change_5m_pct > config.max_price_change_5m_pct:
        return "already_pumped"  # already pumped hard in the last 5 minutes — chasing the top

    buy_sell_ratio = market.buys_24h / (market.sells_24h + 1)
    if buy_sell_ratio < config.min_buy_sell_ratio:
        return "weak_buy_pressure"  # more selling than buying in the last 24h — distribution/dumping, not accumulation

    if market.fdv_usd is not None and market.fdv_usd > 0:
        liquidity_to_fdv_pct = float(market.liquidity_usd / market.fdv_usd * 100)
        if liquidity_to_fdv_pct < config.min_liquidity_to_fdv_pct:
            return "thin_liquidity_to_fdv"  # liquidity is a razor-thin sliver of the reported valuation

    if config.rug_check.enabled:
        if rug_report is None:
            if config.rug_check.fail_closed:
                return "rug_check_unavailable"  # couldn't verify safety and we default to caution
        else:
            if len(rug_report.danger_flags) > config.rug_check.max_danger_flags:
                return "rug_check_danger_flags"
            if len(rug_report.warning_flags) > config.rug_check.max_warning_flags:
                return "rug_check_warning_flags"  # no single dealbreaker, but too many smaller red flags stacked up
            if (
                rug_report.lp_locked_pct is not None
                and rug_report.lp_locked_pct < config.rug_check.min_lp_locked_pct
            ):
                return "rug_check_lp_not_locked"
            if (
                rug_report.top_holder_pct is not None
                and rug_report.top_holder_pct > config.rug_check.max_top_holder_pct
            ):
                return "rug_check_top_holder_concentration"  # LP locked doesn't stop a whale from dumping their own bag
            # Checked directly rather than relying solely on RugCheck's own
            # danger/warning labeling for these two: an un-renounced mint
            # authority (unlimited new supply) or an active freeze authority
            # (deployer can freeze your wallet's tokens) are unambiguous
            # rug vectors regardless of how RugCheck classifies them.
            if rug_report.mint_authority_renounced is False:
                return "mint_authority_active"
            if rug_report.freeze_authority_renounced is False:
                return "freeze_authority_active"

    if config.ml.enabled and ml_confidence is not None:
        if ml_confidence < config.ml.min_confidence:
            return "ml_confidence_too_low"

    if ctx.cash_usd < Decimal(str(config.min_trade_usd)):
        return "insufficient_cash"

    amount = _position_amount(signal, ctx, config, ml_confidence, corroborating_sources)
    if amount < Decimal(str(config.min_trade_usd)):
        return "amount_below_minimum"

    return None


def evaluate_entry(
    signal: SocialSignal,
    market: PairInfo | None,
    ctx: EntryContext,
    config: EntryConfig,
    rug_report: RugRiskReport | None = None,
    ml_confidence: float | None = None,
    corroborating_sources: int = 1,
) -> EntryDecision | None:
    """Returns an EntryDecision if we should buy, otherwise None. See
    rejection_reason for *why* a None came back."""
    if (
        rejection_reason(signal, market, ctx, config, rug_report, ml_confidence, corroborating_sources)
        is not None
    ):
        return None

    amount = _position_amount(signal, ctx, config, ml_confidence, corroborating_sources)
    return EntryDecision(amount_usd=amount, reason="signal_entry")
