"""Common interface every "hype source" implements.

The engine only ever talks to this interface, so swapping the simulated
feed for the real Twitter/X adapter later is a one-line config change —
nothing downstream (entry strategy, ledger, dashboard) needs to know which
one is active.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SocialSignal:
    token_address: str
    symbol: str
    chain_id: str
    source: str  # e.g. "twitter_mock" or "twitter_api"
    score: float  # 0-100 hype/FOMO score
    mention_count: int
    excerpt: str  # example post text, for logging/dashboard transparency
    observed_at: datetime


class SignalSource(ABC):
    """Something that can be polled for new social hype about tokens."""

    name: str

    @abstractmethod
    def poll(self) -> list[SocialSignal]:
        """Return newly observed signals since the last poll. May be empty."""
        raise NotImplementedError
