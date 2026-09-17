"""Pre-trade safety check via RugCheck.xyz's public API (no key required).

RugCheck inspects the token's actual on-chain mint/program state — things
DexScreener doesn't report at all — and flags the classic rug-pull setups:
an un-renounced mint authority (deployer can print unlimited new supply), an
active freeze authority (deployer can freeze your wallet's tokens), unlocked
LP tokens (deployer can pull all liquidity instantly), and extreme holder
concentration (a few wallets, often the deployer's, can dump on everyone
else).

This is a best-effort integration against a third-party API this sandbox
cannot reach to verify live (outbound network here is restricted to a small
allowlist) — the parsing is deliberately defensive so a schema change or
outage degrades to "risk unknown" instead of crashing the bot. Verify it
once it's running for real and tell me if the shape of the response has
drifted from what's assumed here.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.rugcheck.xyz/v1"
REQUEST_TIMEOUT_SECONDS = 10
MAX_RETRIES = 1
RETRY_BACKOFF_SECONDS = 1.5

# RugCheck risk levels, worst to best, as reported per-finding.
_DANGER_LEVELS = {"danger", "critical", "high"}
_WARN_LEVELS = {"warn", "warning", "medium"}


@dataclass(frozen=True)
class RugRiskReport:
    token_address: str
    score: float  # RugCheck's own 0-100+ risk score; higher = riskier
    danger_flags: tuple[str, ...]  # e.g. "Mint authority not renounced"
    warning_flags: tuple[str, ...]
    mint_authority_renounced: bool | None  # None = not reported / unknown
    freeze_authority_renounced: bool | None
    lp_locked_pct: float | None  # 0-100, None if unknown

    @property
    def is_high_risk(self) -> bool:
        return len(self.danger_flags) > 0


class RugCheckClient:
    def __init__(self, session: requests.Session | None = None):
        self._session = session or requests.Session()

    def _get(self, path: str) -> dict[str, Any] | None:
        url = f"{BASE_URL}{path}"
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
                if resp.status_code == 404:
                    return None  # token not indexed by RugCheck yet — not necessarily unsafe
                resp.raise_for_status()
                return resp.json()
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_BACKOFF_SECONDS)
        logger.warning("RugCheck request failed (%s): %s", url, last_exc)
        return None

    def get_risk_report(self, token_address: str) -> RugRiskReport | None:
        """Returns None if the report couldn't be fetched/parsed at all —
        callers must decide (via config) whether "unknown" blocks a trade."""
        data = self._get(f"/tokens/{token_address}/report/summary")
        if data is None:
            return None

        try:
            risks = data.get("risks") or []
            danger = tuple(
                r.get("name", "unknown risk")
                for r in risks
                if str(r.get("level", "")).lower() in _DANGER_LEVELS
            )
            warnings = tuple(
                r.get("name", "unknown risk")
                for r in risks
                if str(r.get("level", "")).lower() in _WARN_LEVELS
            )
            token_meta = data.get("token") or {}
            lp_locked = data.get("markets") or []
            lp_locked_pct = None
            if lp_locked:
                pcts = [m.get("lp", {}).get("lpLockedPct") for m in lp_locked if isinstance(m, dict)]
                pcts = [p for p in pcts if isinstance(p, (int, float))]
                if pcts:
                    lp_locked_pct = min(pcts)  # worst-case across pools

            return RugRiskReport(
                token_address=token_address,
                score=float(data.get("score", 0) or 0),
                danger_flags=danger,
                warning_flags=warnings,
                mint_authority_renounced=(
                    token_meta.get("mintAuthority") is None if "mintAuthority" in token_meta else None
                ),
                freeze_authority_renounced=(
                    token_meta.get("freezeAuthority") is None if "freezeAuthority" in token_meta else None
                ),
                lp_locked_pct=lp_locked_pct,
            )
        except (AttributeError, TypeError, ValueError) as exc:
            logger.warning("RugCheck response for %s didn't match expected shape: %s", token_address, exc)
            return None
