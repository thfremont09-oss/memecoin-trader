"""Real X (Twitter) API v2 adapter.

Disabled until TWITTER_BEARER_TOKEN is set (requires an X API plan with
access to recent search — this is a paid tier as of the API's current
pricing). Once enabled, this produces the exact same SocialSignal shape as
MockTwitterSource, so the rest of the bot (entry strategy, ledger, engine)
does not need to change at all to go from simulated to real hype detection.
"""
from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import requests

from memecoin_trader.config import TwitterSignalConfig
from memecoin_trader.market.dexscreener import DexScreenerClient
from memecoin_trader.signals.base import SignalSource, SocialSignal

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.twitter.com/2/tweets/search/recent"

# Solana addresses are base58 (no 0, O, I, l), typically 32-44 chars.
_SOLANA_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
_CASHTAG_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,9})\b")


class TwitterAPISource(SignalSource):
    name = "twitter_api"

    def __init__(
        self,
        config: TwitterSignalConfig,
        bearer_token: str,
        chain_id: str,
        market_client: DexScreenerClient | None = None,
    ):
        if not bearer_token:
            raise ValueError("TwitterAPISource requires a bearer token")
        self._config = config
        self._chain_id = chain_id
        self._market_client = market_client or DexScreenerClient()
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {bearer_token}"})
        self._since_id: str | None = None

    def _resolve_cashtag(self, symbol: str) -> str | None:
        """Best-effort: map a $SYMBOL mention to a real token address via search.

        Heuristic and unreliable (symbols collide across many tokens) — we
        take the highest-liquidity match on the configured chain and log it
        clearly so anything downstream can be audited.
        """
        results = self._market_client.search(symbol, chain_id=self._chain_id)
        if not results:
            return None
        best = max(results, key=lambda p: p.liquidity_usd)
        logger.info("resolved cashtag $%s -> %s via DexScreener search", symbol, best.token_address)
        return best.token_address

    @staticmethod
    def _engagement_score(metrics: dict[str, Any]) -> float:
        likes = int(metrics.get("like_count", 0))
        retweets = int(metrics.get("retweet_count", 0))
        quotes = int(metrics.get("quote_count", 0))
        replies = int(metrics.get("reply_count", 0))
        weighted = likes + 2 * retweets + 2 * quotes + replies
        # log-scale so a handful of viral tweets doesn't blow the score past 100
        return min(100.0, 12.0 * math.log1p(weighted))

    def _extract_token_addresses(self, text: str) -> list[str]:
        addresses = _SOLANA_ADDRESS_RE.findall(text)
        if addresses:
            return addresses
        cashtags = _CASHTAG_RE.findall(text)
        resolved = []
        for tag in cashtags[:2]:  # avoid burning search calls on spammy multi-tag posts
            addr = self._resolve_cashtag(tag)
            if addr:
                resolved.append(addr)
        return resolved

    def poll(self) -> list[SocialSignal]:
        params: dict[str, Any] = {
            "query": self._config.query,
            "max_results": min(max(self._config.max_results_per_poll, 10), 100),
            "tweet.fields": "public_metrics,created_at",
        }
        if self._since_id:
            params["since_id"] = self._since_id

        try:
            resp = self._session.get(SEARCH_URL, params=params, timeout=15)
            if resp.status_code == 429:
                logger.warning("Twitter API rate-limited, backing off this poll")
                return []
            resp.raise_for_status()
            payload = resp.json()
        except (requests.RequestException, ValueError) as exc:
            logger.warning("Twitter API request failed: %s", exc)
            return []

        tweets = payload.get("data") or []
        meta = payload.get("meta") or {}
        if meta.get("newest_id"):
            self._since_id = meta["newest_id"]
        if not tweets:
            return []

        per_token_scores: dict[str, list[float]] = defaultdict(list)
        per_token_excerpt: dict[str, str] = {}
        for tweet in tweets:
            text = tweet.get("text", "")
            score = self._engagement_score(tweet.get("public_metrics") or {})
            for token_address in self._extract_token_addresses(text):
                per_token_scores[token_address].append(score)
                per_token_excerpt.setdefault(token_address, text[:200])

        now = datetime.now(timezone.utc)
        signals = []
        for token_address, scores in per_token_scores.items():
            mention_count = len(scores)
            # more independent mentions push the score up, capped at 100
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
            logger.info("twitter API source emitted %d signal(s) from %d tweets", len(signals), len(tweets))
        return signals
