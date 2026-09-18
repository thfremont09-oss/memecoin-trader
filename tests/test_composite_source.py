from datetime import datetime, timezone

import pytest

from memecoin_trader.signals.base import SignalSource, SocialSignal
from memecoin_trader.signals.composite_source import CompositeSignalSource

NOW = datetime.now(timezone.utc)


def make_signal(source: str, token: str = "TokenAddr1111111111111111111111111111111") -> SocialSignal:
    return SocialSignal(
        token_address=token,
        symbol="TOK",
        chain_id="solana",
        source=source,
        score=80.0,
        mention_count=5,
        excerpt="hype",
        observed_at=NOW,
    )


class FakeSource(SignalSource):
    def __init__(self, name: str, signals=None, error: Exception | None = None):
        self.name = name
        self._signals = signals or []
        self._error = error
        self.polled = False

    def poll(self):
        self.polled = True
        if self._error:
            raise self._error
        return self._signals


def test_empty_sources_raises():
    with pytest.raises(ValueError):
        CompositeSignalSource([])


def test_merges_signals_from_all_sources():
    a = FakeSource("a", signals=[make_signal("a")])
    b = FakeSource("b", signals=[make_signal("b")])
    composite = CompositeSignalSource([a, b])

    signals = composite.poll()

    assert {s.source for s in signals} == {"a", "b"}
    assert composite.name == "a+b"


def test_one_source_failing_does_not_drop_the_others():
    good = FakeSource("good", signals=[make_signal("good")])
    bad = FakeSource("bad", error=RuntimeError("boom"))
    composite = CompositeSignalSource([bad, good])

    signals = composite.poll()

    assert good.polled and bad.polled
    assert len(signals) == 1
    assert signals[0].source == "good"
