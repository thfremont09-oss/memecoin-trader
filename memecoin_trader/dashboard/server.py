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
from memecoin_trader.portfolio.db import get_connection, init_db
from memecoin_trader.portfolio.ledger import CAUTION_LEVEL_LABELS, Ledger
from memecoin_trader.reporting import build_position_detail, build_summary

app = FastAPI(title="Memecoin Trader Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
_security = HTTPBasic(auto_error=False)


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


@app.get("/", response_class=HTMLResponse)
def index(request: Request, range: str = "all", _auth: None = Depends(require_auth)):
    settings = load_settings()
    summary = build_summary(_ledger(), settings, equity_range=range)
    return templates.TemplateResponse(
        request, "index.html", {"summary": summary, "mode": settings.mode}
    )


@app.get("/api/summary")
def api_summary(range: str = "all", _auth: None = Depends(require_auth)):
    return build_summary(_ledger(), load_settings(), equity_range=range)


@app.get("/api/position/{position_id}")
def api_position_detail(position_id: int, _auth: None = Depends(require_auth)):
    detail = build_position_detail(_ledger(), position_id)
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
    from memecoin_trader.engine import create_engine

    settings = load_settings()
    engine = create_engine(settings)

    state = engine.ledger.get_big_risk_state()
    if state.mode != "idle":
        return {"started": False, "mode": state.mode, "message": "Big Risk is already active."}

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
    from memecoin_trader.engine import create_engine

    settings = load_settings()
    engine = create_engine(settings)

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
    message = "Sold." if sold else "Couldn't sell — no market data available right now. Try again shortly."
    return {"sold": sold, "message": message}


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
    from memecoin_trader.engine import create_engine

    settings = load_settings()
    engine = create_engine(settings)

    total = len(engine.ledger.get_open_positions())
    closed = engine.liquidate_all() if total else 0
    engine.ledger.set_trading_enabled(False)

    if total == 0:
        message = "Offline. No open positions to sell."
    elif closed == total:
        message = f"Offline. Sold all {closed} position(s)."
    else:
        message = f"Offline. Sold {closed}/{total} position(s) — the rest had no market data available."
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
    from memecoin_trader.engine import create_engine

    settings = load_settings()
    engine = create_engine(settings)

    position = engine.ledger.get_open_position_for_token(token_address)
    if position is None:
        return {"sold": False, "message": "That position isn't open anymore."}

    sold = engine.liquidate_position(token_address)
    message = "Sold." if sold else "Couldn't sell — no market data available right now. Try again shortly."
    return {"sold": sold, "message": message}


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
