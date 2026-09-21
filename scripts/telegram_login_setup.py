"""One-time setup for the Telegram signal source.

Logs into your real Telegram account via Telegram's own official MTProto
client library (Telethon) -- the same API the real Telegram app uses, not
scraping. You'll be prompted here for your phone number, the login code
Telegram sends you, and your 2FA password if you have one set. Once done,
the resulting session is saved to disk so the bot can reuse it without
logging in again every time.

Needs a free api_id/api_hash first -- get one at https://my.telegram.org
(log in with your phone number > API development tools > create an app),
then set TELEGRAM_API_ID and TELEGRAM_API_HASH in your .env.

Run once (with your venv active):
    python scripts/telegram_login_setup.py

Re-run it any time the bot logs that its Telegram session has expired.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Running this file directly (`python scripts/telegram_login_setup.py`) puts
# scripts/ on sys.path, not the repo root, so the memecoin_trader package
# next to it can't be found without this.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from memecoin_trader.config import DATA_DIR, PROJECT_ROOT, TELEGRAM_SESSION_PATH  # noqa: E402


def main() -> int:
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        print("Telethon isn't installed. Run:")
        print("  pip install -r requirements.txt")
        return 1

    load_dotenv(PROJECT_ROOT / ".env")
    api_id = os.environ.get("TELEGRAM_API_ID")
    api_hash = os.environ.get("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        print("TELEGRAM_API_ID and/or TELEGRAM_API_HASH are not set in your .env.")
        print("Get a free api_id/api_hash at https://my.telegram.org")
        print("(log in with your phone number > API development tools > create an app)")
        print("then set them in .env and re-run this script.")
        return 1

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 70)
    print(" Logging into Telegram. You'll be prompted for your phone number,")
    print(" then the login code Telegram sends you, and your 2FA password if")
    print(" you have one set.")
    print("=" * 70)

    with TelegramClient(str(TELEGRAM_SESSION_PATH), int(api_id), api_hash) as client:
        me = client.get_me()
        print(f"\nLogged in as {me.first_name} (@{me.username or me.id}).")

    print(f"Session saved to {TELEGRAM_SESSION_PATH}.session.")
    print("The bot will use it from now on — set signals.telegram.enabled: true")
    print("and list the channels you want to watch under signals.telegram.channels")
    print("in config.yaml (if you haven't already), then restart it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
