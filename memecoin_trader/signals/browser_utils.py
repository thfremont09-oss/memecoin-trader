"""Shared browser configuration for the Twitter/X scraper source and its
login setup script, so both present the same fingerprint.

This intentionally stops at "don't trivially announce yourself as
automated" (a disabled Blink flag, a realistic UA/viewport, hiding the one
JS property Playwright sets by default) — not a full anti-detection suite
(no fingerprint spoofing, no proxy rotation, no CAPTCHA solving). X's
automation detection is still likely to catch a script driving a browser
sooner or later; this just avoids failing at the most trivial possible
check.
"""
from __future__ import annotations

LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

VIEWPORT = {"width": 1280, "height": 800}

# Playwright sets navigator.webdriver = true by default; this is one of the
# very first things naive bot-detection checks for.
HIDE_WEBDRIVER_SCRIPT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"


def new_context_kwargs() -> dict:
    """Kwargs for browser.new_context(...) that a caller can further extend."""
    return {"viewport": VIEWPORT, "user_agent": USER_AGENT}
