"""Client for the public DexScreener API (https://docs.dexscreener.com/api/reference).

No API key required. This is where the simulation gets its *real* market
data (price, liquidity, volume, pair age) so that paper trades are filled
against actual on-chain conditions rather than made-up numbers.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.dexscreener.com"
REQUEST_TIMEOUT_SECONDS = 10
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 1.5
# "quick" mode (get_best_pair_for_token(..., quick=True)) is for the
# dashboard's live-refresh display, not trading decisions: a short timeout
# with no retries so a slow/unreachable API degrades to "one stale-looking
# refresh," not "the whole dashboard hangs," plus a short cache so a burst
# of near-simultaneous lookups for the same token (multiple open positions,
# overlapping requests) doesn't multiply into redundant calls.
QUICK_TIMEOUT_SECONDS = 2.5
QUICK_CACHE_TTL_SECONDS = 1.0


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


@dataclass(frozen=True)
class PairInfo:
    """A snapshot of one trading pair, as reported by DexScreener right now."""

    chain_id: str
    dex_id: str
    pair_address: str
    token_address: str
    symbol: str
    name: str
    price_usd: Decimal
    liquidity_usd: Decimal
    volume_24h_usd: Decimal
    price_change_5m_pct: float
    price_change_1h_pct: float
    price_change_24h_pct: float
    buys_24h: int
    sells_24h: int
    pair_created_at: datetime | None
    fdv_usd: Decimal | None
    url: str

    @property
    def age_minutes(self) -> float | None:
        if self.pair_created_at is None:
            return None
        delta = datetime.now(timezone.utc) - self.pair_created_at
        return delta.total_seconds() / 60.0

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "PairInfo | None":
        price_usd = _to_decimal(raw.get("priceUsd"))
        if price_usd is None or price_usd <= 0:
            return None
        base = raw.get("baseToken") or {}
        liquidity = raw.get("liquidity") or {}
        volume = raw.get("volume") or {}
        price_change = raw.get("priceChange") or {}
        txns_24h = (raw.get("txns") or {}).get("h24") or {}

        created_at_ms = raw.get("pairCreatedAt")
        created_at = (
            datetime.fromtimestamp(created_at_ms / 1000, tz=timezone.utc)
            if created_at_ms
            else None
        )

        return cls(
            chain_id=raw.get("chainId", ""),
            dex_id=raw.get("dexId", ""),
            pair_address=raw.get("pairAddress", ""),
            token_address=base.get("address", ""),
            symbol=base.get("symbol", "?"),
            name=base.get("name", "?"),
            price_usd=price_usd,
            liquidity_usd=_to_decimal(liquidity.get("usd")) or Decimal(0),
            volume_24h_usd=_to_decimal(volume.get("h24")) or Decimal(0),
            price_change_5m_pct=float(price_change.get("m5") or 0.0),
            price_change_1h_pct=float(price_change.get("h1") or 0.0),
            price_change_24h_pct=float(price_change.get("h24") or 0.0),
            buys_24h=int(txns_24h.get("buys") or 0),
            sells_24h=int(txns_24h.get("sells") or 0),
            pair_created_at=created_at,
            fdv_usd=_to_decimal(raw.get("fdv")),
            url=raw.get("url", ""),
        )


class DexScreenerError(RuntimeError):
    pass


class DexScreenerClient:
    def __init__(self, session: requests.Session | None = None):
        self._session = session or requests.Session()
        self._quick_cache: dict[tuple[str, str], tuple[float, PairInfo | None]] = {}

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        url = f"{BASE_URL}{path}"
        timeout = REQUEST_TIMEOUT_SECONDS if timeout is None else timeout
        retries = MAX_RETRIES if max_retries is None else max_retries
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=timeout)
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt < retries:
                    time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
        logger.warning("DexScreener request failed (%s): %s", url, last_exc)
        raise DexScreenerError(str(last_exc)) from last_exc

    def get_best_pair_for_token(
        self, chain_id: str, token_address: str, *, quick: bool = False
    ) -> PairInfo | None:
        """Return the highest-liquidity pair for a token address, or None if
        unknown/unreachable.

        `quick=True` is for a display refresh, not a trading decision: a
        short timeout, no retries, and a ~1s cache (see QUICK_TIMEOUT_SECONDS/
        QUICK_CACHE_TTL_SECONDS) so a slow or unreachable API can't stall
        whatever's calling this in a loop. Never used by the engine's own
        trading logic, which still gets the patient, retrying default.
        """
        cache_key = (chain_id, token_address)
        if quick:
            cached = self._quick_cache.get(cache_key)
            if cached is not None and time.time() - cached[0] < QUICK_CACHE_TTL_SECONDS:
                return cached[1]

        try:
            data = self._get(
                f"/latest/dex/tokens/{token_address}",
                timeout=QUICK_TIMEOUT_SECONDS if quick else None,
                max_retries=0 if quick else None,
            )
        except DexScreenerError:
            if quick:
                self._quick_cache[cache_key] = (time.time(), None)
            return None
        pairs_raw = data.get("pairs") or []
        candidates = [
            p
            for p in (PairInfo.from_api(r) for r in pairs_raw)
            if p is not None and p.chain_id == chain_id
        ]
        result = max(candidates, key=lambda p: p.liquidity_usd) if candidates else None
        if quick:
            self._quick_cache[cache_key] = (time.time(), result)
        return result

    def search(self, query: str, chain_id: str | None = None) -> list[PairInfo]:
        try:
            data = self._get("/latest/dex/search", params={"q": query})
        except DexScreenerError:
            return []
        pairs_raw = data.get("pairs") or []
        results = [p for p in (PairInfo.from_api(r) for r in pairs_raw) if p is not None]
        if chain_id:
            results = [p for p in results if p.chain_id == chain_id]
        return results

    def get_latest_boosted_tokens(self, chain_id: str | None = None) -> list[dict[str, Any]]:
        """Tokens whose creators just paid DexScreener to promote them.

        Used as a realistic stand-in "trending pool" for the simulated Twitter
        feed: these are exactly the kind of freshly-launched, actively-promoted
        tokens that generate organic FOMO chatter on social media.
        """
        try:
            data = self._get("/token-boosts/latest/v1")
        except DexScreenerError:
            return []
        items = data if isinstance(data, list) else data.get("tokens", [])
        if chain_id:
            items = [t for t in items if t.get("chainId") == chain_id]
        return items
