import dataclasses

from memecoin_trader.config import load_settings
from memecoin_trader.engine import build_signal_source
from memecoin_trader.market.dexscreener import DexScreenerClient
from memecoin_trader.signals.composite_source import CompositeSignalSource
from memecoin_trader.signals.mock_source import MockTwitterSource


def _with_bearer_token(settings):
    return dataclasses.replace(settings, secrets=dataclasses.replace(settings.secrets, twitter_bearer_token="fake-token"))


def _with_mock_alongside_real(settings, run_alongside_real: bool):
    return dataclasses.replace(settings, mock_signal=dataclasses.replace(settings.mock_signal, run_alongside_real=run_alongside_real))


def test_defaults_to_mock_only_with_no_real_source_configured():
    settings = load_settings()
    market = DexScreenerClient()

    source = build_signal_source(settings, market)

    assert isinstance(source, MockTwitterSource)


def test_mock_runs_alongside_real_source_when_enabled():
    settings = _with_mock_alongside_real(_with_bearer_token(load_settings()), True)
    market = DexScreenerClient()

    source = build_signal_source(settings, market)

    assert isinstance(source, CompositeSignalSource)
    assert source.name == "twitter_api+twitter_mock"


def test_mock_does_not_run_alongside_real_source_when_disabled():
    settings = _with_mock_alongside_real(_with_bearer_token(load_settings()), False)
    market = DexScreenerClient()

    source = build_signal_source(settings, market)

    assert source.name == "twitter_api"
