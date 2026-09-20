"""4chan's /biz/ ("Business & Finance") board as a signal source, via
4chan's free, public, unauthenticated read-only JSON API
(https://github.com/4chan/4chan-API) -- no key, no login, no approval
process. /biz/ is a long-standing origin point for early shitcoin/memecoin
chatter, arguably predating crypto Twitter for this specific purpose.

**This is deliberately wired in as corroboration-only, never a standalone
buy trigger.** /biz/ is anonymous and frequently adversarial: posting a
contract address as a joke, or to bait newcomers into buying something
about to be dumped on, is common there. LAUNCH_SIGNAL_SCORE and the
scoring below are capped low enough that even after
entry.corroboration_bonus_score is added (see effective_score() in
entry_strategy.py), a token /biz/ alone has flagged still can't clear a
sane entry.mention_score_threshold -- it can only add to the "how many
distinct sources have flagged this token" count that makes a *real*
source's signal on the same token stronger. If mention_score_threshold is
ever tuned dramatically lower than its current default, revisit this cap.

4chan enforces a hard rate limit of 1 request/second per client; this
source's poll cadence (governed by entry.signal_poll_interval_seconds,
tens of seconds) is nowhere near that.
"""
from __future__ import annotations

import html
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone

import requests

from memecoin_trader.config import FourChanSignalConfig
from memecoin_trader.signals.base import SignalSource, SocialSignal
from memecoin_trader.signals.text_extraction import extract_token_addresses

logger = logging.getLogger(__name__)

CATALOG_URL_TEMPLATE = "https://a.4cdn.org/{board}/catalog.json"
# Deliberately low: see the module docstring for why this must never let a
# /biz/-only signal clear a normal mention_score_threshold on its own, even
# with the corroboration bonus applied.
MAX_SIGNAL_SCORE = 25.0
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(raw: str) -> str:
    text = raw.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = _HTML_TAG_RE.sub("", text)
    return html.unescape(text)


class FourChanBizSource(SignalSource):
    name = "fourchan_biz"

    def __init__(self, config: FourChanSignalConfig, chain_id: str, session: requests.Session | None = None):
        self._config = config
        self._chain_id = chain_id
        self._session = session or requests.Session()

    def _fetch_thread_texts(self, board: str) -> list[str]:
        url = CATALOG_URL_TEMPLATE.format(board=board)
        resp = self._session.get(url, timeout=15)
        resp.raise_for_status()
        pages = resp.json()

        texts = []
        for page in pages:
            for thread in page.get("threads", []):
                combined = " ".join(
                    _strip_html(thread[field]) for field in ("sub", "com") if thread.get(field)
                )
                if combined:
                    texts.append(combined)
                if len(texts) >= self._config.max_threads_per_poll:
                    return texts
        return texts

    def poll(self) -> list[SocialSignal]:
        all_texts: list[str] = []
        for board in self._config.boards:
            try:
                all_texts.extend(self._fetch_thread_texts(board))
            except (requests.RequestException, ValueError, KeyError) as exc:
                logger.warning("4chan /%s/ catalog request failed: %s", board, exc)

        if not all_texts:
            return []

        per_token_texts: dict[str, list[str]] = defaultdict(list)
        for text in all_texts:
            for token_address in extract_token_addresses(text):
                per_token_texts[token_address].append(text)

        now = datetime.now(timezone.utc)
        signals = []
        for token_address, matches in per_token_texts.items():
            mention_count = len(matches)
            score = min(MAX_SIGNAL_SCORE, 8.0 + 3.0 * mention_count)
            signals.append(
                SocialSignal(
                    token_address=token_address,
                    symbol=token_address[:8],
                    chain_id=self._chain_id,
                    source=self.name,
                    score=round(score, 1),
                    mention_count=mention_count,
                    excerpt=matches[0][:200],
                    observed_at=now,
                )
            )

        if signals:
            logger.info("4chan /biz/ source emitted %d signal(s) from %d threads", len(signals), len(all_texts))
        return signals
