"""Real Reddit signal source via Reddit's free, official API (PRAW).

Unlike the Twitter scraper, this needs no ToS-violating browser automation —
Reddit's API is free for read-only, non-commercial use with a "script" app
(see README's "Reddit" section for how to create one). Runs read-only, so
no user login/OAuth flow is needed, only an app client id/secret.

Produces the same SocialSignal shape as the other sources and is meant to
run *alongside* whichever Twitter source is active (see
engine.build_signal_source), not replace it — see composite_source.py.
"""
from __future__ import annotations

import logging
import math
import time
from collections import defaultdict
from datetime import datetime, timezone

from memecoin_trader.config import RedditSignalConfig, Secrets
from memecoin_trader.market.dexscreener import DexScreenerClient
from memecoin_trader.signals.base import SignalSource, SocialSignal
from memecoin_trader.signals.text_extraction import extract_token_addresses

logger = logging.getLogger(__name__)


class RedditSource(SignalSource):
    name = "reddit"

    def __init__(
        self,
        config: RedditSignalConfig,
        secrets: Secrets,
        chain_id: str,
        market_client: DexScreenerClient | None = None,
    ):
        if not (secrets.reddit_client_id and secrets.reddit_client_secret):
            raise ValueError("RedditSource requires REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET")
        self._config = config
        self._secrets = secrets
        self._chain_id = chain_id
        self._market_client = market_client or DexScreenerClient()
        self._reddit = None
        self._recently_signaled: dict[str, float] = {}

    def _resolve_cashtag(self, symbol: str) -> str | None:
        results = self._market_client.search(symbol, chain_id=self._chain_id)
        if not results:
            return None
        best = max(results, key=lambda p: p.liquidity_usd)
        logger.info("resolved cashtag $%s -> %s via DexScreener search", symbol, best.token_address)
        return best.token_address

    def _ensure_client(self):
        if self._reddit is not None:
            return self._reddit
        import praw

        self._reddit = praw.Reddit(
            client_id=self._secrets.reddit_client_id,
            client_secret=self._secrets.reddit_client_secret,
            user_agent=self._secrets.reddit_user_agent,
        )
        self._reddit.read_only = True
        return self._reddit

    @staticmethod
    def _engagement_score(upvotes: int, num_comments: int) -> float:
        weighted = max(upvotes, 0) + 2 * max(num_comments, 0)
        # log-scale so one viral post doesn't blow the score past 100
        return min(100.0, 12.0 * math.log1p(weighted))

    def _fetch_posts(self) -> list[tuple[str, float, int]]:
        """Returns (text, engagement_score, post_id) tuples for new posts
        across every configured subreddit. Split out so tests can stub this
        one method instead of mocking PRAW itself."""
        reddit = self._ensure_client()
        posts = []
        for name in self._config.subreddits:
            for submission in reddit.subreddit(name).new(limit=self._config.max_posts_per_poll):
                text = f"{submission.title}\n{submission.selftext or ''}"
                score = self._engagement_score(submission.score, submission.num_comments)
                posts.append((text, score, submission.id))
        return posts

    def poll(self) -> list[SocialSignal]:
        try:
            posts = self._fetch_posts()
        except Exception:
            logger.exception("Reddit poll failed, will retry next poll")
            self._reddit = None  # self-heal: rebuild the client next time
            return []

        if not posts:
            return []

        per_token_scores: dict[str, list[float]] = defaultdict(list)
        per_token_excerpt: dict[str, str] = {}
        for text, engagement_score, _post_id in posts:
            for token_address in extract_token_addresses(text, resolve_cashtag=self._resolve_cashtag):
                per_token_scores[token_address].append(engagement_score)
                per_token_excerpt.setdefault(token_address, text[:200])

        now = time.time()
        cooldown_seconds = self._config.mention_cooldown_minutes * 60
        observed_at = datetime.now(timezone.utc)
        signals = []
        for token_address, scores in per_token_scores.items():
            if now - self._recently_signaled.get(token_address, 0) < cooldown_seconds:
                continue
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
                    observed_at=observed_at,
                )
            )
            self._recently_signaled[token_address] = now

        if signals:
            logger.info("reddit source emitted %d signal(s) from %d posts", len(signals), len(posts))
        return signals
