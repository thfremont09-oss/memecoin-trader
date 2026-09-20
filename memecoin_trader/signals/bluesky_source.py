"""Real Bluesky (AT Protocol) posts as a signal source, via Bluesky's free,
fully public searchPosts endpoint -- no API key, no OAuth, no approval
process. Unlike X/Twitter, open unauthenticated reads are the AT Protocol's
actual design intent (the whole point of the protocol is a permissionless
public data layer), so there's no ToS-violation risk here comparable to the
Twitter scraper.

The response shape here is taken from Bluesky's public docs
(docs.bsky.app), not verified live against the API from the sandbox this
was built in (no network access to bsky.app there) -- if the JSON keys
have drifted, poll() logs a warning and returns no signals.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from datetime import datetime, timezone

import requests

from memecoin_trader.config import BlueskySignalConfig
from memecoin_trader.market.dexscreener import DexScreenerClient
from memecoin_trader.signals.base import SignalSource, SocialSignal
from memecoin_trader.signals.text_extraction import extract_token_addresses

logger = logging.getLogger(__name__)

SEARCH_URL = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"


class BlueskySource(SignalSource):
    name = "bluesky"

    def __init__(
        self,
        config: BlueskySignalConfig,
        chain_id: str,
        market_client: DexScreenerClient | None = None,
        session: requests.Session | None = None,
    ):
        self._config = config
        self._chain_id = chain_id
        self._market_client = market_client or DexScreenerClient()
        self._session = session or requests.Session()

    def _resolve_cashtag(self, symbol: str) -> str | None:
        results = self._market_client.search(symbol, chain_id=self._chain_id)
        if not results:
            return None
        best = max(results, key=lambda p: p.liquidity_usd)
        logger.info("resolved cashtag $%s -> %s via DexScreener search", symbol, best.token_address)
        return best.token_address

    @staticmethod
    def _engagement_score(post: dict) -> float:
        likes = int(post.get("likeCount") or 0)
        reposts = int(post.get("repostCount") or 0)
        replies = int(post.get("replyCount") or 0)
        weighted = likes + 2 * reposts + replies
        return min(100.0, 12.0 * math.log1p(weighted))

    def poll(self) -> list[SocialSignal]:
        params = {"q": self._config.query, "limit": min(max(self._config.max_results_per_poll, 10), 100)}
        try:
            resp = self._session.get(SEARCH_URL, params=params, timeout=15)
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Bluesky search request failed: %s", exc)
            return []

        posts = payload.get("posts")
        if posts is None:
            logger.warning("Bluesky search response didn't have the expected 'posts' shape")
            return []
        if not posts:
            return []

        per_token_scores: dict[str, list[float]] = defaultdict(list)
        per_token_excerpt: dict[str, str] = {}
        for post in posts:
            text = ((post.get("record") or {}).get("text")) or ""
            score = self._engagement_score(post)
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
            logger.info("Bluesky source emitted %d signal(s) from %d posts", len(signals), len(posts))
        return signals
