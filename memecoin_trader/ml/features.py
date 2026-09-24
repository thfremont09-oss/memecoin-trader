"""Turns a signal + market snapshot (+ optional rug report) into the fixed
numeric feature vector the trade-quality model trains and predicts on.

Missing/unknown values are filled with 0.0 rather than dropped, since the
model needs a fixed-width vector every time — this is a deliberate
simplification appropriate for a small logistic-regression baseline, not a
claim that 0 is a meaningful value for e.g. "liquidity/FDV ratio unknown".
"""
from __future__ import annotations

from typing import Final

from memecoin_trader.market.dexscreener import PairInfo
from memecoin_trader.market.rugcheck import RugRiskReport
from memecoin_trader.signals.base import SocialSignal

FEATURE_NAMES: Final[list[str]] = [
    "signal_score",
    "mention_count",
    "liquidity_usd",
    "volume_24h_usd",
    "fdv_usd",
    "liquidity_to_fdv_pct",
    "price_change_5m_pct",
    "price_change_1h_pct",
    "price_change_24h_pct",
    "buy_sell_ratio",
    "pair_age_minutes",
    "rug_score",
    "rug_danger_flag_count",
    "rug_lp_locked_pct",
    "rug_top_holder_pct",
    "corroborating_source_count",
]


def extract_features(
    signal: SocialSignal,
    market: PairInfo,
    rug_report: RugRiskReport | None = None,
    corroborating_sources: int = 0,
) -> dict[str, float]:
    fdv = float(market.fdv_usd) if market.fdv_usd else 0.0
    liquidity = float(market.liquidity_usd)
    liquidity_to_fdv_pct = (liquidity / fdv * 100) if fdv > 0 else 0.0
    age_minutes = market.age_minutes

    return {
        "signal_score": float(signal.score),
        "mention_count": float(signal.mention_count),
        "liquidity_usd": liquidity,
        "volume_24h_usd": float(market.volume_24h_usd),
        "fdv_usd": fdv,
        "liquidity_to_fdv_pct": liquidity_to_fdv_pct,
        "price_change_5m_pct": float(market.price_change_5m_pct),
        "price_change_1h_pct": float(market.price_change_1h_pct),
        "price_change_24h_pct": float(market.price_change_24h_pct),
        "buy_sell_ratio": market.buys_24h / (market.sells_24h + 1),
        "pair_age_minutes": float(age_minutes) if age_minutes is not None else 0.0,
        "rug_score": float(rug_report.score) if rug_report else 0.0,
        "rug_danger_flag_count": float(len(rug_report.danger_flags)) if rug_report else 0.0,
        "rug_lp_locked_pct": (
            float(rug_report.lp_locked_pct)
            if (rug_report is not None and rug_report.lp_locked_pct is not None)
            else 0.0
        ),
        "rug_top_holder_pct": (
            float(rug_report.top_holder_pct)
            if (rug_report is not None and rug_report.top_holder_pct is not None)
            else 0.0
        ),
        "corroborating_source_count": float(corroborating_sources),
    }


def features_to_vector(features: dict[str, float]) -> list[float]:
    """Fixed-order vector for feeding to the model, tolerant of extra/missing keys."""
    return [features.get(name, 0.0) for name in FEATURE_NAMES]
