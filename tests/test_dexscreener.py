"""Unit tests for DexScreenerClient's own HTTP/retry/cache behavior, using a
fake requests.Session (dependency-injected via the `session` param) rather
than hitting the real API -- everything else in this codebase tests this
client indirectly through fakes/monkeypatches instead."""
from decimal import Decimal

import requests

from memecoin_trader.market.dexscreener import DexScreenerClient, QUICK_CACHE_TTL_SECONDS


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    """Returns each of `responses` in order (repeating the last one if
    called more times than provided), or always raises if `fail=True`."""

    def __init__(self, responses=None, fail=False):
        self.calls = 0
        self._responses = responses or []
        self._fail = fail

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        if self._fail:
            raise requests.exceptions.ConnectionError("no network")
        return self._responses[min(self.calls - 1, len(self._responses) - 1)]


def _pair_payload(token_address="TOKEN1", price="1.0", chain_id="solana"):
    return {
        "pairs": [
            {
                "chainId": chain_id,
                "dexId": "raydium",
                "pairAddress": "PAIR1",
                "baseToken": {"address": token_address, "symbol": "MEME", "name": "Meme"},
                "priceUsd": price,
                "liquidity": {"usd": "10000"},
                "volume": {"h24": "50000"},
                "priceChange": {"m5": "0", "h1": "0", "h24": "0"},
                "txns": {"h24": {"buys": 10, "sells": 5}},
                "pairCreatedAt": None,
                "fdv": "1000000",
                "url": "https://dexscreener.com/solana/pair1",
            }
        ]
    }


def test_quick_mode_uses_a_short_cache_within_ttl():
    session = FakeSession(responses=[FakeResponse(_pair_payload(price="1.0"))])
    client = DexScreenerClient(session=session)

    first = client.get_best_pair_for_token("solana", "TOKEN1", quick=True)
    second = client.get_best_pair_for_token("solana", "TOKEN1", quick=True)

    assert first is not None
    assert first.price_usd == Decimal("1.0")
    assert second is first  # served from cache, not a second HTTP call
    assert session.calls == 1


def test_quick_mode_refetches_after_the_cache_expires():
    session = FakeSession(
        responses=[FakeResponse(_pair_payload(price="1.0")), FakeResponse(_pair_payload(price="2.0"))]
    )
    client = DexScreenerClient(session=session)

    client.get_best_pair_for_token("solana", "TOKEN1", quick=True)
    cache_key = ("solana", "TOKEN1")
    aged_at, cached_pair = client._quick_cache[cache_key]
    client._quick_cache[cache_key] = (aged_at - QUICK_CACHE_TTL_SECONDS - 1, cached_pair)

    second = client.get_best_pair_for_token("solana", "TOKEN1", quick=True)

    assert second.price_usd == Decimal("2.0")
    assert session.calls == 2


def test_quick_mode_does_not_retry_on_failure():
    session = FakeSession(fail=True)
    client = DexScreenerClient(session=session)

    result = client.get_best_pair_for_token("solana", "TOKEN1", quick=True)

    assert result is None
    assert session.calls == 1  # no retries, unlike the default (non-quick) path


def test_quick_mode_caches_a_failure_too():
    session = FakeSession(fail=True)
    client = DexScreenerClient(session=session)

    client.get_best_pair_for_token("solana", "TOKEN1", quick=True)
    client.get_best_pair_for_token("solana", "TOKEN1", quick=True)

    assert session.calls == 1  # the "nothing found" result is cached too


def test_non_quick_mode_still_retries_on_failure(monkeypatch):
    session = FakeSession(fail=True)
    client = DexScreenerClient(session=session)
    monkeypatch.setattr("memecoin_trader.market.dexscreener.time.sleep", lambda _: None)

    result = client.get_best_pair_for_token("solana", "TOKEN1")

    assert result is None
    assert session.calls == 3  # MAX_RETRIES=2 -> 3 total attempts, unlike quick mode's 1


def test_non_quick_mode_is_not_affected_by_the_quick_cache():
    session = FakeSession(
        responses=[FakeResponse(_pair_payload(price="1.0")), FakeResponse(_pair_payload(price="2.0"))]
    )
    client = DexScreenerClient(session=session)

    first = client.get_best_pair_for_token("solana", "TOKEN1")
    second = client.get_best_pair_for_token("solana", "TOKEN1")

    assert first.price_usd == Decimal("1.0")
    assert second.price_usd == Decimal("2.0")
    assert session.calls == 2  # every normal-mode call is a real fetch
