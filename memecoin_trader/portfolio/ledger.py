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
from memecoin_trader.portfolio.models import PortfolioState, Position, Trade
from memecoin_trader.signals.base import SocialSignal


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


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
        )

    def get_cash_usd(self) -> Decimal:
        return self.get_portfolio_state().cash_usd

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

    def get_equity_curve(self, limit: int = 1000) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT * FROM (
                SELECT cash_usd, positions_value_usd, equity_usd, recorded_at
                FROM equity_history ORDER BY recorded_at DESC LIMIT ?
            ) ORDER BY recorded_at ASC
            """,
            (limit,),
        ).fetchall()
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
