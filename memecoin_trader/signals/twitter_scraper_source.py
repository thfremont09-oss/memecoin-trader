"""Real X (Twitter) signal source via browser automation — for when you
don't have (or don't want to pay for) official API access.

Logs into a session you create yourself once, interactively, via
`scripts/twitter_login_setup.py`, then reads live search results from that
authenticated browser session on each poll. Produces the same SocialSignal
shape as MockTwitterSource/TwitterAPISource, so nothing downstream (entry
strategy, ledger, engine) needs to change.

**This is not sanctioned by X and violates its Terms of Service** —
automating an account to read the site is against the rules no matter how
gently it's done. The real, concrete risk is that the X account whose
session is used here gets suspended if X's abuse detection flags it. Use a
throwaway account created just for this, never your main one. See the
README's "Real Twitter/X via browser scraping" section before enabling this
(`signals.scraper.enabled` in config.yaml).

Unverified against the live site from the sandbox this was built in (no
network access to x.com there) — X's page structure/selectors may have
drifted from what's assumed here by the time you run this for real. Watch
the logs the first few times.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote_plus

from memecoin_trader.config import ScraperSignalConfig
from memecoin_trader.market.dexscreener import DexScreenerClient
from memecoin_trader.signals.base import SignalSource, SocialSignal
from memecoin_trader.signals.text_extraction import extract_token_addresses

logger = logging.getLogger(__name__)

SEARCH_URL_TEMPLATE = "https://x.com/search?q={query}&src=typed_query&f=live"
TWEET_SELECTOR = 'article[data-testid="tweet"]'


class SessionExpiredError(RuntimeError):
    pass


class TwitterScraperSource(SignalSource):
    name = "twitter_scraper"

    def __init__(
        self,
        config: ScraperSignalConfig,
        session_path: Path,
        chain_id: str,
        market_client: DexScreenerClient | None = None,
    ):
        if not session_path.exists():
            raise FileNotFoundError(
                f"No saved X session at {session_path}. Run: python scripts/twitter_login_setup.py"
            )
        self._config = config
        self._session_path = session_path
        self._chain_id = chain_id
        self._market_client = market_client or DexScreenerClient()
        self._playwright = None
        self._browser = None
        self._context = None
        self._recently_signaled: dict[str, float] = {}
        self._warned_expired = False

    def _resolve_cashtag(self, symbol: str) -> str | None:
        results = self._market_client.search(symbol, chain_id=self._chain_id)
        if not results:
            return None
        best = max(results, key=lambda p: p.liquidity_usd)
        logger.info("resolved cashtag $%s -> %s via DexScreener search", symbol, best.token_address)
        return best.token_address

    def _ensure_browser(self) -> None:
        if self._context is not None:
            return
        from playwright.sync_api import sync_playwright

        from memecoin_trader.signals.browser_utils import (
            HIDE_WEBDRIVER_SCRIPT,
            LAUNCH_ARGS,
            new_context_kwargs,
        )

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(headless=self._config.headless, args=LAUNCH_ARGS)
        self._context = self._browser.new_context(
            storage_state=str(self._session_path), **new_context_kwargs()
        )
        self._context.add_init_script(HIDE_WEBDRIVER_SCRIPT)

    def _teardown_browser(self) -> None:
        for obj, method in ((self._context, "close"), (self._browser, "close"), (self._playwright, "stop")):
            if obj is None:
                continue
            try:
                getattr(obj, method)()
            except Exception:
                pass
        self._context = None
        self._browser = None
        self._playwright = None

    def _scrape_tweet_texts(self) -> list[str]:
        url = SEARCH_URL_TEMPLATE.format(query=quote_plus(self._config.search_query))
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            if "/login" in page.url or "/i/flow/login" in page.url:
                raise SessionExpiredError("redirected to login -- session expired or invalid")
            page.wait_for_selector(TWEET_SELECTOR, timeout=15000)
            articles = page.locator(TWEET_SELECTOR)
            count = min(articles.count(), self._config.max_tweets_per_poll)
            return [articles.nth(i).inner_text() for i in range(count)]
        finally:
            page.close()

    def poll(self) -> list[SocialSignal]:
        try:
            self._ensure_browser()
            texts = self._scrape_tweet_texts()
            self._warned_expired = False
        except SessionExpiredError:
            if not self._warned_expired:
                logger.error(
                    "X session expired or invalid -- re-run: python scripts/twitter_login_setup.py"
                )
                self._warned_expired = True
            self._teardown_browser()
            return []
        except Exception:
            logger.exception("browser scrape failed, will retry next poll")
            self._teardown_browser()  # self-heal: relaunch fresh next time rather than stay wedged
            return []

        now = time.time()
        per_token_texts: dict[str, list[str]] = defaultdict(list)
        for text in texts:
            for token_address in extract_token_addresses(text, resolve_cashtag=self._resolve_cashtag):
                per_token_texts[token_address].append(text)

        cooldown_seconds = self._config.mention_cooldown_minutes * 60
        observed_at = datetime.now(timezone.utc)
        signals = []
        for token_address, matches in per_token_texts.items():
            if now - self._recently_signaled.get(token_address, 0) < cooldown_seconds:
                continue
            mention_count = len(matches)
            # No engagement counts are scraped (X's markup for like/retweet
            # counts is one of the most brittle parts of the page and changes
            # often), so the score is mention-count-based rather than
            # engagement-weighted like the official API source.
            score = min(100.0, 40.0 + 10.0 * mention_count)
            signals.append(
                SocialSignal(
                    token_address=token_address,
                    symbol=token_address[:8],
                    chain_id=self._chain_id,
                    source=self.name,
                    score=round(score, 1),
                    mention_count=mention_count,
                    excerpt=matches[0][:200],
                    observed_at=observed_at,
                )
            )
            self._recently_signaled[token_address] = now

        if signals:
            logger.info("twitter scraper emitted %d signal(s) from %d tweets", len(signals), len(texts))
        return signals
