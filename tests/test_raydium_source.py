from unittest.mock import MagicMock

import requests

from memecoin_trader.config import RaydiumSignalConfig
from memecoin_trader.signals.raydium_source import RaydiumPoolsSource

CONFIG = RaydiumSignalConfig(enabled=True, limit=20, mention_cooldown_minutes=30)

SOL_MINT = "So11111111111111111111111111111111111111112"
TOKEN_A = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
TOKEN_B = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


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


def pool(token_address, token_symbol="MOON", quote_mint=SOL_MINT, quote_symbol="SOL"):
    return {
        "mintA": {"address": quote_mint, "symbol": quote_symbol},
        "mintB": {"address": token_address, "symbol": token_symbol},
    }


def test_poll_emits_signals_scored_by_rank():
    session = make_session({"data": {"data": [pool(TOKEN_A, "MOON"), pool(TOKEN_B, "DOGE")]}})
    source = RaydiumPoolsSource(config=CONFIG, chain_id="solana", session=session)

    signals = source.poll()

    by_token = {s.token_address: s for s in signals}
    assert set(by_token) == {TOKEN_A, TOKEN_B}
    assert by_token[TOKEN_A].score > by_token[TOKEN_B].score
    assert by_token[TOKEN_A].symbol == "MOON"
    assert all(s.source == "raydium_pools" for s in signals)


def test_picks_the_non_quote_side_of_the_pool():
    # token is mintA here, SOL is mintB -- should still pick the token, not SOL
    session = make_session({"data": {"data": [{"mintA": {"address": TOKEN_A, "symbol": "MOON"}, "mintB": {"address": SOL_MINT, "symbol": "SOL"}}]}})
    source = RaydiumPoolsSource(config=CONFIG, chain_id="solana", session=session)

    signals = source.poll()
    assert signals[0].token_address == TOKEN_A
    assert signals[0].symbol == "MOON"


def test_poll_respects_cooldown():
    session = make_session({"data": {"data": [pool(TOKEN_A)]}})
    source = RaydiumPoolsSource(config=CONFIG, chain_id="solana", session=session)

    assert len(source.poll()) == 1
    assert source.poll() == []  # still within mention_cooldown_minutes


def test_request_failure_returns_empty_list():
    session = make_session(raise_exc=requests.RequestException("boom"))
    source = RaydiumPoolsSource(config=CONFIG, chain_id="solana", session=session)

    assert source.poll() == []


def test_unexpected_response_shape_returns_empty_list():
    session = make_session({"unexpected": "shape"})
    source = RaydiumPoolsSource(config=CONFIG, chain_id="solana", session=session)

    assert source.poll() == []


def test_pool_with_no_usable_mint_is_skipped():
    session = make_session({"data": {"data": [{"mintA": {}, "mintB": {}}]}})
    source = RaydiumPoolsSource(config=CONFIG, chain_id="solana", session=session)

    assert source.poll() == []
