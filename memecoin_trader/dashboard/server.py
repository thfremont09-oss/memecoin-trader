"""A small local dashboard: balance, equity curve, open positions, trade history.

Reads straight from the SQLite file the engine writes to, so it can run as a
separate process from `memecoin-trader run` and always show live state.
"""
from __future__ import annotations

import os
import secrets as secrets_module
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from memecoin_trader.config import DB_PATH, load_settings
from memecoin_trader.market.dexscreener import DexScreenerClient
from memecoin_trader.portfolio.db import get_connection, init_db
from memecoin_trader.portfolio.ledger import CAUTION_LEVEL_LABELS, Ledger
from memecoin_trader.reporting import build_position_detail, build_summary

app = FastAPI(title="Memecoin Trader Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_security = HTTPBasic(auto_error=False)
# One shared client (not a fresh one per request) so its quick-lookup cache
# (see DexScreenerClient.get_best_pair_for_token's `quick=True`) actually
# does something -- it dedupes near-simultaneous lookups for the same
# token across requests, which a throwaway per-request instance couldn't.
_market_client = DexScreenerClient()


def require_auth(credentials: Annotated[HTTPBasicCredentials | None, Depends(_security)] = None) -> None:
    """Gate the dashboard with HTTP Basic Auth if DASHBOARD_USERNAME/PASSWORD are set.

    Left wide open if neither is configured (fine for localhost use), but you
    should set both before exposing this on a public URL (e.g. a Fly.io app).
    """
    username = os.environ.get("DASHBOARD_USERNAME")
    password = os.environ.get("DASHBOARD_PASSWORD")
    if not username or not password:
        return
    valid = credentials is not None and secrets_module.compare_digest(
        credentials.username, username
    ) and secrets_module.compare_digest(credentials.password, password)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


def _ledger() -> Ledger:
    settings = load_settings()
    conn = get_connection(DB_PATH)
    init_db(conn, Decimal(str(settings.starting_balance_usd)))
    return Ledger(conn)


def _sell_failure_reason(mode: str) -> str:
    """Why `liquidate_position` returned False, worded for the mode it ran
    in. Paper mode can only fail this way for one reason (no DexScreener
    data to simulate against); live mode no longer depends on DexScreener
    for pricing a sell, so a failure there is a real execution error
    (the swap itself failing), not missing market data."""
    if mode == "live":
        return "the swap failed"
    return "no market data available right now"


def _sell_failure_message(mode: str) -> str:
    if mode == "live":
        return "Couldn't sell — the swap failed. Check trader.log for the exact error."
    return "Couldn't sell — no market data available right now. Try again shortly."


@app.get("/", response_class=HTMLResponse)
def index(request: Request, range: str = "all", _auth: None = Depends(require_auth)):
    settings = load_settings()
    summary = build_summary(_ledger(), settings, equity_range=range, market_client=_market_client)
    return templates.TemplateResponse(
        request, "index.html", {"summary": summary, "mode": settings.mode}
    )


@app.get("/api/summary")
def api_summary(range: str = "all", _auth: None = Depends(require_auth)):
    return build_summary(_ledger(), load_settings(), equity_range=range, market_client=_market_client)


@app.get("/api/position/{position_id}")
def api_position_detail(position_id: int, _auth: None = Depends(require_auth)):
    settings = load_settings()
    detail = build_position_detail(_ledger(), position_id, market_client=_market_client, chain_id=settings.chain_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Position not found")
    return detail


@app.post("/api/big-risk/start")
def api_big_risk_start(_auth: None = Depends(require_auth)):
    """The BIG RISK button: sells everything right now, then arms a
    search window (big_risk.search_window_seconds) during which the
    engine looks for one signal to put the entire cash balance into —
    see TradingEngine._big_risk_search/_big_risk_manage_position for the
    actual state machine. Built the same way api_offline is: a fresh
    engine instance against the shared SQLite file, since the dashboard
    and the running engine are separate processes.
    """
    from memecoin_trader.engine import create_action_engine

    settings = load_settings()
    engine = create_action_engine(settings)

    state = engine.ledger.get_big_risk_state()
    if state.mode != "idle":
        return {"started": False, "mode": state.mode, "message": "Big Risk is already active."}

    if not engine.ledger.is_trading_enabled():
        # The engine's own tick() aborts any Big Risk search the instant it
        # sees trading is offline (see tick()'s "searching" branch) -- with
        # only a log line, no visible explanation. Arming one here anyway
        # would self-cancel within one tick (~5s) looking exactly like a
        # bug, so refuse up front with an actionable message instead.
        return {
            "started": False,
            "mode": state.mode,
            "message": "Can't start Big Risk — trading is currently offline. Click Online first.",
        }

    total = len(engine.ledger.get_open_positions())
    closed = engine.liquidate_all(reason="big_risk_start") if total else 0
    engine.ledger.start_big_risk_search()
    return {
        "started": True,
        "mode": "searching",
        "sold": closed,
        "message": f"BIG RISK ARMED. Sold {closed} position(s) — scanning for a target...",
    }


@app.post("/api/big-risk/cancel")
def api_big_risk_cancel(_auth: None = Depends(require_auth)):
    """Cancels an in-progress search (before anything's been bought).
    Once a position's been taken (mode "invested"), use /api/big-risk/stop
    instead — there's an actual position to sell there, not just a
    search to call off."""
    ledger = _ledger()
    state = ledger.get_big_risk_state()
    if state.mode != "searching":
        return {"cancelled": False, "mode": state.mode, "message": "No Big Risk search in progress."}
    ledger.end_big_risk()
    return {"cancelled": True, "mode": "idle", "message": "Big Risk search cancelled."}


@app.post("/api/big-risk/stop")
def api_big_risk_stop(_auth: None = Depends(require_auth)):
    """Manually sells the Big Risk position early, same idea as the
    per-position Sell button."""
    from memecoin_trader.engine import create_action_engine

    settings = load_settings()
    engine = create_action_engine(settings)

    state = engine.ledger.get_big_risk_state()
    if state.mode != "invested" or state.position_id is None:
        return {"sold": False, "message": "No Big Risk position to sell."}

    position = engine.ledger.get_position_by_id(state.position_id)
    if position is None or position.status != "open":
        engine.ledger.end_big_risk()
        return {"sold": False, "message": "That position isn't open anymore."}

    sold = engine.liquidate_position(position.token_address, reason="big_risk_manual_sell")
    if sold:
        engine.ledger.end_big_risk()
    message = "Sold." if sold else _sell_failure_message(settings.mode)
    return {"sold": sold, "message": message}


@app.get("/api/search")
def api_search_tokens(q: str, _auth: None = Depends(require_auth)):
    """Backs the "Manual buy" panel's search box -- DexScreener's public
    search, matching by name/symbol/address, restricted to the configured
    chain and sorted by liquidity (highest first, so the real token tends
    to rank above copycat/scam clones sharing the same ticker)."""
    query = q.strip()
    if not query:
        return {"results": []}

    settings = load_settings()
    pairs = _market_client.search(query, chain_id=settings.chain_id)
    pairs.sort(key=lambda p: p.liquidity_usd, reverse=True)
    results = [
        {
            "token_address": p.token_address,
            "symbol": p.symbol,
            "name": p.name,
            "price_usd": float(p.price_usd),
            "liquidity_usd": float(p.liquidity_usd),
            "volume_24h_usd": float(p.volume_24h_usd),
            "price_change_24h_pct": p.price_change_24h_pct,
            "age_minutes": p.age_minutes,
            "dex_id": p.dex_id,
        }
        for p in pairs[:15]
    ]
    return {"results": results}


@app.post("/api/manual-buy/{token_address}")
def api_manual_buy(token_address: str, amount_usd: float, _auth: None = Depends(require_auth)):
    """The "Manual buy" panel's Buy button: buys a specific, user-picked
    token for a user-picked dollar amount, bypassing the algorithmic entry
    filters entirely -- see TradingEngine.manual_buy for why. Once bought
    it's an ordinary position, protected by the normal exit rules from
    the engine's very next tick."""
    from memecoin_trader.engine import create_action_engine

    settings = load_settings()
    engine = create_action_engine(settings)
    bought, message = engine.manual_buy(token_address, Decimal(str(amount_usd)))
    return {"bought": bought, "message": message}


@app.post("/api/offline")
def api_offline(_auth: None = Depends(require_auth)):
    """Sells everything, then flips the engine's trading_enabled flag off.

    Builds its own engine instance rather than reaching into a running one —
    the dashboard and the trading engine are separate processes (separate
    Scheduled Tasks), so there is no shared in-memory engine to call into.
    Safe to do: both processes already share the same SQLite file (WAL mode).
    Going offline stops new buys; the actual engine process keeps running and
    still protects any position that couldn't be sold below.
    """
    from memecoin_trader.engine import create_action_engine

    settings = load_settings()
    engine = create_action_engine(settings)

    total = len(engine.ledger.get_open_positions())
    closed = engine.liquidate_all() if total else 0
    engine.ledger.set_trading_enabled(False)

    if total == 0:
        message = "Offline. No open positions to sell."
    elif closed == total:
        message = f"Offline. Sold all {closed} position(s)."
    else:
        message = (
            f"Offline. Sold {closed}/{total} position(s) — the rest failed to sell "
            f"({_sell_failure_reason(settings.mode)}). Check trader.log for the exact error."
        )
    return {"trading_enabled": False, "closed": closed, "total": total, "message": message}


@app.post("/api/online")
def api_online(_auth: None = Depends(require_auth)):
    _ledger().set_trading_enabled(True)
    return {
        "trading_enabled": True,
        "message": "Online. The engine will start looking for new trades again on its next check.",
    }


@app.post("/api/sell/{token_address}")
def api_sell_one(token_address: str, _auth: None = Depends(require_auth)):
    from memecoin_trader.engine import create_action_engine

    settings = load_settings()
    engine = create_action_engine(settings)

    position = engine.ledger.get_open_position_for_token(token_address)
    if position is None:
        return {"sold": False, "message": "That position isn't open anymore."}

    sold = engine.liquidate_position(token_address)
    message = "Sold." if sold else _sell_failure_message(settings.mode)
    return {"sold": sold, "message": message}


@app.post("/api/close-position/{token_address}")
def api_close_position(token_address: str, proceeds_usd: float | None = None, _auth: None = Depends(require_auth)):
    """Marks an open position closed WITHOUT executing a swap -- for when
    you already sold it yourself directly in your wallet (Solflare,
    Phantom, etc.), outside the bot. Live mode's cash balance auto-syncs
    from your real wallet, but open *positions* are pure ledger
    bookkeeping -- nothing reconciles them against what your wallet
    actually holds, so a position you closed elsewhere stays "open" here,
    and the engine keeps trying (and failing) to exit it, until this is
    called. `proceeds_usd` (optional) is the total USD you actually
    received; omitted, this falls back to the current DexScreener price,
    which is only an estimate of what you actually got."""
    from memecoin_trader.execution.base import FillResult

    settings = load_settings()
    ledger = _ledger()
    position = ledger.get_open_position_for_token(token_address)
    if position is None:
        return {"closed": False, "message": "That position isn't open anymore."}

    if proceeds_usd is not None:
        proceeds = Decimal(str(proceeds_usd))
        price_usd = proceeds / position.quantity if position.quantity > 0 else Decimal(0)
    else:
        market = _market_client.get_best_pair_for_token(settings.chain_id, token_address)
        if market is None:
            return {
                "closed": False,
                "message": "No current market price available either -- re-send with the amount you actually received.",
            }
        price_usd = market.price_usd
        proceeds = price_usd * position.quantity

    fill = FillResult(price_usd=price_usd, quantity=position.quantity, amount_usd=proceeds, fee_usd=Decimal(0), tx_id=None)
    ledger.apply_sell(
        position=position,
        fraction=Decimal(1),
        fill=fill,
        reason="manual_external_sell",
        mark_take_profit_taken=True,
        mode=settings.mode,
    )
    estimated_note = "" if proceeds_usd is not None else " (estimated from current market price, not your actual proceeds)"
    return {"closed": True, "message": f"Closed {position.symbol} at ${proceeds:.2f}{estimated_note}."}


@app.post("/api/close-all-positions")
def api_close_all_positions(_auth: None = Depends(require_auth)):
    """Closes every open position in the ledger WITHOUT executing any swaps
    -- for when your wallet no longer matches the dashboard at all (e.g. you
    sold everything directly in Solflare/Phantom). Each position is priced
    off current DexScreener data, since there's no way to know your actual
    per-token proceeds here; close-position (single-token, dashboard button
    or CLI) takes an exact proceeds_usd if you want accurate P&L for one."""
    from memecoin_trader.execution.base import FillResult

    settings = load_settings()
    ledger = _ledger()
    positions = ledger.get_open_positions()
    if not positions:
        return {"closed": 0, "total": 0, "message": "No open positions."}

    closed = 0
    for position in positions:
        market = _market_client.get_best_pair_for_token(settings.chain_id, position.token_address)
        if market is None:
            continue
        proceeds = market.price_usd * position.quantity
        fill = FillResult(price_usd=market.price_usd, quantity=position.quantity, amount_usd=proceeds, fee_usd=Decimal(0), tx_id=None)
        ledger.apply_sell(
            position=position,
            fraction=Decimal(1),
            fill=fill,
            reason="manual_external_sell",
            mark_take_profit_taken=True,
            mode=settings.mode,
        )
        closed += 1

    total = len(positions)
    if closed == total:
        message = f"Closed all {closed} position(s) (estimated from current market prices)."
    else:
        message = f"Closed {closed}/{total} position(s) -- the rest had no market data to price them at. Try again shortly."
    return {"closed": closed, "total": total, "message": message}


@app.post("/api/caution-level/{level}")
def api_set_caution_level(level: int, _auth: None = Depends(require_auth)):
    """Sets the caution-level slider. Takes effect on the engine's very next
    signal poll (it reads this fresh every poll, no restart needed) — same
    immediacy as the online/offline toggle."""
    stored = _ledger().set_caution_level(level)
    return {
        "caution_level": stored,
        "label": CAUTION_LEVEL_LABELS.get(stored, str(stored)),
        "message": f"Caution level set to {stored} ({CAUTION_LEVEL_LABELS.get(stored, stored)}).",
    }
