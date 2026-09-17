"""Simulated Twitter/X hype feed.

We don't have X API access configured (see .env.example), so instead of
inventing tokens out of thin air, this source pulls **real** Solana tokens
that are currently being promoted / trending on DexScreener, and synthesizes
plausible "mention spike" events on top of them. This keeps the simulation's
market side 100% real (real prices, real liquidity, real slippage) while the
social side is clearly-labeled synthetic data standing in for Twitter.

Swapping in memecoin_trader.signals.twitter_source.TwitterAPISource later
requires no other code changes — same SocialSignal shape, same interface.
"""
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timezone

from memecoin_trader.config import MockSignalConfig
from memecoin_trader.market.dexscreener import DexScreenerClient
from memecoin_trader.signals.base import SignalSource, SocialSignal

logger = logging.getLogger(__name__)

_HYPE_TEMPLATES = [
    "[SIMULATED] 🚀 everyone in my tl is talking about ${symbol} rn, chart looks insane",
    "[SIMULATED] just aped into ${symbol}, feels like early gem energy",
    "[SIMULATED] ${symbol} volume going parabolic, cabal calling it the next 100x",
    "[SIMULATED] ${symbol} CT is going crazy, mcap still tiny for the attention it's getting",
    "[SIMULATED] saw ${symbol} on 4 different accounts in the last hour, fomo is real",
]


class MockTwitterSource(SignalSource):
    name = "twitter_mock"

    def __init__(self, config: MockSignalConfig, chain_id: str, client: DexScreenerClient | None = None):
        self._config = config
        self._chain_id = chain_id
        self._client = client or DexScreenerClient()
        self._pool: list[dict] = []
        self._pool_refreshed_at: float = 0.0
        self._recently_signaled: dict[str, float] = {}  # token_address -> last signal time

    def _refresh_pool_if_stale(self) -> None:
        age_seconds = time.time() - self._pool_refreshed_at
        if self._pool and age_seconds < self._config.trending_refresh_minutes * 60:
            return
        tokens = self._client.get_latest_boosted_tokens(chain_id=self._chain_id)
        if tokens:
            self._pool = tokens
            self._pool_refreshed_at = time.time()
            logger.info("mock twitter source refreshed trending pool: %d tokens", len(tokens))
        elif not self._pool:
            logger.warning("mock twitter source could not fetch a trending pool yet")

    def poll(self) -> list[SocialSignal]:
        self._refresh_pool_if_stale()
        if not self._pool:
            return []

        now = time.time()
        eligible = [
            t
            for t in self._pool
            if now - self._recently_signaled.get(t.get("tokenAddress", ""), 0) > 30 * 60
        ]
        if not eligible:
            return []

        k = min(self._config.max_signals_per_poll, len(eligible))
        chosen = random.sample(eligible, k=k) if random.random() < 0.6 else []

        signals: list[SocialSignal] = []
        for token in chosen:
            token_address = token.get("tokenAddress")
            if not token_address:
                continue
            symbol = (token.get("description") or token.get("tokenAddress"))[:12]
            score = round(random.uniform(35, 95), 1)
            mention_count = random.randint(3, 60)
            excerpt = random.choice(_HYPE_TEMPLATES).format(symbol=symbol)

            signals.append(
                SocialSignal(
                    token_address=token_address,
                    symbol=symbol,
                    chain_id=self._chain_id,
                    source=self.name,
                    score=score,
                    mention_count=mention_count,
                    excerpt=excerpt,
                    observed_at=datetime.now(timezone.utc),
                )
            )
            self._recently_signaled[token_address] = now

        if signals:
            logger.info("mock twitter source emitted %d signal(s)", len(signals))
        return signals
