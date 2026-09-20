from unittest.mock import MagicMock

import requests

from memecoin_trader.config import GeckoTerminalSignalConfig
from memecoin_trader.signals.geckoterminal_source import GeckoTerminalTrendingSource

CONFIG = GeckoTerminalSignalConfig(enabled=True, limit=20, mention_cooldown_minutes=30)

ADDR_A = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
ADDR_B = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


def make_session(json_data=None, raise_exc=None):
    session = MagicMock()
    if raise_exc:
        session.get.side_effect = raise_exc
    else:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = json_data
        session.get.return_value = resp
    return session


def pool(token_address, name="MOON / SOL"):
    return {
        "attributes": {"name": name},
        "relationships": {"base_token": {"data": {"id": f"solana_{token_address}"}}},
    }


def test_poll_emits_signals_scored_by_rank():
    session = make_session({"data": [pool(ADDR_A, "MOON / SOL"), pool(ADDR_B, "DOGE / SOL")]})
    source = GeckoTerminalTrendingSource(config=CONFIG, chain_id="solana", session=session)

    signals = source.poll()

    by_token = {s.token_address: s for s in signals}
    assert set(by_token) == {ADDR_A, ADDR_B}
    assert by_token[ADDR_A].score > by_token[ADDR_B].score
    assert by_token[ADDR_A].symbol == "MOON"
    assert all(s.source == "geckoterminal_trending" for s in signals)


def test_poll_respects_cooldown():
    session = make_session({"data": [pool(ADDR_A)]})
    source = GeckoTerminalTrendingSource(config=CONFIG, chain_id="solana", session=session)

    assert len(source.poll()) == 1
    assert source.poll() == []  # still within mention_cooldown_minutes


def test_request_failure_returns_empty_list():
    session = make_session(raise_exc=requests.RequestException("boom"))
    source = GeckoTerminalTrendingSource(config=CONFIG, chain_id="solana", session=session)

    assert source.poll() == []


def test_unexpected_response_shape_returns_empty_list():
    session = make_session({"unexpected": "shape"})
    source = GeckoTerminalTrendingSource(config=CONFIG, chain_id="solana", session=session)

    assert source.poll() == []


def test_pool_without_base_token_id_is_skipped():
    session = make_session({"data": [{"attributes": {"name": "??? / SOL"}, "relationships": {}}]})
    source = GeckoTerminalTrendingSource(config=CONFIG, chain_id="solana", session=session)

    assert source.poll() == []


def test_symbol_falls_back_to_token_prefix_when_name_has_no_slash():
    session = make_session({"data": [pool(ADDR_A, name="weird-name")]})
    source = GeckoTerminalTrendingSource(config=CONFIG, chain_id="solana", session=session)

    signals = source.poll()
    assert signals[0].symbol == ADDR_A[:8]
