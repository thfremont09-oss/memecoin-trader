"""Shared summary-building logic for the CLI `status` command and the dashboard."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from memecoin_trader.config import Settings
from memecoin_trader.market.dexscreener import DexScreenerClient
from memecoin_trader.portfolio.ledger import CAUTION_LEVEL_LABELS, Ledger

# The dashboard's equity-curve zoom presets, ordered zoomed-in to zoomed-out.
# "ytd"/"all" have no fixed timedelta -- see _range_since_iso().
EQUITY_CURVE_RANGES: list[str] = ["1min", "5m", "1h", "1d", "1w", "1m", "ytd", "all"]
_EQUITY_CURVE_DELTAS: dict[str, timedelta] = {
    "1min": timedelta(minutes=1),
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


def _mark_price(
    ledger: Ledger,
    token_address: str,
    fallback: Decimal,
    market_client: DexScreenerClient | None = None,
    chain_id: str | None = None,
) -> Decimal:
    """The price to show for a token right now.

    With `market_client` + `chain_id` given (the dashboard's live-refresh
    path), tries a quick, cached, no-retry DexScreener lookup first, so the
    displayed price tracks how often the *dashboard* is asked to refresh
    rather than only how often the engine's own position-check tick writes
    a new snapshot to the DB. Falls back to the last persisted snapshot (the
    CLI's `status` command, and the dashboard whenever the quick lookup
    comes back empty) exactly as before -- `market_client=None` is the
    default so nothing about existing callers changes.
    """
    if market_client is not None and chain_id is not None:
        try:
            live = market_client.get_best_pair_for_token(chain_id, token_address, quick=True)
        except Exception:
            live = None  # never let a flaky network call break a display refresh
        if live is not None:
            return live.price_usd
    price = ledger.get_latest_price(token_address)
    return price if price is not None else fallback


def build_summary(
    ledger: Ledger,
    settings: Settings,
    equity_range: str = "all",
    market_client: DexScreenerClient | None = None,
) -> dict:
    state = ledger.get_portfolio_state()
    open_positions = ledger.get_open_positions()

    positions_value = Decimal(0)
    position_rows = []
    for p in open_positions:
        price = _mark_price(ledger, p.token_address, p.entry_price_usd, market_client, settings.chain_id)
        value = p.quantity * price
        positions_value += value
        position_rows.append(
            {
                "id": p.id,
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
            "position_id": t.position_id,
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

    big_risk_state = ledger.get_big_risk_state()
    big_risk_position = None
    if big_risk_state.position_id is not None:
        bp = ledger.get_position_by_id(big_risk_state.position_id)
        if bp is not None:
            bp_price = _mark_price(ledger, bp.token_address, bp.entry_price_usd, market_client, settings.chain_id)
            big_risk_position = {
                "id": bp.id,
                "symbol": bp.symbol,
                "token_address": bp.token_address,
                "entry_price_usd": float(bp.entry_price_usd),
                "current_price_usd": float(bp_price),
                "unrealized_pnl_usd": float(bp.unrealized_pnl_usd(bp_price)),
                "unrealized_pnl_pct": bp.unrealized_pnl_pct(bp_price),
            }

    return {
        "trading_enabled": state.trading_enabled,
        "caution_level": state.caution_level,
        "caution_label": CAUTION_LEVEL_LABELS.get(state.caution_level, str(state.caution_level)),
        "big_risk": {
            "mode": big_risk_state.mode,
            "started_at": big_risk_state.started_at.isoformat() if big_risk_state.started_at else None,
            "search_window_seconds": settings.big_risk.search_window_seconds,
            "max_position_usd": settings.big_risk.max_position_usd,
            "position": big_risk_position,
        },
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


def build_position_detail(
    ledger: Ledger,
    position_id: int,
    market_client: DexScreenerClient | None = None,
    chain_id: str | None = None,
) -> dict | None:
    """Everything the dashboard's per-position detail view needs: entry vs.
    current/exit price, cost basis and fees, realized + unrealized P&L, the
    full trade history for this one position (entry buy, any partial
    take-profit sells, the final exit), and a price series spanning its
    lifetime for the chart. Returns None if no position with that id exists.
    """
    position = ledger.get_position_by_id(position_id)
    if position is None:
        return None

    trades = ledger.get_trades_for_position(position_id)
    is_open = position.status == "open"
    if is_open:
        current_price = _mark_price(ledger, position.token_address, position.entry_price_usd, market_client, chain_id)
    elif trades and trades[-1].side == "sell":
        current_price = trades[-1].price_usd  # the price it actually exited at
    else:
        current_price = position.entry_price_usd

    realized_pnl_usd = sum((t.realized_pnl_usd for t in trades if t.realized_pnl_usd is not None), Decimal(0))
    unrealized_pnl_usd = position.unrealized_pnl_usd(current_price) if is_open else Decimal(0)
    total_pnl_usd = realized_pnl_usd + unrealized_pnl_usd
    initial_cost_usd = position.original_quantity * position.entry_price_usd
    total_return_pct = float(total_pnl_usd / initial_cost_usd * 100) if initial_cost_usd else 0.0

    start_iso = position.opened_at.isoformat()
    end_iso = position.closed_at.isoformat() if position.closed_at else None
    price_series = ledger.get_price_series_for_position(position.token_address, start_iso, end_iso)

    return {
        "id": position.id,
        "token_address": position.token_address,
        "symbol": position.symbol,
        "status": position.status,
        "entry_price_usd": float(position.entry_price_usd),
        "current_price_usd": float(current_price),
        "original_quantity": float(position.original_quantity),
        "remaining_quantity": float(position.quantity),
        "cost_basis_usd": float(position.cost_basis_usd),
        "fees_paid_usd": float(position.fees_paid_usd),
        "peak_price_usd": float(position.peak_price_usd),
        "entry_liquidity_usd": float(position.entry_liquidity_usd),
        "take_profit_taken": position.take_profit_taken,
        "opened_at": position.opened_at.isoformat(),
        "closed_at": position.closed_at.isoformat() if position.closed_at else None,
        "signal_source": position.signal_source,
        "signal_score": position.signal_score,
        "realized_pnl_usd": float(realized_pnl_usd),
        "unrealized_pnl_usd": float(unrealized_pnl_usd),
        "total_pnl_usd": float(total_pnl_usd),
        "total_return_pct": total_return_pct,
        "trades": [
            {
                "id": t.id,
                "side": t.side,
                "price_usd": float(t.price_usd),
                "quantity": float(t.quantity),
                "amount_usd": float(t.amount_usd),
                "fee_usd": float(t.fee_usd),
                "realized_pnl_usd": float(t.realized_pnl_usd) if t.realized_pnl_usd is not None else None,
                "reason": t.reason,
                "executed_at": t.executed_at.isoformat(),
            }
            for t in trades
        ],
        "price_series": price_series,
    }
