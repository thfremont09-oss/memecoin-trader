from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from memecoin_trader.market.dexscreener import PairInfo
from memecoin_trader.market.rugcheck import RugRiskReport
from memecoin_trader.portfolio.db import get_connection, init_db
from memecoin_trader.portfolio.ledger import Ledger
from memecoin_trader.signals.base import SocialSignal


@pytest.fixture
def conn(tmp_path):
    c = get_connection(tmp_path / "test.db")
    init_db(c, Decimal("100"))
    yield c
    c.close()


@pytest.fixture
def ledger(conn):
    return Ledger(conn)


def make_pair(
    token_address: str = "TOKEN1111111111111111111111111111111111111",
    price_usd: str = "0.001",
    liquidity_usd: str = "50000",
    volume_24h_usd: str = "50000",
    age_minutes: float = 60,
    symbol: str = "MEME",
    fdv_usd: str | None = "1000000",
    price_change_5m_pct: float = 0.0,
    buys_24h: int = 10,
    sells_24h: int = 5,
) -> PairInfo:
    created_at = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    return PairInfo(
        chain_id="solana",
        dex_id="raydium",
        pair_address="PAIR111111111111111111111111111111111111111",
        token_address=token_address,
        symbol=symbol,
        name=symbol,
        price_usd=Decimal(price_usd),
        liquidity_usd=Decimal(liquidity_usd),
        volume_24h_usd=Decimal(volume_24h_usd),
        price_change_5m_pct=price_change_5m_pct,
        price_change_1h_pct=0.0,
        price_change_24h_pct=0.0,
        buys_24h=buys_24h,
        sells_24h=sells_24h,
        pair_created_at=created_at,
        fdv_usd=Decimal(fdv_usd) if fdv_usd is not None else None,
        url="https://dexscreener.com/solana/pair111",
    )


def make_rug_report(
    token_address: str = "TOKEN1111111111111111111111111111111111111",
    score: float = 5.0,
    danger_flags: tuple[str, ...] = (),
    warning_flags: tuple[str, ...] = (),
    lp_locked_pct: float | None = 90.0,
) -> RugRiskReport:
    return RugRiskReport(
        token_address=token_address,
        score=score,
        danger_flags=danger_flags,
        warning_flags=warning_flags,
        mint_authority_renounced=True,
        freeze_authority_renounced=True,
        lp_locked_pct=lp_locked_pct,
    )


def make_signal(
    token_address: str = "TOKEN1111111111111111111111111111111111111",
    score: float = 80.0,
    symbol: str = "MEME",
    source: str = "twitter_mock",
) -> SocialSignal:
    return SocialSignal(
        token_address=token_address,
        symbol=symbol,
        chain_id="solana",
        source=source,
        score=score,
        mention_count=10,
        excerpt="[SIMULATED] test excerpt",
        observed_at=datetime.now(timezone.utc),
    )
