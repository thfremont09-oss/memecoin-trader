"""Birdeye's free-tier trending-tokens API (https://docs.birdeye.so) as a
signal source -- a third-party trending/momentum ranking independent of
DexScreener's own boosted-tokens list (used by the mock source) and of any
social-media hype signal. Needs a free BIRDEYE_API_KEY (self-serve signup,
no approval wait, unlike Reddit's current process).

The `/defi/token_trending` response shape here is taken from Birdeye's
public docs, not verified live against the API from the sandbox this was
built in (no network access to birdeye.so there) -- if the response's key
names have drifted, poll() logs a warning and returns no signals; check the
logs the first time this runs for real.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from memecoin_trader.config import BirdeyeSignalConfig
from memecoin_trader.signals.base import SignalSource, SocialSignal

logger = logging.getLogger(__name__)

TRENDING_URL = "https://public-api.birdeye.so/defi/token_trending"


class BirdeyeTrendingSource(SignalSource):
    name = "birdeye_trending"

    def __init__(
        self,
        config: BirdeyeSignalConfig,
        api_key: str,
        chain_id: str,
        session: requests.Session | None = None,
    ):
        if not api_key:
            raise ValueError("BirdeyeTrendingSource requires a Birdeye API key")
        self._config = config
        self._chain_id = chain_id
        self._session = session or requests.Session()
        self._session.headers.update(
            {"accept": "application/json", "x-chain": chain_id, "X-API-KEY": api_key}
        )
        self._recently_signaled: dict[str, float] = {}

    @staticmethod
    def _rank_score(rank: int) -> float:
        # Birdeye's own rank 1 (hottest) scores highest; the curve puts
        # roughly the top 15-20 ranked tokens above a typical entry
        # threshold, tapering off after that.
        return max(5.0, 100.0 - (max(rank, 1) - 1) * 4.0)

    def poll(self) -> list[SocialSignal]:
        params = {"sort_by": "rank", "sort_type": "asc", "offset": 0, "limit": self._config.limit}
        try:
            resp = self._session.get(TRENDING_URL, params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Birdeye trending request failed: %s", exc)
            return []

        tokens = (payload.get("data") or {}).get("tokens")
        if tokens is None:
            logger.warning("Birdeye trending response didn't have the expected data.tokens shape")
            return []

        now = time.time()
        cooldown_seconds = self._config.mention_cooldown_minutes * 60
        observed_at = datetime.now(timezone.utc)
        signals = []
        for i, token in enumerate(tokens):
            address = token.get("address")
            if not address:
                continue
            if now - self._recently_signaled.get(address, 0) < cooldown_seconds:
                continue
            rank = token.get("rank", i + 1)
            symbol = token.get("symbol") or address[:8]
            signals.append(
                SocialSignal(
                    token_address=address,
                    symbol=symbol,
                    chain_id=self._chain_id,
                    source=self.name,
                    score=round(self._rank_score(rank), 1),
                    mention_count=1,
                    excerpt=f"Birdeye trending #{rank}: {symbol} (24h vol ${token.get('volume24hUSD', '?')})",
                    observed_at=observed_at,
                )
            )
            self._recently_signaled[address] = now

        if signals:
            logger.info("Birdeye trending source emitted %d signal(s) from %d ranked tokens", len(signals), len(tokens))
        return signals
