"""SQLite schema + connection helper.

Money values are stored as TEXT (decimal strings), never REAL/float, so we
never lose precision round-tripping through the database — the whole point
of "the simulation must be accurate".
"""
from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS portfolio_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    cash_usd TEXT NOT NULL,
    realized_pnl_usd TEXT NOT NULL,
    starting_balance_usd TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    trading_enabled INTEGER NOT NULL DEFAULT 1,
    caution_level INTEGER NOT NULL DEFAULT 3
);

CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT NOT NULL,
    symbol TEXT,
    chain_id TEXT,
    entry_price_usd TEXT NOT NULL,
    quantity TEXT NOT NULL,
    original_quantity TEXT NOT NULL,
    cost_basis_usd TEXT NOT NULL,
    fees_paid_usd TEXT NOT NULL,
    peak_price_usd TEXT NOT NULL,
    entry_liquidity_usd TEXT NOT NULL DEFAULT '0',
    take_profit_taken INTEGER NOT NULL DEFAULT 0,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    signal_source TEXT,
    signal_score REAL,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_token ON positions(token_address);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER,
    token_address TEXT NOT NULL,
    symbol TEXT,
    side TEXT NOT NULL,
    price_usd TEXT NOT NULL,
    quantity TEXT NOT NULL,
    amount_usd TEXT NOT NULL,
    fee_usd TEXT NOT NULL,
    realized_pnl_usd TEXT,
    reason TEXT,
    executed_at TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'paper'
);
CREATE INDEX IF NOT EXISTS idx_trades_executed_at ON trades(executed_at);

CREATE TABLE IF NOT EXISTS price_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT NOT NULL,
    price_usd TEXT NOT NULL,
    liquidity_usd TEXT,
    volume_24h_usd TEXT,
    captured_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_token_time ON price_snapshots(token_address, captured_at);

CREATE TABLE IF NOT EXISTS signals_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT NOT NULL,
    symbol TEXT,
    source TEXT NOT NULL,
    score REAL,
    mention_count INTEGER,
    raw_excerpt TEXT,
    received_at TEXT NOT NULL,
    acted_on INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS trade_features (
    position_id INTEGER PRIMARY KEY,
    features_json TEXT NOT NULL,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS equity_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cash_usd TEXT NOT NULL,
    positions_value_usd TEXT NOT NULL,
    equity_usd TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_equity_time ON equity_history(recorded_at);
"""


def get_connection(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, isolation_level=None)  # autocommit; ledger wraps its own transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def init_db(conn: sqlite3.Connection, starting_balance_usd: Decimal) -> None:
    conn.executescript(SCHEMA)

    # Migration for DBs created before the online/offline toggle existed.
    if not _column_exists(conn, "portfolio_state", "trading_enabled"):
        conn.execute("ALTER TABLE portfolio_state ADD COLUMN trading_enabled INTEGER NOT NULL DEFAULT 1")

    # Migration for DBs created before the caution-level slider existed.
    if not _column_exists(conn, "portfolio_state", "caution_level"):
        conn.execute("ALTER TABLE portfolio_state ADD COLUMN caution_level INTEGER NOT NULL DEFAULT 3")

    row = conn.execute("SELECT 1 FROM portfolio_state WHERE id = 1").fetchone()
    if row is None:
        from datetime import datetime, timezone

        conn.execute(
            "INSERT INTO portfolio_state "
            "(id, cash_usd, realized_pnl_usd, starting_balance_usd, updated_at, trading_enabled, caution_level) "
            "VALUES (1, ?, '0', ?, ?, 1, 3)",
            (str(starting_balance_usd), str(starting_balance_usd), datetime.now(timezone.utc).isoformat()),
        )


def reset_db(conn: sqlite3.Connection, starting_balance_usd: Decimal) -> None:
    """Wipe all simulation data and restart from the configured starting balance."""
    conn.executescript(
        """
        DELETE FROM positions;
        DELETE FROM trades;
        DELETE FROM price_snapshots;
        DELETE FROM signals_log;
        DELETE FROM equity_history;
        DELETE FROM trade_features;
        DELETE FROM portfolio_state;
        """
    )
    init_db(conn, starting_balance_usd)
