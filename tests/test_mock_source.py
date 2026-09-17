from unittest.mock import MagicMock

from memecoin_trader.config import MockSignalConfig
from memecoin_trader.signals.mock_source import MockTwitterSource

CONFIG = MockSignalConfig(trending_refresh_minutes=10, max_signals_per_poll=3)


def test_poll_returns_empty_when_no_trending_pool_available():
    client = MagicMock()
    client.get_latest_boosted_tokens.return_value = []
    source = MockTwitterSource(CONFIG, chain_id="solana", client=client)
    assert source.poll() == []


def test_signals_reference_real_pool_token_addresses(monkeypatch):
    client = MagicMock()
    client.get_latest_boosted_tokens.return_value = [
        {"tokenAddress": "TOKEN_A", "description": "coin a"},
        {"tokenAddress": "TOKEN_B", "description": "coin b"},
    ]
    source = MockTwitterSource(CONFIG, chain_id="solana", client=client)

    monkeypatch.setattr("random.random", lambda: 0.0)  # force the "emit signals" branch
    monkeypatch.setattr("random.sample", lambda pool, k: pool[:k])

    signals = source.poll()
    assert len(signals) > 0
    addresses = {s.token_address for s in signals}
    assert addresses <= {"TOKEN_A", "TOKEN_B"}
    for s in signals:
        assert s.source == "twitter_mock"
        assert "[SIMULATED]" in s.excerpt
        assert 0 <= s.score <= 100


def test_recently_signaled_tokens_are_excluded(monkeypatch):
    client = MagicMock()
    client.get_latest_boosted_tokens.return_value = [{"tokenAddress": "TOKEN_A", "description": "coin a"}]
    source = MockTwitterSource(CONFIG, chain_id="solana", client=client)

    monkeypatch.setattr("random.random", lambda: 0.0)
    monkeypatch.setattr("random.sample", lambda pool, k: pool[:k])

    first = source.poll()
    assert len(first) == 1

    second = source.poll()
    assert second == []  # TOKEN_A is on its own internal cooldown now
