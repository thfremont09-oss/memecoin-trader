"""GeckoTerminal's free, public trending-pools API
(https://www.geckoterminal.com/dex-api) as a signal source -- no key, no
approval, ever. A different provider with a different trending
methodology from both Birdeye and DexScreener's own boosted-tokens list,
so it mostly adds independent corroboration weight on tokens the other
trending sources already flagged, plus occasionally surfaces its own
early picks.

The `/networks/{network}/trending_pools` response shape here is taken
from GeckoTerminal's public docs, not verified live against the API from
the sandbox this was built in (no network access to geckoterminal.com
there) -- if the JSON:API shape has drifted, poll() logs a warning and
returns no signals.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from memecoin_trader.config import GeckoTerminalSignalConfig
from memecoin_trader.signals.base import SignalSource, SocialSignal

logger = logging.getLogger(__name__)

TRENDING_POOLS_URL_TEMPLATE = "https://api.geckoterminal.com/api/v2/networks/{network}/trending_pools"

# GeckoTerminal's own network slugs don't always match this bot's internal
# chain_id (e.g. "solana" matches, but this keeps the door open if that
# ever changes without touching the source itself).
_NETWORK_SLUGS = {"solana": "solana"}


def _extract_token_address(pool: dict) -> str | None:
    base_token = ((pool.get("relationships") or {}).get("base_token") or {}).get("data") or {}
    token_id = base_token.get("id")
    if not token_id or "_" not in token_id:
        return None
    return token_id.split("_", 1)[1]


def _extract_symbol(pool: dict, fallback: str) -> str:
    name = ((pool.get("attributes") or {}).get("name")) or ""
    if "/" in name:
        return name.split("/", 1)[0].strip() or fallback
    return fallback


class GeckoTerminalTrendingSource(SignalSource):
    name = "geckoterminal_trending"

    def __init__(self, config: GeckoTerminalSignalConfig, chain_id: str, session: requests.Session | None = None):
        self._config = config
        self._chain_id = chain_id
        self._session = session or requests.Session()
        self._recently_signaled: dict[str, float] = {}

    @staticmethod
    def _rank_score(rank: int) -> float:
        return max(5.0, 100.0 - (max(rank, 1) - 1) * 4.0)

    def poll(self) -> list[SocialSignal]:
        network = _NETWORK_SLUGS.get(self._chain_id, self._chain_id)
        url = TRENDING_POOLS_URL_TEMPLATE.format(network=network)
        try:
            resp = self._session.get(url, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("GeckoTerminal trending-pools request failed: %s", exc)
            return []

        pools = payload.get("data")
        if pools is None:
            logger.warning("GeckoTerminal trending-pools response didn't have the expected 'data' shape")
            return []

        now = time.time()
        cooldown_seconds = self._config.mention_cooldown_minutes * 60
        observed_at = datetime.now(timezone.utc)
        signals = []
        for i, pool in enumerate(pools[: self._config.limit]):
            address = _extract_token_address(pool)
            if not address:
                continue
            if now - self._recently_signaled.get(address, 0) < cooldown_seconds:
                continue
            rank = i + 1
            symbol = _extract_symbol(pool, address[:8])
            signals.append(
                SocialSignal(
                    token_address=address,
                    symbol=symbol,
                    chain_id=self._chain_id,
                    source=self.name,
                    score=round(self._rank_score(rank), 1),
                    mention_count=1,
                    excerpt=f"GeckoTerminal trending #{rank}: {symbol}",
                    observed_at=observed_at,
                )
            )
            self._recently_signaled[address] = now

        if signals:
            logger.info("GeckoTerminal trending source emitted %d signal(s) from %d pools", len(signals), len(pools))
        return signals
