from unittest.mock import MagicMock

import pytest
import requests

from memecoin_trader.config import FarcasterSignalConfig
from memecoin_trader.signals.farcaster_source import FarcasterSource

CONFIG = FarcasterSignalConfig(
    enabled=True,
    query="test query",
    max_results_per_poll=25,
    mention_cooldown_minutes=30,
)

SOLANA_ADDR_A = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
SOLANA_ADDR_B = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


def make_session(json_data=None, raise_exc=None):
    session = MagicMock()
    resp = MagicMock()
    if raise_exc:
        session.get.side_effect = raise_exc
    else:
        resp.raise_for_status = MagicMock()
        resp.json.return_value = json_data
        session.get.return_value = resp
    return session


def make_cast(text, likes=0, recasts=0, replies=0):
    return {
        "text": text,
        "reactions": {"likes_count": likes, "recasts_count": recasts},
        "replies": {"count": replies},
    }


def test_missing_api_key_raises():
    with pytest.raises(ValueError):
        FarcasterSource(config=CONFIG, api_key="", chain_id="solana")


def test_poll_extracts_and_scores_from_casts():
    session = make_session(
        {
            "result": {
                "casts": [
                    make_cast(f"$MOON is pumping, CA {SOLANA_ADDR_A}", likes=50, recasts=10),
                    make_cast(f"different coin {SOLANA_ADDR_B}", likes=1),
                ]
            }
        }
    )
    source = FarcasterSource(config=CONFIG, api_key="key", chain_id="solana", session=session)

    signals = source.poll()

    by_token = {s.token_address: s for s in signals}
    assert set(by_token) == {SOLANA_ADDR_A, SOLANA_ADDR_B}
    assert by_token[SOLANA_ADDR_A].score > by_token[SOLANA_ADDR_B].score
    assert all(s.source == "farcaster" for s in signals)


def test_request_failure_returns_empty_list():
    session = make_session(raise_exc=requests.RequestException("boom"))
    source = FarcasterSource(config=CONFIG, api_key="key", chain_id="solana", session=session)
    assert source.poll() == []


def test_unexpected_response_shape_returns_empty_list():
    session = make_session({"unexpected": "shape"})
    source = FarcasterSource(config=CONFIG, api_key="key", chain_id="solana", session=session)
    assert source.poll() == []


def test_no_casts_returns_no_signals():
    session = make_session({"result": {"casts": []}})
    source = FarcasterSource(config=CONFIG, api_key="key", chain_id="solana", session=session)
    assert source.poll() == []
