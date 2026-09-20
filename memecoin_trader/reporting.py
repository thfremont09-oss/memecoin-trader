"""Shared summary-building logic for the CLI `status` command and the dashboard."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from memecoin_trader.portfolio.ledger import CAUTION_LEVEL_LABELS, Ledger

# The dashboard's equity-curve zoom presets, ordered zoomed-in to zoomed-out.
# "ytd"/"all" have no fixed timedelta -- see _range_since_iso().
EQUITY_CURVE_RANGES: list[str] = ["5m", "1h", "1d", "1w", "1m", "ytd", "all"]
_EQUITY_CURVE_DELTAS: dict[str, timedelta] = {
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
    "1d": timedelta(days=1),
    "1w": timedelta(days=7),
    "1m": timedelta(days=30),
}


def _range_since_iso(equity_range: str) -> str | None:
    now = datetime.now(timezone.utc)
    delta = _EQUITY_CURVE_DELTAS.get(equity_range)
    if delta is not None:
        return (now - delta).isoformat()
    if equity_range == "ytd":
        return datetime(now.year, 1, 1, tzinfo=timezone.utc).isoformat()
    return None  # "all", or an unrecognized range -- no filter


def _mark_price(ledger: Ledger, token_address: str, fallback: Decimal) -> Decimal:
    price = ledger.get_latest_price(token_address)
    return price if price is not None else fallback


def build_summary(ledger: Ledger, equity_range: str = "all") -> dict:
    state = ledger.get_portfolio_state()
    open_positions = ledger.get_open_positions()

    positions_value = Decimal(0)
    position_rows = []
    for p in open_positions:
        price = _mark_price(ledger, p.token_address, p.entry_price_usd)
        value = p.quantity * price
        positions_value += value
        position_rows.append(
            {
                "token_address": p.token_address,
                "symbol": p.symbol,
                "entry_price_usd": float(p.entry_price_usd),
                "current_price_usd": float(price),
                "quantity": float(p.quantity),
                "value_usd": float(value),
                "unrealized_pnl_usd": float(p.unrealized_pnl_usd(price)),
                "unrealized_pnl_pct": p.unrealized_pnl_pct(price),
                "peak_price_usd": float(p.peak_price_usd),
                "opened_at": p.opened_at.isoformat(),
                "signal_source": p.signal_source,
                "signal_score": p.signal_score,
                "take_profit_taken": p.take_profit_taken,
            }
        )

    equity = state.cash_usd + positions_value
    total_return_usd = equity - state.starting_balance_usd
    total_return_pct = (
        float(total_return_usd / state.starting_balance_usd * 100) if state.starting_balance_usd else 0.0
    )

    recent_trades = [
        {
            "id": t.id,
            "token_address": t.token_address,
            "symbol": t.symbol,
            "side": t.side,
            "price_usd": float(t.price_usd),
            "quantity": float(t.quantity),
            "amount_usd": float(t.amount_usd),
            "fee_usd": float(t.fee_usd),
            "realized_pnl_usd": float(t.realized_pnl_usd) if t.realized_pnl_usd is not None else None,
            "reason": t.reason,
            "executed_at": t.executed_at.isoformat(),
            "mode": t.mode,
        }
        for t in ledger.get_recent_trades(limit=50)
    ]

    return {
        "trading_enabled": state.trading_enabled,
        "caution_level": state.caution_level,
        "caution_label": CAUTION_LEVEL_LABELS.get(state.caution_level, str(state.caution_level)),
        "starting_balance_usd": float(state.starting_balance_usd),
        "cash_usd": float(state.cash_usd),
        "positions_value_usd": float(positions_value),
        "equity_usd": float(equity),
        "realized_pnl_usd": float(state.realized_pnl_usd),
        "total_return_usd": float(total_return_usd),
        "total_return_pct": total_return_pct,
        "open_positions": position_rows,
        "recent_trades": recent_trades,
        "equity_range": equity_range if equity_range in EQUITY_CURVE_RANGES else "all",
        "equity_curve": ledger.get_equity_curve(since_iso=_range_since_iso(equity_range)),
        "performance_by_source": ledger.get_performance_by_source(),
    }
