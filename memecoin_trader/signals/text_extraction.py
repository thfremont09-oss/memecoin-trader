"""Token-address/cashtag extraction shared by every real (non-simulated)
signal source — the X API adapter and the browser-scraper adapter both need
to pull the same things out of a blob of tweet text, so this is the one
place that logic lives.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Solana addresses are base58 (no 0, O, I, l), typically 32-44 chars.
SOLANA_ADDRESS_RE = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,44}\b")
CASHTAG_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,9})\b")


def extract_token_addresses(
    text: str,
    resolve_cashtag: Callable[[str], str | None] | None = None,
    max_cashtags: int = 2,
) -> list[str]:
    """Real Solana addresses in the text win outright; only falls back to
    resolving $CASHTAGs (via `resolve_cashtag`, e.g. a DexScreener search) if
    none are found, since a resolved cashtag is a much weaker signal than an
    address someone actually typed out.
    """
    addresses = SOLANA_ADDRESS_RE.findall(text)
    if addresses or resolve_cashtag is None:
        return addresses

    cashtags = CASHTAG_RE.findall(text)
    resolved = []
    for tag in cashtags[:max_cashtags]:  # avoid burning lookups on spammy multi-tag posts
        addr = resolve_cashtag(tag)
        if addr:
            resolved.append(addr)
    return resolved
