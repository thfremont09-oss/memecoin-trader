import pytest

from memecoin_trader.config import Secrets, TelegramSignalConfig
from memecoin_trader.signals.telegram_source import TelegramSource

CONFIG = TelegramSignalConfig(
    enabled=True,
    channels=["solanamemecoincalls"],
    max_messages_per_channel_per_poll=20,
    mention_cooldown_minutes=30,
)

SECRETS = Secrets(
    twitter_bearer_token=None,
    reddit_client_id=None,
    reddit_client_secret=None,
    reddit_user_agent="test-agent/1.0",
    birdeye_api_key=None,
    neynar_api_key=None,
    telegram_api_id="123456",
    telegram_api_hash="hash",
    solana_private_key=None,
    solana_rpc_url="https://api.mainnet-beta.solana.com",
    live_trading_confirmed=False,
)

NO_TELEGRAM_SECRETS = Secrets(
    twitter_bearer_token=None,
    reddit_client_id=None,
    reddit_client_secret=None,
    reddit_user_agent="test-agent/1.0",
    birdeye_api_key=None,
    neynar_api_key=None,
    telegram_api_id=None,
    telegram_api_hash=None,
    solana_private_key=None,
    solana_rpc_url="https://api.mainnet-beta.solana.com",
    live_trading_confirmed=False,
)

SOLANA_ADDR_A = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
SOLANA_ADDR_B = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


def make_source(tmp_path):
    session_path = tmp_path / "telegram_session"
    (tmp_path / "telegram_session.session").write_text("fake session")
    return TelegramSource(config=CONFIG, secrets=SECRETS, session_path=session_path, chain_id="solana")


def test_missing_credentials_raises(tmp_path):
    session_path = tmp_path / "telegram_session"
    (tmp_path / "telegram_session.session").write_text("fake session")
    with pytest.raises(ValueError):
        TelegramSource(config=CONFIG, secrets=NO_TELEGRAM_SECRETS, session_path=session_path, chain_id="solana")


def test_missing_session_file_raises(tmp_path):
    session_path = tmp_path / "telegram_session"
    with pytest.raises(FileNotFoundError):
        TelegramSource(config=CONFIG, secrets=SECRETS, session_path=session_path, chain_id="solana")


def test_poll_extracts_and_scores_from_messages(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    monkeypatch.setattr(
        source,
        "_fetch_messages",
        lambda: [
            (f"$MOON is pumping hard, CA {SOLANA_ADDR_A}", 50.0, "c:1"),
            (f"everyone talking about {SOLANA_ADDR_A} today", 30.0, "c:2"),
            (f"different coin {SOLANA_ADDR_B}", 20.0, "c:3"),
        ],
    )

    signals = source.poll()

    by_token = {s.token_address: s for s in signals}
    assert set(by_token) == {SOLANA_ADDR_A, SOLANA_ADDR_B}
    assert by_token[SOLANA_ADDR_A].mention_count == 2
    assert by_token[SOLANA_ADDR_B].mention_count == 1
    assert by_token[SOLANA_ADDR_A].score > by_token[SOLANA_ADDR_B].score  # more mentions -> higher score
    assert all(s.source == "telegram" for s in signals)


def test_poll_respects_mention_cooldown(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    monkeypatch.setattr(source, "_fetch_messages", lambda: [(f"hype {SOLANA_ADDR_A}", 40.0, "c:1")])

    first = source.poll()
    assert len(first) == 1

    second = source.poll()
    assert second == []  # still within mention_cooldown_minutes


def test_fetch_failure_self_heals_instead_of_crashing(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    source._client = "not-none-sentinel"  # pretend a client is already built

    def raise_error():
        raise RuntimeError("connection down")

    monkeypatch.setattr(source, "_fetch_messages", raise_error)
    assert source.poll() == []
    assert source._client is None  # torn down so the next poll rebuilds the client


def test_no_messages_returns_no_signals(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    monkeypatch.setattr(source, "_fetch_messages", lambda: [])
    assert source.poll() == []


def test_engagement_score_increases_with_views_and_forwards():
    low = TelegramSource._engagement_score(views=1, forwards=0)
    high = TelegramSource._engagement_score(views=5000, forwards=200)
    assert 0 <= low < high <= 100
