"""A small local dashboard: balance, equity curve, open positions, trade history.

Reads straight from the SQLite file the engine writes to, so it can run as a
separate process from `memecoin-trader run` and always show live state.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from memecoin_trader.config import DB_PATH, load_settings
from memecoin_trader.portfolio.db import get_connection, init_db
from memecoin_trader.portfolio.ledger import Ledger
from memecoin_trader.reporting import build_summary

app = FastAPI(title="Memecoin Trader Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _ledger() -> Ledger:
    settings = load_settings()
    conn = get_connection(DB_PATH)
    init_db(conn, Decimal(str(settings.starting_balance_usd)))
    return Ledger(conn)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    settings = load_settings()
    summary = build_summary(_ledger())
    return templates.TemplateResponse(
        request, "index.html", {"summary": summary, "mode": settings.mode}
    )


@app.get("/api/summary")
def api_summary():
    return build_summary(_ledger())
