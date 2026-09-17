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
from memecoin_trader.portfolio.ledger import Ledger
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
def index(request: Request, _auth: None = Depends(require_auth)):
    settings = load_settings()
    summary = build_summary(_ledger())
    return templates.TemplateResponse(
        request, "index.html", {"summary": summary, "mode": settings.mode}
    )


@app.get("/api/summary")
def api_summary(_auth: None = Depends(require_auth)):
    return build_summary(_ledger())


@app.post("/api/liquidate")
def api_liquidate(_auth: None = Depends(require_auth)):
    """Sells every open position right now, at current market price.

    Builds its own engine instance rather than reaching into a running one —
    the dashboard and the trading engine are separate processes (separate
    Scheduled Tasks), so there is no shared in-memory engine to call into.
    Safe to do: both processes already share the same SQLite file (WAL mode).
    """
    from memecoin_trader.engine import create_engine

    settings = load_settings()
    engine = create_engine(settings)

    total = len(engine.ledger.get_open_positions())
    if total == 0:
        return {"closed": 0, "total": 0, "message": "No open positions."}

    closed = engine.liquidate_all()
    if closed == total:
        message = f"Liquidated all {closed} position(s)."
    else:
        message = f"Liquidated {closed}/{total} position(s) — the rest had no market data available; try again shortly."
    return {"closed": closed, "total": total, "message": message}
