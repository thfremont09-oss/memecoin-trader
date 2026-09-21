"""Real Telegram signal source via Telegram's own official MTProto client
library (Telethon) -- not scraping. This is the same API the real Telegram
app itself uses, logged in as a real user account, reading public channels
you choose. Telegram's own channel search/messages are freely readable by
any user who joins them, so there's no ToS-violation risk here comparable
to the Twitter scraper.

Needs a free api_id/api_hash from https://my.telegram.org (self-serve, no
approval wait) and a one-time interactive login (phone number + code, and
2FA password if you have one set) via `scripts/telegram_login_setup.py`,
which saves a reusable session file to disk -- see README's "Telegram"
section.

Solana memecoin "gem call" / alpha channels are one of the most active
sources of this kind of hype, often earlier than Twitter/Reddit, so this
tends to be a high-signal (and high rug-risk) source -- everything
downstream (liquidity checks, RugCheck, the ML gate) still applies exactly
like it does to any other source.
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from memecoin_trader.config import Secrets, TelegramSignalConfig
from memecoin_trader.market.dexscreener import DexScreenerClient
from memecoin_trader.signals.base import SignalSource, SocialSignal
from memecoin_trader.signals.text_extraction import extract_token_addresses

logger = logging.getLogger(__name__)


class SessionExpiredError(RuntimeError):
    pass


class TelegramSource(SignalSource):
    name = "telegram"

    def __init__(
        self,
        config: TelegramSignalConfig,
        secrets: Secrets,
        session_path: Path,
        chain_id: str,
        market_client: DexScreenerClient | None = None,
    ):
        if not (secrets.telegram_api_id and secrets.telegram_api_hash):
            raise ValueError("TelegramSource requires TELEGRAM_API_ID and TELEGRAM_API_HASH")
        if not session_path.exists() and not Path(str(session_path) + ".session").exists():
            raise FileNotFoundError(
                f"No saved Telegram session at {session_path}. Run: python scripts/telegram_login_setup.py"
            )
        self._config = config
        self._secrets = secrets
        self._session_path = session_path
        self._chain_id = chain_id
        self._market_client = market_client or DexScreenerClient()
        self._client = None
        self._recently_signaled: dict[str, float] = {}

    def _resolve_cashtag(self, symbol: str) -> str | None:
        results = self._market_client.search(symbol, chain_id=self._chain_id)
        if not results:
            return None
        best = max(results, key=lambda p: p.liquidity_usd)
        logger.info("resolved cashtag $%s -> %s via DexScreener search", symbol, best.token_address)
        return best.token_address

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        from telethon.sync import TelegramClient

        client = TelegramClient(
            str(self._session_path), int(self._secrets.telegram_api_id), self._secrets.telegram_api_hash
        )
        client.connect()
        if not client.is_user_authorized():
            client.disconnect()
            raise SessionExpiredError(
                "Telegram session is not authorized -- run: python scripts/telegram_login_setup.py"
            )
        self._client = client
        return client

    @staticmethod
    def _engagement_score(views: int, forwards: int) -> float:
        import math

        weighted = max(views, 0) + 5 * max(forwards, 0)
        # log-scale so one viral post doesn't blow the score past 100
        return min(100.0, 10.0 * math.log1p(weighted))

    def _fetch_messages(self) -> list[tuple[str, float, str]]:
        """Returns (text, engagement_score, message_key) tuples for recent
        messages across every configured channel. Split out so tests can
        stub this one method instead of mocking Telethon itself."""
        client = self._ensure_client()
        messages = []
        for channel in self._config.channels:
            try:
                for msg in client.get_messages(channel, limit=self._config.max_messages_per_channel_per_poll):
                    if not msg or not msg.message:
                        continue
                    score = self._engagement_score(msg.views or 0, msg.forwards or 0)
                    messages.append((msg.message, score, f"{channel}:{msg.id}"))
            except Exception:
                logger.warning("Telegram channel %s fetch failed, skipping it this poll", channel)
        return messages

    def poll(self) -> list[SocialSignal]:
        try:
            messages = self._fetch_messages()
        except SessionExpiredError:
            logger.error("Telegram session expired or invalid -- re-run: python scripts/telegram_login_setup.py")
            self._client = None
            return []
        except Exception:
            logger.exception("Telegram poll failed, will retry next poll")
            self._client = None  # self-heal: rebuild the client next time
            return []

        if not messages:
            return []

        per_token_scores: dict[str, list[float]] = defaultdict(list)
        per_token_excerpt: dict[str, str] = {}
        for text, engagement_score, _msg_key in messages:
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
            logger.info("Telegram source emitted %d signal(s) from %d messages", len(signals), len(messages))
        return signals
