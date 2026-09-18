from unittest.mock import MagicMock

import pytest
import requests

from memecoin_trader.config import BirdeyeSignalConfig
from memecoin_trader.signals.birdeye_source import BirdeyeTrendingSource

CONFIG = BirdeyeSignalConfig(enabled=True, limit=20, mention_cooldown_minutes=30)

ADDR_A = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
ADDR_B = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


def make_session(json_data=None, status_code=200, raise_exc=None):
    session = MagicMock()
    resp = MagicMock()
    resp.status_code = status_code
    if raise_exc:
        session.get.side_effect = raise_exc
    else:
        resp.raise_for_status = MagicMock()
        resp.json.return_value = json_data
        session.get.return_value = resp
    return session


def test_missing_api_key_raises():
    with pytest.raises(ValueError):
        BirdeyeTrendingSource(config=CONFIG, api_key="", chain_id="solana")


def test_poll_emits_signals_scored_by_rank():
    session = make_session(
        {
            "data": {
                "tokens": [
                    {"address": ADDR_A, "symbol": "MOON", "rank": 1, "volume24hUSD": 500000},
                    {"address": ADDR_B, "symbol": "DOGE", "rank": 2, "volume24hUSD": 300000},
                ]
            }
        }
    )
    source = BirdeyeTrendingSource(config=CONFIG, api_key="key", chain_id="solana", session=session)

    signals = source.poll()

    by_token = {s.token_address: s for s in signals}
    assert set(by_token) == {ADDR_A, ADDR_B}
    assert by_token[ADDR_A].score > by_token[ADDR_B].score  # rank 1 scores higher than rank 2
    assert all(s.source == "birdeye_trending" for s in signals)


def test_poll_respects_cooldown():
    session = make_session({"data": {"tokens": [{"address": ADDR_A, "symbol": "MOON", "rank": 1}]}})
    source = BirdeyeTrendingSource(config=CONFIG, api_key="key", chain_id="solana", session=session)

    first = source.poll()
    assert len(first) == 1

    second = source.poll()
    assert second == []  # still within mention_cooldown_minutes


def test_request_failure_returns_empty_list():
    session = make_session(raise_exc=requests.RequestException("boom"))
    source = BirdeyeTrendingSource(config=CONFIG, api_key="key", chain_id="solana", session=session)

    assert source.poll() == []


def test_unexpected_response_shape_returns_empty_list():
    session = make_session({"unexpected": "shape"})
    source = BirdeyeTrendingSource(config=CONFIG, api_key="key", chain_id="solana", session=session)

    assert source.poll() == []


def test_token_without_address_is_skipped():
    session = make_session({"data": {"tokens": [{"symbol": "NOADDR", "rank": 1}]}})
    source = BirdeyeTrendingSource(config=CONFIG, api_key="key", chain_id="solana", session=session)

    assert source.poll() == []
