"""One-time setup for the browser-based Twitter/X signal source.

Opens a REAL, visible browser window to x.com/login. You log in yourself —
type your username/password, solve any CAPTCHA, complete 2FA — exactly like
you would in your everyday browser. This script never sees or stores your
password; once you confirm you're logged in, it just saves the resulting
session (cookies/local storage) to disk so the bot can reuse it without
logging in again every time.

Use a THROWAWAY X account for this, not your main one — see the README's
"Real Twitter/X via browser scraping" section for why.

Run once (with your venv active):
    python scripts/twitter_login_setup.py

Re-run it any time the bot logs that its session has expired or been logged
out.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Running this file directly (`python scripts/twitter_login_setup.py`) puts
# scripts/ on sys.path, not the repo root, so the memecoin_trader package
# next to it can't be found without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from memecoin_trader.config import DATA_DIR  # noqa: E402

SESSION_PATH = DATA_DIR / "twitter_session.json"


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright isn't installed. Run:")
        print("  pip install -r requirements-scraper.txt")
        print("  playwright install chromium")
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=False)
        except Exception as exc:
            print(f"Couldn't launch a browser: {exc}")
            print("Run: playwright install chromium")
            return 1

        context = browser.new_context()
        page = context.new_page()
        page.goto("https://x.com/login")

        print()
        print("=" * 70)
        print(" A browser window just opened.")
        print(" Log into your THROWAWAY X account there — type your")
        print(" username/password, solve any CAPTCHA, complete 2FA, whatever")
        print(" X asks for. This script does none of that for you.")
        print("=" * 70)
        input(" Once you can see your home timeline, press Enter here... ")

        context.storage_state(path=str(SESSION_PATH))
        browser.close()

    print(f"\nSession saved to {SESSION_PATH}.")
    print("The bot will use it from now on — set signals.scraper.enabled: true")
    print("in config.yaml (if you haven't already) and restart it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
