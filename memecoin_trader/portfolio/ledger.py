"""The accounting core: every buy/sell mutates cash and positions atomically.

All money math is done in Decimal and persisted as decimal strings (see
portfolio/db.py) — no floats touch a balance anywhere in this module.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal

from memecoin_trader.execution.base import FillResult
from memecoin_trader.portfolio.models import BigRiskState, PortfolioState, Position, Trade
from memecoin_trader.signals.base import SocialSignal

MIN_CAUTION_LEVEL = 1
MAX_CAUTION_LEVEL = 5
CAUTION_LEVEL_LABELS: dict[int, str] = {
    1: "Very cautious",
    2: "Cautious",
    3: "Balanced",
    4: "Active",
    5: "Aggressive",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _downsample(rows: list, max_points: int) -> list:
    """Picks up to `max_points` evenly-spaced rows from an oldest-first
    sequence, always keeping the most recent one exactly (so the chart's
    latest value is never stale/interpolated)."""
    if max_points <= 0 or len(rows) <= max_points:
        return rows
    step = len(rows) / max_points
    indices = sorted({int(i * step) for i in range(max_points)})
    if indices[-1] != len(rows) - 1:
        indices[-1] = len(rows) - 1
    return [rows[i] for i in indices]


def _row_to_position(row: sqlite3.Row) -> Position:
    return Position(
        id=row["id"],
        token_address=row["token_address"],
        symbol=row["symbol"],
        chain_id=row["chain_id"],
        entry_price_usd=Decimal(row["entry_price_usd"]),
        quantity=Decimal(row["quantity"]),
        original_quantity=Decimal(row["original_quantity"]),
        cost_basis_usd=Decimal(row["cost_basis_usd"]),
        fees_paid_usd=Decimal(row["fees_paid_usd"]),
        peak_price_usd=Decimal(row["peak_price_usd"]),
        entry_liquidity_usd=Decimal(row["entry_liquidity_usd"]),
        take_profit_taken=bool(row["take_profit_taken"]),
        opened_at=_parse_dt(row["opened_at"]),
        signal_source=row["signal_source"] or "",
        signal_score=row["signal_score"] or 0.0,
        status=row["status"],
        closed_at=_parse_dt(row["closed_at"]) if row["closed_at"] else None,
    )


def _row_to_trade(row: sqlite3.Row) -> Trade:
    return Trade(
        id=row["id"],
        position_id=row["position_id"],
        token_address=row["token_address"],
        symbol=row["symbol"],
        side=row["side"],
        price_usd=Decimal(row["price_usd"]),
        quantity=Decimal(row["quantity"]),
        amount_usd=Decimal(row["amount_usd"]),
        fee_usd=Decimal(row["fee_usd"]),
        realized_pnl_usd=Decimal(row["realized_pnl_usd"]) if row["realized_pnl_usd"] is not None else None,
        reason=row["reason"] or "",
        executed_at=_parse_dt(row["executed_at"]),
        mode=row["mode"],
    )


class InsufficientCashError(RuntimeError):
    pass


class Ledger:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    # ---------------------------------------------------------------- state

    def get_portfolio_state(self) -> PortfolioState:
        row = self._conn.execute("SELECT * FROM portfolio_state WHERE id = 1").fetchone()
        return PortfolioState(
            cash_usd=Decimal(row["cash_usd"]),
            realized_pnl_usd=Decimal(row["realized_pnl_usd"]),
            starting_balance_usd=Decimal(row["starting_balance_usd"]),
            updated_at=_parse_dt(row["updated_at"]),
            trading_enabled=bool(row["trading_enabled"]),
            caution_level=row["caution_level"],
        )

    def get_cash_usd(self) -> Decimal:
        return self.get_portfolio_state().cash_usd

    def deposit_cash(self, amount: Decimal) -> Decimal:
        """Adds fresh cash to the portfolio (e.g. `memecoin-trader deposit 20`).

        Raises `starting_balance_usd` by the same amount as `cash_usd`, so
        `total_return_usd` (equity - starting_balance) keeps measuring
        actual trading performance rather than counting the deposit itself
        as profit. Returns the new cash balance.
        """
        if amount <= 0:
            raise ValueError("deposit amount must be positive")
        state = self.get_portfolio_state()
        new_cash = state.cash_usd + amount
        new_starting_balance = state.starting_balance_usd + amount
        self._conn.execute(
            "UPDATE portfolio_state SET cash_usd = ?, starting_balance_usd = ?, updated_at = ? WHERE id = 1",
            (str(new_cash), str(new_starting_balance), _now_iso()),
        )
        return new_cash

    def is_trading_enabled(self) -> bool:
        return self.get_portfolio_state().trading_enabled

    def set_trading_enabled(self, enabled: bool) -> None:
        """Toggled by the dashboard's Go offline/Go online buttons (and the
        matching CLI commands). The engine checks this every tick — going
        offline stops new buys but keeps protecting any open position."""
        self._conn.execute(
            "UPDATE portfolio_state SET trading_enabled = ?, updated_at = ? WHERE id = 1",
            (1 if enabled else 0, _now_iso()),
        )

    def get_caution_level(self) -> int:
        return self.get_portfolio_state().caution_level

    def set_caution_level(self, level: int) -> int:
        """Set by the dashboard's caution slider (and the matching CLI
        command). Clamped to [MIN_CAUTION_LEVEL, MAX_CAUTION_LEVEL] rather
        than rejected outright, so a stray out-of-range value from a client
        just lands at the nearest valid level instead of erroring. See
        engine.CAUTION_LEVEL_OFFSETS for what each level actually does —
        the ledger just persists the number, it doesn't interpret it.
        Returns the level actually stored (post-clamp)."""
        level = max(MIN_CAUTION_LEVEL, min(MAX_CAUTION_LEVEL, level))
        self._conn.execute(
            "UPDATE portfolio_state SET caution_level = ?, updated_at = ? WHERE id = 1",
            (level, _now_iso()),
        )
        return level

    # ------------------------------------------------------------ big risk

    def get_big_risk_state(self) -> BigRiskState:
        row = self._conn.execute(
            "SELECT big_risk_mode, big_risk_started_at, big_risk_position_id FROM portfolio_state WHERE id = 1"
        ).fetchone()
        return BigRiskState(
            mode=row["big_risk_mode"],
            started_at=_parse_dt(row["big_risk_started_at"]) if row["big_risk_started_at"] else None,
            position_id=row["big_risk_position_id"],
        )

    def start_big_risk_search(self) -> None:
        """Arms Big Risk mode: the engine now spends up to
        big_risk.search_window_seconds looking for one signal to go all-in
        on instead of its normal multi-source strategy. Call sites are
        expected to have already sold every open position first (the
        dashboard's BIG RISK button does this via liquidate_all)."""
        self._conn.execute(
            "UPDATE portfolio_state SET big_risk_mode = 'searching', big_risk_started_at = ?, "
            "big_risk_position_id = NULL, updated_at = ? WHERE id = 1",
            (_now_iso(), _now_iso()),
        )

    def set_big_risk_invested(self, position_id: int) -> None:
        self._conn.execute(
            "UPDATE portfolio_state SET big_risk_mode = 'invested', big_risk_position_id = ?, "
            "updated_at = ? WHERE id = 1",
            (position_id, _now_iso()),
        )

    def end_big_risk(self) -> None:
        """Back to idle -- the engine resumes its normal multi-source
        strategy on its very next tick. Called when the search window
        times out with no candidate, trading goes offline mid-search, or
        the all-in position fully closes for any reason."""
        self._conn.execute(
            "UPDATE portfolio_state SET big_risk_mode = 'idle', big_risk_started_at = NULL, "
            "big_risk_position_id = NULL, updated_at = ? WHERE id = 1",
            (_now_iso(),),
        )

    # ------------------------------------------------------------ positions

    def get_open_positions(self) -> list[Position]:
        rows = self._conn.execute("SELECT * FROM positions WHERE status = 'open' ORDER BY opened_at").fetchall()
        return [_row_to_position(r) for r in rows]

    def get_open_position_for_token(self, token_address: str) -> Position | None:
        row = self._conn.execute(
            "SELECT * FROM positions WHERE token_address = ? AND status = 'open' LIMIT 1",
            (token_address,),
        ).fetchone()
        return _row_to_position(row) if row else None

    def get_position_by_id(self, position_id: int) -> Position | None:
        """Looks up a position regardless of status -- unlike
        get_open_position_for_token, this is how the dashboard's per-position
        detail view (open or already closed) finds the one the user clicked
        on."""
        row = self._conn.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
        return _row_to_position(row) if row else None

    def is_token_on_cooldown(self, token_address: str, cooldown_minutes: float) -> bool:
        row = self._conn.execute(
            "SELECT closed_at FROM positions WHERE token_address = ? AND status = 'closed' "
            "ORDER BY closed_at DESC LIMIT 1",
            (token_address,),
        ).fetchone()
        if row is None or row["closed_at"] is None:
            return False
        closed_at = _parse_dt(row["closed_at"])
        elapsed_minutes = (datetime.now(timezone.utc) - closed_at).total_seconds() / 60.0
        return elapsed_minutes < cooldown_minutes

    def open_position(
        self,
        token_address: str,
        symbol: str,
        chain_id: str,
        fill: FillResult,
        signal: SocialSignal,
        entry_liquidity_usd: Decimal,
        mode: str,
    ) -> Position:
        state = self.get_portfolio_state()
        total_cost = fill.amount_usd + fill.fee_usd
        if total_cost > state.cash_usd:
            raise InsufficientCashError(
                f"need ${total_cost} but only ${state.cash_usd} cash available"
            )

        now = _now_iso()
        cur = self._conn.execute(
            """
            INSERT INTO positions (
                token_address, symbol, chain_id, entry_price_usd, quantity, original_quantity,
                cost_basis_usd, fees_paid_usd, peak_price_usd, entry_liquidity_usd,
                take_profit_taken, opened_at, signal_source, signal_score, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, 'open')
            """,
            (
                token_address,
                symbol,
                chain_id,
                str(fill.price_usd),
                str(fill.quantity),
                str(fill.quantity),
                str(total_cost),
                str(fill.fee_usd),
                str(fill.price_usd),
                str(entry_liquidity_usd),
                now,
                signal.source,
                signal.score,
            ),
        )
        position_id = cur.lastrowid

        new_cash = state.cash_usd - total_cost
        self._conn.execute(
            "UPDATE portfolio_state SET cash_usd = ?, updated_at = ? WHERE id = 1",
            (str(new_cash), now),
        )

        self._conn.execute(
            """
            INSERT INTO trades (position_id, token_address, symbol, side, price_usd, quantity,
                                 amount_usd, fee_usd, realized_pnl_usd, reason, executed_at, mode)
            VALUES (?, ?, ?, 'buy', ?, ?, ?, ?, NULL, 'signal_entry', ?, ?)
            """,
            (
                position_id,
                token_address,
                symbol,
                str(fill.price_usd),
                str(fill.quantity),
                str(fill.amount_usd),
                str(fill.fee_usd),
                now,
                mode,
            ),
        )

        row = self._conn.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
        return _row_to_position(row)

    def update_peak_price(self, position: Position, current_price_usd: Decimal) -> None:
        if current_price_usd > position.peak_price_usd:
            self._conn.execute(
                "UPDATE positions SET peak_price_usd = ? WHERE id = ?",
                (str(current_price_usd), position.id),
            )

    def apply_sell(
        self,
        position: Position,
        fraction: Decimal,
        fill: FillResult,
        reason: str,
        mark_take_profit_taken: bool,
        mode: str,
    ) -> Trade:
        """Sell `fraction` of the position's *current* remaining quantity.

        fill.quantity/amount_usd/fee_usd must correspond to that fraction
        (the executor computes the fill from fraction * position.quantity).
        """
        now = _now_iso()
        sold_qty = fill.quantity
        cost_basis_sold = (
            position.cost_basis_usd * (sold_qty / position.quantity)
            if position.quantity > 0
            else Decimal(0)
        )
        proceeds_net = fill.amount_usd - fill.fee_usd
        realized_pnl = proceeds_net - cost_basis_sold

        remaining_qty = position.quantity - sold_qty
        remaining_cost_basis = position.cost_basis_usd - cost_basis_sold
        is_fully_closed = remaining_qty <= Decimal("0.00000001")

        state = self.get_portfolio_state()
        new_cash = state.cash_usd + proceeds_net
        new_realized_pnl = state.realized_pnl_usd + realized_pnl

        self._conn.execute(
            "UPDATE portfolio_state SET cash_usd = ?, realized_pnl_usd = ?, updated_at = ? WHERE id = 1",
            (str(new_cash), str(new_realized_pnl), now),
        )

        if is_fully_closed:
            self._conn.execute(
                "UPDATE positions SET quantity = '0', cost_basis_usd = '0', status = 'closed', "
                "closed_at = ?, take_profit_taken = ? WHERE id = ?",
                (now, 1 if (position.take_profit_taken or mark_take_profit_taken) else 0, position.id),
            )
        else:
            self._conn.execute(
                "UPDATE positions SET quantity = ?, cost_basis_usd = ?, take_profit_taken = ? WHERE id = ?",
                (
                    str(remaining_qty),
                    str(remaining_cost_basis),
                    1 if (position.take_profit_taken or mark_take_profit_taken) else 0,
                    position.id,
                ),
            )

        cur = self._conn.execute(
            """
            INSERT INTO trades (position_id, token_address, symbol, side, price_usd, quantity,
                                 amount_usd, fee_usd, realized_pnl_usd, reason, executed_at, mode)
            VALUES (?, ?, ?, 'sell', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position.id,
                position.token_address,
                position.symbol,
                str(fill.price_usd),
                str(sold_qty),
                str(fill.amount_usd),
                str(fill.fee_usd),
                str(realized_pnl),
                reason,
                now,
                mode,
            ),
        )
        row = self._conn.execute("SELECT * FROM trades WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_trade(row)

    # ------------------------------------------------------------- signals

    def record_signal(self, signal: SocialSignal, acted_on: bool) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO signals_log (token_address, symbol, source, score, mention_count,
                                      raw_excerpt, received_at, acted_on)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal.token_address,
                signal.symbol,
                signal.source,
                signal.score,
                signal.mention_count,
                signal.excerpt,
                signal.observed_at.isoformat(),
                1 if acted_on else 0,
            ),
        )
        return cur.lastrowid

    # --------------------------------------------------------------- price

    def record_price_snapshot(
        self, token_address: str, price_usd: Decimal, liquidity_usd: Decimal, volume_24h_usd: Decimal
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO price_snapshots (token_address, price_usd, liquidity_usd, volume_24h_usd, captured_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (token_address, str(price_usd), str(liquidity_usd), str(volume_24h_usd), _now_iso()),
        )

    def get_latest_price(self, token_address: str) -> Decimal | None:
        row = self._conn.execute(
            "SELECT price_usd FROM price_snapshots WHERE token_address = ? "
            "ORDER BY captured_at DESC LIMIT 1",
            (token_address,),
        ).fetchone()
        return Decimal(row["price_usd"]) if row else None

    def get_latest_liquidity(self, token_address: str) -> Decimal | None:
        """The liquidity_usd from the most recent snapshot recorded *before*
        this call — used to detect a sudden single-interval liquidity drop
        by comparing against the fresh reading about to be recorded."""
        row = self._conn.execute(
            "SELECT liquidity_usd FROM price_snapshots WHERE token_address = ? "
            "ORDER BY captured_at DESC LIMIT 1",
            (token_address,),
        ).fetchone()
        return Decimal(row["liquidity_usd"]) if row and row["liquidity_usd"] is not None else None

    def get_liquidity_before(self, token_address: str, cutoff_iso: str) -> Decimal | None:
        """The liquidity_usd from the most recent snapshot at or before
        `cutoff_iso` -- used as the "previous" reading for the sudden-drop
        rug check instead of the literal last poll, so that check compares
        against a real window (e.g. 30s) rather than whatever
        position_check_interval_seconds happens to be (5s, tuned purely for
        dashboard responsiveness) -- a single 5s-apart comparison is prone
        to normal thin-pool price-impact noise reading as a "rug"."""
        row = self._conn.execute(
            "SELECT liquidity_usd FROM price_snapshots WHERE token_address = ? AND captured_at <= ? "
            "ORDER BY captured_at DESC LIMIT 1",
            (token_address, cutoff_iso),
        ).fetchone()
        return Decimal(row["liquidity_usd"]) if row and row["liquidity_usd"] is not None else None

    def get_price_history(self, token_address: str, limit: int = 200) -> list[Decimal]:
        rows = self._conn.execute(
            """
            SELECT price_usd FROM (
                SELECT price_usd, captured_at FROM price_snapshots
                WHERE token_address = ? ORDER BY captured_at DESC LIMIT ?
            ) ORDER BY captured_at ASC
            """,
            (token_address, limit),
        ).fetchall()
        return [Decimal(r["price_usd"]) for r in rows]

    def get_price_series_for_position(
        self, token_address: str, start_iso: str, end_iso: str | None = None, max_points: int = 300
    ) -> list[dict]:
        """Price snapshots spanning one position's lifetime -- from its entry
        to its exit (or now, if still open) -- for the dashboard's
        per-position detail chart. Downsampled the same way the equity curve
        is, so a long-lived position doesn't pull thousands of 5s snapshots
        into one response."""
        if end_iso is not None:
            rows = self._conn.execute(
                "SELECT price_usd, captured_at FROM price_snapshots "
                "WHERE token_address = ? AND captured_at >= ? AND captured_at <= ? ORDER BY captured_at ASC",
                (token_address, start_iso, end_iso),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT price_usd, captured_at FROM price_snapshots "
                "WHERE token_address = ? AND captured_at >= ? ORDER BY captured_at ASC",
                (token_address, start_iso),
            ).fetchall()
        rows = _downsample(rows, max_points)
        return [{"captured_at": r["captured_at"], "price_usd": float(Decimal(r["price_usd"]))} for r in rows]

    # -------------------------------------------------------------- equity

    def record_equity_snapshot(self, positions_value_usd: Decimal) -> None:
        cash = self.get_cash_usd()
        equity = cash + positions_value_usd
        self._conn.execute(
            """
            INSERT INTO equity_history (cash_usd, positions_value_usd, equity_usd, recorded_at)
            VALUES (?, ?, ?, ?)
            """,
            (str(cash), str(positions_value_usd), str(equity), _now_iso()),
        )

    def get_equity_curve(self, since_iso: str | None = None, max_points: int = 500) -> list[dict]:
        """Returns equity snapshots oldest-first, optionally restricted to
        `recorded_at >= since_iso` (e.g. for the dashboard's zoom-range
        selector). Downsampled to `max_points` evenly-spaced rows -- without
        this, a long-lived bot's "1 month" or "all time" range would pull
        hundreds of thousands of 5-second snapshots into one HTTP response.
        """
        if since_iso is not None:
            rows = self._conn.execute(
                """
                SELECT cash_usd, positions_value_usd, equity_usd, recorded_at
                FROM equity_history WHERE recorded_at >= ? ORDER BY recorded_at ASC
                """,
                (since_iso,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM (
                    SELECT cash_usd, positions_value_usd, equity_usd, recorded_at
                    FROM equity_history ORDER BY recorded_at DESC LIMIT ?
                ) ORDER BY recorded_at ASC
                """,
                (max_points * 20,),  # generous pre-filter cap before downsampling below
            ).fetchall()

        rows = _downsample(rows, max_points)
        return [
            {
                "recorded_at": r["recorded_at"],
                "cash_usd": float(Decimal(r["cash_usd"])),
                "positions_value_usd": float(Decimal(r["positions_value_usd"])),
                "equity_usd": float(Decimal(r["equity_usd"])),
            }
            for r in rows
        ]

    # --------------------------------------------------------------- trades

    def get_recent_trades(self, limit: int = 50) -> list[Trade]:
        rows = self._conn.execute(
            "SELECT * FROM trades ORDER BY executed_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_trade(r) for r in rows]

    def get_trades_for_position(self, position_id: int) -> list[Trade]:
        """Every fill (the entry buy, any partial take-profit sells, the
        final exit) that belongs to one position -- oldest first, for the
        dashboard's per-position detail view."""
        rows = self._conn.execute(
            "SELECT * FROM trades WHERE position_id = ? ORDER BY executed_at ASC", (position_id,)
        ).fetchall()
        return [_row_to_trade(r) for r in rows]

    def get_recent_signals(self, limit: int = 50) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM signals_log ORDER BY received_at DESC LIMIT ?", (limit,)
        ).fetchall()

    # ------------------------------------------------------------- ML data

    def save_trade_features(self, position_id: int, features: dict[str, float]) -> None:
        """Snapshots the entry-time feature vector for a position, so it can
        later be paired with that trade's eventual outcome for training."""
        self._conn.execute(
            "INSERT OR REPLACE INTO trade_features (position_id, features_json, captured_at) VALUES (?, ?, ?)",
            (position_id, json.dumps(features), _now_iso()),
        )

    def get_training_dataset(self) -> list[tuple[dict[str, float], int]]:
        """(features, label) pairs for every closed position with a saved
        feature snapshot. label=1 if the position's total realized P&L (across
        all its sells, partial take-profits included) was positive.

        The SQL-side float SUM here is only ever used to derive this binary
        label for training — it never touches the actual ledger balance.
        """
        rows = self._conn.execute(
            """
            SELECT tf.features_json AS features_json,
                   COALESCE(SUM(CASE WHEN t.side = 'sell' THEN CAST(t.realized_pnl_usd AS REAL) ELSE 0 END), 0)
                       AS total_pnl
            FROM trade_features tf
            JOIN positions p ON p.id = tf.position_id
            LEFT JOIN trades t ON t.position_id = tf.position_id
            WHERE p.status = 'closed'
            GROUP BY tf.position_id
            """
        ).fetchall()
        dataset = []
        for row in rows:
            features = json.loads(row["features_json"])
            label = 1 if row["total_pnl"] > 0 else 0
            dataset.append((features, label))
        return dataset

    def get_performance_by_source(self) -> list[dict]:
        """Realized P&L, win rate, and trade count per signal source (e.g.
        twitter_scraper, reddit, birdeye_trending), for every *closed*
        position -- the only way to actually tell which sources are worth
        keeping rather than guessing. Sources with zero closed trades don't
        appear; there's nothing to report yet."""
        rows = self._conn.execute(
            """
            SELECT p.signal_source AS source,
                   COUNT(*) AS closed_trades,
                   SUM(CASE WHEN t.total_pnl > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(t.total_pnl) AS total_pnl_usd
            FROM (
                SELECT position_id,
                       COALESCE(SUM(CASE WHEN side = 'sell' THEN CAST(realized_pnl_usd AS REAL) ELSE 0 END), 0)
                           AS total_pnl
                FROM trades
                GROUP BY position_id
            ) t
            JOIN positions p ON p.id = t.position_id
            WHERE p.status = 'closed'
            GROUP BY p.signal_source
            ORDER BY total_pnl_usd DESC
            """
        ).fetchall()
        return [
            {
                "source": row["source"] or "unknown",
                "closed_trades": row["closed_trades"],
                "wins": row["wins"],
                "win_rate_pct": (row["wins"] / row["closed_trades"] * 100) if row["closed_trades"] else 0.0,
                "total_pnl_usd": row["total_pnl_usd"],
                "avg_pnl_usd": row["total_pnl_usd"] / row["closed_trades"] if row["closed_trades"] else 0.0,
            }
            for row in rows
        ]
