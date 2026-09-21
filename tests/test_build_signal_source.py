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


def _disable_additive_sources(settings):
    """Reddit/Birdeye/Farcaster are skip-with-warning without credentials
    already, but Bluesky, 4chan, DexScreener boosts, GeckoTerminal, and
    Raydium need no credentials at all and are on by default -- explicitly
    disabling every additive source keeps these tests isolated to just the
    one interaction each is actually testing."""
    return dataclasses.replace(
        settings,
        reddit_signal=dataclasses.replace(settings.reddit_signal, enabled=False),
        pumpfun_signal=dataclasses.replace(settings.pumpfun_signal, enabled=False),
        birdeye_signal=dataclasses.replace(settings.birdeye_signal, enabled=False),
        raydium_signal=dataclasses.replace(settings.raydium_signal, enabled=False),
        dexscreener_boosts_signal=dataclasses.replace(settings.dexscreener_boosts_signal, enabled=False),
        geckoterminal_signal=dataclasses.replace(settings.geckoterminal_signal, enabled=False),
        bluesky_signal=dataclasses.replace(settings.bluesky_signal, enabled=False),
        farcaster_signal=dataclasses.replace(settings.farcaster_signal, enabled=False),
        fourchan_signal=dataclasses.replace(settings.fourchan_signal, enabled=False),
    )


def test_defaults_to_mock_only_with_no_real_source_configured():
    settings = _disable_additive_sources(load_settings())
    market = DexScreenerClient()

    source = build_signal_source(settings, market)

    assert isinstance(source, MockTwitterSource)


def test_mock_runs_alongside_real_source_when_enabled():
    settings = _disable_additive_sources(_with_mock_alongside_real(_with_bearer_token(load_settings()), True))
    market = DexScreenerClient()

    source = build_signal_source(settings, market)

    assert isinstance(source, CompositeSignalSource)
    assert source.name == "twitter_api+twitter_mock"


def test_mock_does_not_run_alongside_real_source_when_disabled():
    settings = _disable_additive_sources(_with_mock_alongside_real(_with_bearer_token(load_settings()), False))
    market = DexScreenerClient()

    source = build_signal_source(settings, market)

    assert source.name == "twitter_api"


def test_bluesky_and_fourchan_are_on_by_default_needing_no_credentials():
    settings = load_settings()
    market = DexScreenerClient()

    source = build_signal_source(settings, market)

    assert isinstance(source, CompositeSignalSource)
    assert "bluesky" in source.name
    assert "fourchan_biz" in source.name


def test_dexscreener_boosts_and_geckoterminal_are_on_by_default_needing_no_credentials():
    settings = load_settings()
    market = DexScreenerClient()

    source = build_signal_source(settings, market)

    assert isinstance(source, CompositeSignalSource)
    assert "dexscreener_boosts" in source.name
    assert "geckoterminal_trending" in source.name


def test_raydium_is_on_by_default_needing_no_credentials():
    settings = load_settings()
    market = DexScreenerClient()

    source = build_signal_source(settings, market)

    assert isinstance(source, CompositeSignalSource)
    assert "raydium_pools" in source.name


def test_farcaster_is_skipped_without_an_api_key():
    settings = load_settings()
    market = DexScreenerClient()

    source = build_signal_source(settings, market)

    names = source.name if isinstance(source, CompositeSignalSource) else source.name
    assert "farcaster" not in names


def test_farcaster_is_added_once_credentialed():
    settings = load_settings()
    settings = dataclasses.replace(settings, secrets=dataclasses.replace(settings.secrets, neynar_api_key="fake-key"))
    market = DexScreenerClient()

    source = build_signal_source(settings, market)

    assert "farcaster" in source.name


def test_telegram_is_off_by_default():
    settings = load_settings()
    market = DexScreenerClient()

    source = build_signal_source(settings, market)

    names = source.name if isinstance(source, CompositeSignalSource) else source.name
    assert "telegram" not in names


def test_telegram_is_skipped_without_credentials_even_if_enabled(tmp_path, monkeypatch):
    settings = load_settings()
    settings = dataclasses.replace(settings, telegram_signal=dataclasses.replace(settings.telegram_signal, enabled=True))
    session_path = tmp_path / "telegram_session"
    (tmp_path / "telegram_session.session").write_text("fake session")
    monkeypatch.setattr("memecoin_trader.engine.TELEGRAM_SESSION_PATH", session_path)
    market = DexScreenerClient()

    source = build_signal_source(settings, market)

    names = source.name if isinstance(source, CompositeSignalSource) else source.name
    assert "telegram" not in names


def test_telegram_is_skipped_without_a_session_even_if_credentialed(tmp_path, monkeypatch):
    settings = load_settings()
    settings = dataclasses.replace(settings, telegram_signal=dataclasses.replace(settings.telegram_signal, enabled=True))
    settings = dataclasses.replace(
        settings,
        secrets=dataclasses.replace(settings.secrets, telegram_api_id="123456", telegram_api_hash="hash"),
    )
    monkeypatch.setattr("memecoin_trader.engine.TELEGRAM_SESSION_PATH", tmp_path / "telegram_session")  # no .session file created
    market = DexScreenerClient()

    source = build_signal_source(settings, market)

    names = source.name if isinstance(source, CompositeSignalSource) else source.name
    assert "telegram" not in names


def test_telegram_is_added_once_credentialed_and_logged_in(tmp_path, monkeypatch):
    settings = load_settings()
    settings = dataclasses.replace(settings, telegram_signal=dataclasses.replace(settings.telegram_signal, enabled=True))
    settings = dataclasses.replace(
        settings,
        secrets=dataclasses.replace(settings.secrets, telegram_api_id="123456", telegram_api_hash="hash"),
    )
    session_path = tmp_path / "telegram_session"
    (tmp_path / "telegram_session.session").write_text("fake session")
    monkeypatch.setattr("memecoin_trader.engine.TELEGRAM_SESSION_PATH", session_path)
    market = DexScreenerClient()

    source = build_signal_source(settings, market)

    assert "telegram" in source.name
