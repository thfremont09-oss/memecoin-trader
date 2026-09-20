"""Real Farcaster casts as a signal source, via Neynar's free-tier cast
search API (https://docs.neynar.com) -- Farcaster's own protocol-level Hub
API doesn't support full-text search, so Neynar (the standard developer
platform built on top of it) is the practical way in. Needs a free,
self-serve NEYNAR_API_KEY -- no approval wait.

Farcaster's community skews Ethereum/Base-oriented rather than
Solana-specific, so expect fewer hits here than Twitter/Reddit/Bluesky for
this bot's Solana-only universe -- it's still a real, independent source
worth having for whatever crossover chatter exists.

The response shape here is taken from Neynar's public docs, not verified
live against the API from the sandbox this was built in (no network access
to neynar.com there) -- if the JSON keys have drifted, poll() logs a
warning and returns no signals.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timezone

import requests

from memecoin_trader.config import FarcasterSignalConfig
from memecoin_trader.market.dexscreener import DexScreenerClient
from memecoin_trader.signals.base import SignalSource, SocialSignal
from memecoin_trader.signals.text_extraction import extract_token_addresses

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.neynar.com/v2/farcaster/cast/search"


class FarcasterSource(SignalSource):
    name = "farcaster"

    def __init__(
        self,
        config: FarcasterSignalConfig,
        api_key: str,
        chain_id: str,
        market_client: DexScreenerClient | None = None,
        session: requests.Session | None = None,
    ):
        if not api_key:
            raise ValueError("FarcasterSource requires a Neynar API key")
        self._config = config
        self._chain_id = chain_id
        self._market_client = market_client or DexScreenerClient()
        self._session = session or requests.Session()
        self._session.headers.update({"x-api-key": api_key, "accept": "application/json"})

    def _resolve_cashtag(self, symbol: str) -> str | None:
        results = self._market_client.search(symbol, chain_id=self._chain_id)
        if not results:
            return None
        best = max(results, key=lambda p: p.liquidity_usd)
        logger.info("resolved cashtag $%s -> %s via DexScreener search", symbol, best.token_address)
        return best.token_address

    @staticmethod
    def _engagement_score(cast: dict) -> float:
        reactions = cast.get("reactions") or {}
        likes = int(reactions.get("likes_count") or 0)
        recasts = int(reactions.get("recasts_count") or 0)
        replies = int((cast.get("replies") or {}).get("count") or 0)
        weighted = likes + 2 * recasts + replies
        return min(100.0, 12.0 * math.log1p(weighted))

    def poll(self) -> list[SocialSignal]:
        params = {"q": self._config.query, "limit": min(max(self._config.max_results_per_poll, 10), 100)}
        try:
            resp = self._session.get(SEARCH_URL, params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Farcaster (Neynar) search request failed: %s", exc)
            return []

        casts = (payload.get("result") or {}).get("casts")
        if casts is None:
            logger.warning("Neynar search response didn't have the expected result.casts shape")
            return []
        if not casts:
            return []

        per_token_scores: dict[str, list[float]] = defaultdict(list)
        per_token_excerpt: dict[str, str] = {}
        for cast in casts:
            text = cast.get("text") or ""
            score = self._engagement_score(cast)
            for token_address in extract_token_addresses(text, resolve_cashtag=self._resolve_cashtag):
                per_token_scores[token_address].append(score)
                per_token_excerpt.setdefault(token_address, text[:200])

        now = datetime.now(timezone.utc)
        signals = []
        for token_address, scores in per_token_scores.items():
            mention_count = len(scores)
            combined = min(100.0, max(scores) + 3.0 * (mention_count - 1))
            signals.append(
                SocialSignal(
                    token_address=token_address,
                    symbol=token_address[:8],
                    chain_id=self._chain_id,
                    source=self.name,
                    score=round(combined, 1),
                    mention_count=mention_count,
                    excerpt=per_token_excerpt[token_address],
                    observed_at=now,
                )
            )

        if signals:
            logger.info("Farcaster source emitted %d signal(s) from %d casts", len(signals), len(casts))
        return signals
