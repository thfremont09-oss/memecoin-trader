"""Merges several signal sources into one, so the engine (which only ever
talks to a single SignalSource) can poll Twitter and Reddit for the same
tick without knowing either exists.

Each underlying source's poll() is isolated: one source erroring or being
slow to fail doesn't drop signals from the others.
"""
from __future__ import annotations

import logging

from memecoin_trader.signals.base import SignalSource, SocialSignal

logger = logging.getLogger(__name__)


class CompositeSignalSource(SignalSource):
    name = "composite"

    def __init__(self, sources: list[SignalSource]):
        if not sources:
            raise ValueError("CompositeSignalSource requires at least one source")
        self._sources = sources
        self.name = "+".join(s.name for s in sources)

    def poll(self) -> list[SocialSignal]:
        signals: list[SocialSignal] = []
        for source in self._sources:
            try:
                signals.extend(source.poll())
            except Exception:
                logger.exception("signal source %s failed, skipping it this poll", source.name)
        return signals
