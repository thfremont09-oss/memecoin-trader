"""Unit tests for LiveExecutor's own logic (safety gates, decimals lookup,
cap enforcement), using a fake requests.Session so nothing here ever
touches a real Jupiter/Solana RPC endpoint or a real wallet."""
from decimal import Decimal
from unittest.mock import MagicMock

import base58
import pytest
from solders.keypair import Keypair

from memecoin_trader.config import LiveExecutionConfig, Secrets
from memecoin_trader.execution.live_executor import LiveExecutor, LiveTradingDisabledError
from tests.conftest import make_pair

CONFIG = LiveExecutionConfig(max_trade_usd=5.0, slippage_bps=100, priority_fee_lamports=200000)

# A throwaway, randomly generated keypair -- never funded, never used for
# anything but satisfying LiveExecutor's "is this a valid secret key" check.
_FAKE_SECRET_B58 = base58.b58encode(bytes(Keypair())).decode("ascii")


def make_secrets(**overrides) -> Secrets:
    defaults = dict(
        twitter_bearer_token=None,
        reddit_client_id=None,
        reddit_client_secret=None,
        reddit_user_agent="test-agent/1.0",
        birdeye_api_key=None,
        neynar_api_key=None,
        telegram_api_id=None,
        telegram_api_hash=None,
        solana_private_key=_FAKE_SECRET_B58,
        solana_rpc_url="https://example-rpc.invalid",
        live_trading_confirmed=True,
    )
    defaults.update(overrides)
    return Secrets(**defaults)


def make_executor(session=None) -> LiveExecutor:
    executor = LiveExecutor(CONFIG, make_secrets(), mode="live")
    if session is not None:
        executor._session = session
    return executor


def fake_response(json_data):
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = json_data
    return resp


def test_refuses_when_mode_is_not_live():
    with pytest.raises(LiveTradingDisabledError, match="mode is not"):
        LiveExecutor(CONFIG, make_secrets(), mode="paper")


def test_refuses_without_risk_confirmation():
    with pytest.raises(LiveTradingDisabledError, match="I_UNDERSTAND_LIVE_TRADING_RISK"):
        LiveExecutor(CONFIG, make_secrets(live_trading_confirmed=False), mode="live")


def test_refuses_without_a_private_key():
    with pytest.raises(LiveTradingDisabledError, match="SOLANA_PRIVATE_KEY"):
        LiveExecutor(CONFIG, make_secrets(solana_private_key=None), mode="live")


def test_get_mint_decimals_parses_the_real_value():
    session = MagicMock()
    session.post.return_value = fake_response({"result": {"value": {"decimals": 9, "amount": "123"}}})
    executor = make_executor(session)

    assert executor._get_mint_decimals("SOMEMINT") == 9


def test_get_mint_decimals_raises_on_rpc_error_instead_of_guessing():
    session = MagicMock()
    session.post.return_value = fake_response({"error": {"message": "not found"}})
    executor = make_executor(session)

    with pytest.raises(RuntimeError, match="not found"):
        executor._get_mint_decimals("SOMEMINT")


def test_sell_uses_the_mints_real_decimals_not_a_hardcoded_guess():
    # 9-decimal token (not the common 6) -- if sell() still hardcoded 6,
    # this would send an atomic amount 1000x too large to Jupiter.
    session = MagicMock()
    session.post.side_effect = [
        fake_response({"result": {"value": {"decimals": 9}}}),  # _get_mint_decimals
        fake_response({"swapTransaction": None}),  # would be used by _execute_swap if reached
    ]
    executor = make_executor(session)

    captured = {}

    def fake_get_quote(input_mint, output_mint, amount_atomic):
        captured["amount_atomic"] = amount_atomic
        return {"outAmount": "1000000000"}

    executor._get_quote = fake_get_quote
    executor._execute_swap = lambda quote: ("FAKE_TX_SIG", quote)
    executor._get_sol_price_usd = lambda: Decimal("150")

    market = make_pair(price_usd="0.01")  # 400 * $0.01 = $4, under the $5 cap
    executor.sell("SOMEMINT", Decimal("400"), market)

    assert captured["amount_atomic"] == 400 * 10**9  # not 400 * 10**6


def test_sell_enforces_the_live_cap_before_touching_the_network():
    executor = make_executor(MagicMock())
    executor._get_mint_decimals = MagicMock(side_effect=AssertionError("should never be called"))

    market = make_pair(price_usd="1.0")
    with pytest.raises(LiveTradingDisabledError, match="exceeds the configured live safety cap"):
        executor.sell("SOMEMINT", Decimal("1000"), market)  # $1000 worth, cap is $5
