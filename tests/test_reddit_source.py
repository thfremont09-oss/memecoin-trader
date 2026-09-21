import pytest

from memecoin_trader.config import RedditSignalConfig, Secrets
from memecoin_trader.signals.reddit_source import RedditSource

CONFIG = RedditSignalConfig(
    enabled=True,
    subreddits=["CryptoMoonShots"],
    max_posts_per_poll=25,
    mention_cooldown_minutes=30,
)

SECRETS = Secrets(
    twitter_bearer_token=None,
    reddit_client_id="id",
    reddit_client_secret="secret",
    reddit_user_agent="test-agent/1.0",
    birdeye_api_key=None,
    neynar_api_key=None,
    telegram_api_id=None,
    telegram_api_hash=None,
    solana_private_key=None,
    solana_rpc_url="https://api.mainnet-beta.solana.com",
    live_trading_confirmed=False,
)

NO_REDDIT_SECRETS = Secrets(
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


def make_source():
    return RedditSource(config=CONFIG, secrets=SECRETS, chain_id="solana")


def test_missing_credentials_raises():
    with pytest.raises(ValueError):
        RedditSource(config=CONFIG, secrets=NO_REDDIT_SECRETS, chain_id="solana")


def test_poll_extracts_and_scores_from_posts(monkeypatch):
    source = make_source()
    monkeypatch.setattr(
        source,
        "_fetch_posts",
        lambda: [
            (f"$MOON is pumping hard, CA {SOLANA_ADDR_A}", 50.0, "p1"),
            (f"everyone talking about {SOLANA_ADDR_A} today", 30.0, "p2"),
            (f"different coin {SOLANA_ADDR_B}", 20.0, "p3"),
        ],
    )

    signals = source.poll()

    by_token = {s.token_address: s for s in signals}
    assert set(by_token) == {SOLANA_ADDR_A, SOLANA_ADDR_B}
    assert by_token[SOLANA_ADDR_A].mention_count == 2
    assert by_token[SOLANA_ADDR_B].mention_count == 1
    assert by_token[SOLANA_ADDR_A].score > by_token[SOLANA_ADDR_B].score  # more mentions -> higher score
    assert all(s.source == "reddit" for s in signals)


def test_poll_respects_mention_cooldown(monkeypatch):
    source = make_source()
    monkeypatch.setattr(source, "_fetch_posts", lambda: [(f"hype {SOLANA_ADDR_A}", 40.0, "p1")])

    first = source.poll()
    assert len(first) == 1

    second = source.poll()
    assert second == []  # still within mention_cooldown_minutes


def test_fetch_failure_self_heals_instead_of_crashing(monkeypatch):
    source = make_source()
    source._reddit = "not-none-sentinel"  # pretend a client is already built

    def raise_error():
        raise RuntimeError("API down")

    monkeypatch.setattr(source, "_fetch_posts", raise_error)
    assert source.poll() == []
    assert source._reddit is None  # torn down so the next poll rebuilds the client


def test_no_posts_returns_no_signals(monkeypatch):
    source = make_source()
    monkeypatch.setattr(source, "_fetch_posts", lambda: [])
    assert source.poll() == []


def test_engagement_score_increases_with_upvotes_and_comments():
    low = RedditSource._engagement_score(upvotes=1, num_comments=0)
    high = RedditSource._engagement_score(upvotes=500, num_comments=200)
    assert 0 <= low < high <= 100
