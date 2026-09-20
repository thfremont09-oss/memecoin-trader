from unittest.mock import MagicMock

import requests

from memecoin_trader.config import BlueskySignalConfig
from memecoin_trader.signals.bluesky_source import BlueskySource

CONFIG = BlueskySignalConfig(
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


def make_post(text, likes=0, reposts=0, replies=0):
    return {
        "record": {"text": text},
        "likeCount": likes,
        "repostCount": reposts,
        "replyCount": replies,
    }


def test_poll_extracts_and_scores_from_posts():
    session = make_session(
        {
            "posts": [
                make_post(f"$MOON is pumping, CA {SOLANA_ADDR_A}", likes=50, reposts=10),
                make_post(f"different coin {SOLANA_ADDR_B}", likes=1),
            ]
        }
    )
    source = BlueskySource(config=CONFIG, chain_id="solana", session=session)

    signals = source.poll()

    by_token = {s.token_address: s for s in signals}
    assert set(by_token) == {SOLANA_ADDR_A, SOLANA_ADDR_B}
    assert by_token[SOLANA_ADDR_A].score > by_token[SOLANA_ADDR_B].score
    assert all(s.source == "bluesky" for s in signals)


def test_request_failure_returns_empty_list():
    session = make_session(raise_exc=requests.RequestException("boom"))
    source = BlueskySource(config=CONFIG, chain_id="solana", session=session)
    assert source.poll() == []


def test_unexpected_response_shape_returns_empty_list():
    session = make_session({"unexpected": "shape"})
    source = BlueskySource(config=CONFIG, chain_id="solana", session=session)
    assert source.poll() == []


def test_no_posts_returns_no_signals():
    session = make_session({"posts": []})
    source = BlueskySource(config=CONFIG, chain_id="solana", session=session)
    assert source.poll() == []
