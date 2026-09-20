"""Raydium's own free, public pools API (https://api-v3.raydium.io) as a
signal source -- no key, no approval, ever. Raydium is one of the two or
three biggest Solana AMMs and where a lot of pump.fun graduates and other
memecoins end up listed, so its own by-volume pool ranking is a different,
more "where the real trading is happening" signal than a third-party
trending aggregator (Birdeye, GeckoTerminal) or DexScreener's paid-boost
list.

The `/pools/info/list` response shape here is taken from Raydium's public
API docs, not verified live against the API from the sandbox this was
built in (no network access to api-v3.raydium.io there) -- if the JSON
keys have drifted, poll() logs a warning and returns no signals.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from memecoin_trader.config import RaydiumSignalConfig
from memecoin_trader.signals.base import SignalSource, SocialSignal

logger = logging.getLogger(__name__)

POOLS_URL = "https://api-v3.raydium.io/pools/info/list"

# Well-known quote-side mints to skip when picking which side of a pool is
# the "interesting" token -- a pool is always priced against one of these,
# and it's never the token worth signaling on.
_KNOWN_QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112",  # wrapped SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}


def _pick_token_mint(pool: dict) -> tuple[str, str] | None:
    """Returns (address, symbol) for whichever side of the pool isn't a
    known quote asset, preferring mintA when neither or both sides match."""
    mint_a = pool.get("mintA") or {}
    mint_b = pool.get("mintB") or {}
    addr_a, addr_b = mint_a.get("address"), mint_b.get("address")
    if addr_b and addr_b not in _KNOWN_QUOTE_MINTS and addr_a in _KNOWN_QUOTE_MINTS:
        return addr_b, mint_b.get("symbol") or addr_b[:8]
    if addr_a:
        return addr_a, mint_a.get("symbol") or addr_a[:8]
    return None


class RaydiumPoolsSource(SignalSource):
    name = "raydium_pools"

    def __init__(self, config: RaydiumSignalConfig, chain_id: str, session: requests.Session | None = None):
        self._config = config
        self._chain_id = chain_id
        self._session = session or requests.Session()
        self._recently_signaled: dict[str, float] = {}

    @staticmethod
    def _rank_score(rank: int) -> float:
        return max(5.0, 100.0 - (max(rank, 1) - 1) * 4.0)

    def poll(self) -> list[SocialSignal]:
        params = {
            "poolType": "all",
            "poolSortField": "volume24h",
            "sortType": "desc",
            "pageSize": self._config.limit,
            "page": 1,
        }
        try:
            resp = self._session.get(POOLS_URL, params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Raydium pools request failed: %s", exc)
            return []

        pools = ((payload.get("data") or {}).get("data")) if isinstance(payload, dict) else None
        if pools is None:
            logger.warning("Raydium pools response didn't have the expected data.data shape")
            return []

        now = time.time()
        cooldown_seconds = self._config.mention_cooldown_minutes * 60
        observed_at = datetime.now(timezone.utc)
        signals = []
        for i, pool in enumerate(pools[: self._config.limit]):
            picked = _pick_token_mint(pool)
            if picked is None:
                continue
            address, symbol = picked
            if now - self._recently_signaled.get(address, 0) < cooldown_seconds:
                continue
            rank = i + 1
            signals.append(
                SocialSignal(
                    token_address=address,
                    symbol=symbol,
                    chain_id=self._chain_id,
                    source=self.name,
                    score=round(self._rank_score(rank), 1),
                    mention_count=1,
                    excerpt=f"Raydium pool by 24h volume #{rank}: {symbol}",
                    observed_at=observed_at,
                )
            )
            self._recently_signaled[address] = now

        if signals:
            logger.info("Raydium pools source emitted %d signal(s) from %d pools", len(signals), len(pools))
        return signals
