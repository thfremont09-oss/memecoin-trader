import pytest

from memecoin_trader.config import ScraperSignalConfig
from memecoin_trader.signals.twitter_scraper_source import SessionExpiredError, TwitterScraperSource

CONFIG = ScraperSignalConfig(
    enabled=True,
    search_query="test query",
    headless=True,
    max_tweets_per_poll=25,
    mention_cooldown_minutes=30,
)

SOLANA_ADDR_A = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
SOLANA_ADDR_B = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"


def make_source(tmp_path, monkeypatch):
    session_path = tmp_path / "twitter_session.json"
    session_path.write_text("{}")
    source = TwitterScraperSource(config=CONFIG, session_path=session_path, chain_id="solana")
    monkeypatch.setattr(source, "_ensure_browser", lambda: None)
    return source


def test_missing_session_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        TwitterScraperSource(config=CONFIG, session_path=tmp_path / "nope.json", chain_id="solana")


def test_poll_extracts_and_scores_from_scraped_tweets(tmp_path, monkeypatch):
    source = make_source(tmp_path, monkeypatch)
    monkeypatch.setattr(
        source,
        "_scrape_tweet_texts",
        lambda: [
            f"$MOON is pumping hard, CA {SOLANA_ADDR_A}",
            f"everyone talking about {SOLANA_ADDR_A} today",
            f"different coin {SOLANA_ADDR_B}",
        ],
    )

    signals = source.poll()

    by_token = {s.token_address: s for s in signals}
    assert set(by_token) == {SOLANA_ADDR_A, SOLANA_ADDR_B}
    assert by_token[SOLANA_ADDR_A].mention_count == 2
    assert by_token[SOLANA_ADDR_B].mention_count == 1
    assert by_token[SOLANA_ADDR_A].score > by_token[SOLANA_ADDR_B].score  # more mentions -> higher score
    assert all(s.source == "twitter_scraper" for s in signals)
    assert all("[SIMULATED]" not in s.excerpt for s in signals)  # this source is real, not the mock


def test_poll_respects_mention_cooldown(tmp_path, monkeypatch):
    source = make_source(tmp_path, monkeypatch)
    monkeypatch.setattr(source, "_scrape_tweet_texts", lambda: [f"hype {SOLANA_ADDR_A}"])

    first = source.poll()
    assert len(first) == 1

    second = source.poll()
    assert second == []  # still within mention_cooldown_minutes


def test_session_expired_returns_empty_without_raising(tmp_path, monkeypatch):
    source = make_source(tmp_path, monkeypatch)

    def raise_expired():
        raise SessionExpiredError("boom")

    monkeypatch.setattr(source, "_scrape_tweet_texts", raise_expired)
    assert source.poll() == []


def test_generic_scrape_failure_self_heals_instead_of_crashing(tmp_path, monkeypatch):
    source = make_source(tmp_path, monkeypatch)
    source._context = "not-none-sentinel"  # pretend a browser context is already open

    def raise_error():
        raise RuntimeError("page crashed")

    monkeypatch.setattr(source, "_scrape_tweet_texts", raise_error)
    assert source.poll() == []
    assert source._context is None  # torn down so the next poll relaunches fresh


def test_no_tweets_returns_no_signals(tmp_path, monkeypatch):
    source = make_source(tmp_path, monkeypatch)
    monkeypatch.setattr(source, "_scrape_tweet_texts", lambda: [])
    assert source.poll() == []


def _launch_chromium_or_skip(playwright):
    import os

    kwargs = {}
    pinned = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    if os.path.exists(pinned):
        kwargs["executable_path"] = pinned
    try:
        return playwright.chromium.launch(headless=True, **kwargs)
    except Exception as exc:
        pytest.skip(f"Chromium not available in this environment: {exc}")


def test_tweet_selector_extracts_text_from_rendered_markup():
    """Validates the actual scraping mechanism (data-testid selector +
    inner_text) against markup shaped like X's real tweet structure, using a
    real (offline) Playwright page rather than mocking Playwright itself.
    Skips if no Chromium is installed (e.g. `playwright install` not run)."""
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    from memecoin_trader.signals.twitter_scraper_source import TWEET_SELECTOR

    html = f"""
    <html><body>
      <article data-testid="tweet">huge news, $MOON CA {SOLANA_ADDR_A} 🚀</article>
      <article data-testid="tweet">second tweet mentioning {SOLANA_ADDR_B}</article>
      <div data-testid="not-a-tweet">ignore me</div>
    </body></html>
    """

    with sync_playwright() as p:
        browser = _launch_chromium_or_skip(p)
        page = browser.new_page()
        page.set_content(html)
        articles = page.locator(TWEET_SELECTOR)
        texts = [articles.nth(i).inner_text() for i in range(articles.count())]
        browser.close()

    assert len(texts) == 2
    assert SOLANA_ADDR_A in texts[0]
    assert SOLANA_ADDR_B in texts[1]
