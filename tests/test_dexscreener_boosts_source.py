from unittest.mock import MagicMock

import pytest
import requests

from memecoin_trader.config import DexScreenerBoostsSignalConfig
from memecoin_trader.signals.dexscreener_boosts_source import DexScreenerBoostsSource

CONFIG = DexScreenerBoostsSignalConfig(enabled=True, limit=20, mention_cooldown_minutes=30)

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


def test_poll_emits_signals_scored_by_rank():
    session = make_session(
        [
            {"chainId": "solana", "tokenAddress": ADDR_A, "totalAmount": 1500},
            {"chainId": "solana", "tokenAddress": ADDR_B, "totalAmount": 800},
        ]
    )
    source = DexScreenerBoostsSource(config=CONFIG, chain_id="solana", session=session)

    signals = source.poll()

    by_token = {s.token_address: s for s in signals}
    assert set(by_token) == {ADDR_A, ADDR_B}
    assert by_token[ADDR_A].score > by_token[ADDR_B].score  # earlier in the list scores higher
    assert all(s.source == "dexscreener_boosts" for s in signals)


def test_poll_filters_to_configured_chain():
    session = make_session(
        [
            {"chainId": "ethereum", "tokenAddress": "0xnotsolana", "totalAmount": 5000},
            {"chainId": "solana", "tokenAddress": ADDR_A, "totalAmount": 100},
        ]
    )
    source = DexScreenerBoostsSource(config=CONFIG, chain_id="solana", session=session)

    signals = source.poll()

    assert [s.token_address for s in signals] == [ADDR_A]


def test_poll_respects_cooldown():
    session = make_session([{"chainId": "solana", "tokenAddress": ADDR_A, "totalAmount": 100}])
    source = DexScreenerBoostsSource(config=CONFIG, chain_id="solana", session=session)

    assert len(source.poll()) == 1
    assert source.poll() == []  # still within mention_cooldown_minutes


def test_request_failure_returns_empty_list():
    session = make_session(raise_exc=requests.RequestException("boom"))
    source = DexScreenerBoostsSource(config=CONFIG, chain_id="solana", session=session)

    assert source.poll() == []


def test_unexpected_response_shape_returns_empty_list():
    session = make_session({"unexpected": "shape"})
    source = DexScreenerBoostsSource(config=CONFIG, chain_id="solana", session=session)

    assert source.poll() == []


def test_item_without_token_address_is_skipped():
    session = make_session([{"chainId": "solana", "totalAmount": 100}])
    source = DexScreenerBoostsSource(config=CONFIG, chain_id="solana", session=session)

    assert source.poll() == []
