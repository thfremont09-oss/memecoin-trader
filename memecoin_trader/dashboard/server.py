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
from memecoin_trader.reporting import build_summary

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
    summary = build_summary(_ledger(), equity_range=range)
    return templates.TemplateResponse(
        request, "index.html", {"summary": summary, "mode": settings.mode}
    )


@app.get("/api/summary")
def api_summary(range: str = "all", _auth: None = Depends(require_auth)):
    return build_summary(_ledger(), equity_range=range)


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
