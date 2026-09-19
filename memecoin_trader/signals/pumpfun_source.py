"""Real-time pump.fun token-launch feed via PumpPortal's free public
WebSocket data API (wss://pumpportal.fun/api/data) -- no API key, no cost,
no ToS-violating scraping. Unlike every other signal source here, this
isn't social hype about an existing token; it's literal on-chain "a token
was just created" events.

That makes it the earliest possible discovery signal, but also the
highest-risk one: pump.fun launches are the single most rug/scam-heavy
category of token on Solana (most are abandoned or drained within minutes
of launch). This source does not treat a launch as investable on its own --
it just buffers each new mint for `config.min_age_minutes` before emitting
it as a SocialSignal at all, so nothing gets a chance to be bought before
it's already old enough to clear the engine's own min_pair_age_minutes
filter, and everything downstream (liquidity checks, RugCheck, the ML gate)
still applies exactly like it does to a Twitter/Reddit-sourced signal.

PumpPortal's exact message schema is taken from its public docs/community
examples, not verified live against the feed from the sandbox this was
built in (no network access to pumpportal.fun there) -- if the `mint`/
`name`/`symbol` field names have drifted, poll() will just quietly find no
launches; check the logs the first time this runs for real.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone

from memecoin_trader.config import PumpFunSignalConfig
from memecoin_trader.signals.base import SignalSource, SocialSignal

logger = logging.getLogger(__name__)

WS_URL = "wss://pumpportal.fun/api/data"
SUBSCRIBE_MESSAGE = json.dumps({"method": "subscribeNewToken"})
# No hype/engagement data exists for a brand-new launch -- this is a flat
# baseline, not a computed score, reflecting "a real launch was observed"
# and nothing more.
LAUNCH_SIGNAL_SCORE = 50.0
INITIAL_RECONNECT_BACKOFF_SECONDS = 5
MAX_RECONNECT_BACKOFF_SECONDS = 60


class PumpFunLaunchSource(SignalSource):
    name = "pumpfun_launch"

    def __init__(self, config: PumpFunSignalConfig, chain_id: str):
        self._config = config
        self._chain_id = chain_id
        self._lock = threading.Lock()
        self._buffer: dict[str, dict] = {}  # mint -> {"first_seen": ts, "symbol": ..., "name": ...}
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def _handle_message(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return
        mint = data.get("mint")
        if not mint or not isinstance(mint, str):
            return
        with self._lock:
            if mint not in self._buffer:
                self._buffer[mint] = {
                    "first_seen": time.time(),
                    "symbol": data.get("symbol") or mint[:8],
                    "name": data.get("name") or "",
                }

    def _run_forever(self) -> None:
        import websocket

        backoff = INITIAL_RECONNECT_BACKOFF_SECONDS
        while not self._stop_event.is_set():
            try:
                # `timeout` here only bounds the initial connect -- it also
                # sets the socket's default timeout, so without clearing it
                # afterwards, recv() below would raise a timeout (and look
                # like a dropped connection) during any quiet stretch longer
                # than this, even though the socket is perfectly fine.
                ws = websocket.create_connection(WS_URL, timeout=10)
                ws.settimeout(None)
                try:
                    ws.send(SUBSCRIBE_MESSAGE)
                    logger.info("connected to pump.fun launch feed")
                    backoff = INITIAL_RECONNECT_BACKOFF_SECONDS
                    while not self._stop_event.is_set():
                        self._handle_message(ws.recv())
                finally:
                    ws.close()
            except Exception:
                if self._stop_event.is_set():
                    return
                logger.warning("pump.fun feed connection dropped, reconnecting in %ds", backoff)
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, MAX_RECONNECT_BACKOFF_SECONDS)

    def _ensure_connected(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_forever, daemon=True)
        self._thread.start()

    def poll(self) -> list[SocialSignal]:
        self._ensure_connected()

        now = time.time()
        min_age_seconds = self._config.min_age_minutes * 60
        max_buffer_seconds = self._config.max_buffer_minutes * 60
        ready: list[tuple[str, dict]] = []
        with self._lock:
            for mint, info in list(self._buffer.items()):
                age_seconds = now - info["first_seen"]
                if age_seconds >= max_buffer_seconds:
                    del self._buffer[mint]  # never cleared the bar in time -- give up on it
                    continue
                if age_seconds >= min_age_seconds:
                    ready.append((mint, info))
                    del self._buffer[mint]  # emit at most once per launch

        if not ready:
            return []

        observed_at = datetime.now(timezone.utc)
        signals = [
            SocialSignal(
                token_address=mint,
                symbol=info["symbol"],
                chain_id=self._chain_id,
                source=self.name,
                score=LAUNCH_SIGNAL_SCORE,
                mention_count=1,
                excerpt=f"pump.fun launch: {info['name'] or info['symbol']} ({mint})",
                observed_at=observed_at,
            )
            for mint, info in ready
        ]
        logger.info("pump.fun launch source emitted %d signal(s) from buffered launches", len(signals))
        return signals
