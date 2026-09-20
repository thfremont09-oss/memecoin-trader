"""DexScreener's own free, public "token boosts" API
(https://docs.dexscreener.com/api/reference#token-boosts) as a signal
source -- no key, no approval, ever. It uses the exact same host this bot
already depends on for market data, just a different endpoint.

A "boost" is a paid promotion a token team buys to get more visibility on
DexScreener's own site -- it's marketing spend, not organic momentum, so
it's independent of (and a different kind of signal than) Birdeye's
trending ranking, DexScreener's own price/volume data, or any social-media
source. Every other filter (rug check, liquidity, ML gate) still applies
before a buy happens, same as any other source.

The `/token-boosts/top/v1` response shape here is taken from DexScreener's
public docs, not verified live against the API from the sandbox this was
built in (no network access to api.dexscreener.com there) -- if the JSON
keys have drifted, poll() logs a warning and returns no signals.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from memecoin_trader.config import DexScreenerBoostsSignalConfig
from memecoin_trader.signals.base import SignalSource, SocialSignal

logger = logging.getLogger(__name__)

TOP_BOOSTS_URL = "https://api.dexscreener.com/token-boosts/top/v1"


class DexScreenerBoostsSource(SignalSource):
    name = "dexscreener_boosts"

    def __init__(self, config: DexScreenerBoostsSignalConfig, chain_id: str, session: requests.Session | None = None):
        self._config = config
        self._chain_id = chain_id
        self._session = session or requests.Session()
        self._recently_signaled: dict[str, float] = {}

    @staticmethod
    def _rank_score(rank: int) -> float:
        # Same shape as Birdeye's rank curve: rank 1 (most-boosted) scores
        # highest, tapering off after roughly the top 15-20.
        return max(5.0, 100.0 - (max(rank, 1) - 1) * 4.0)

    def poll(self) -> list[SocialSignal]:
        try:
            resp = self._session.get(TOP_BOOSTS_URL, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("DexScreener token-boosts request failed: %s", exc)
            return []

        if not isinstance(payload, list):
            logger.warning("DexScreener token-boosts response wasn't the expected list shape")
            return []

        boosted = [item for item in payload if item.get("chainId") == self._chain_id]

        now = time.time()
        cooldown_seconds = self._config.mention_cooldown_minutes * 60
        observed_at = datetime.now(timezone.utc)
        signals = []
        for i, item in enumerate(boosted[: self._config.limit]):
            address = item.get("tokenAddress")
            if not address:
                continue
            if now - self._recently_signaled.get(address, 0) < cooldown_seconds:
                continue
            rank = i + 1
            symbol = address[:8]
            total_amount = item.get("totalAmount", "?")
            signals.append(
                SocialSignal(
                    token_address=address,
                    symbol=symbol,
                    chain_id=self._chain_id,
                    source=self.name,
                    score=round(self._rank_score(rank), 1),
                    mention_count=1,
                    excerpt=f"DexScreener boosted #{rank}: {address} (total boost {total_amount})",
                    observed_at=observed_at,
                )
            )
            self._recently_signaled[address] = now

        if signals:
            logger.info(
                "DexScreener boosts source emitted %d signal(s) from %d boosted %s tokens",
                len(signals), len(boosted), self._chain_id,
            )
        return signals
