"""Shared summary-building logic for the CLI `status` command and the dashboard."""
from __future__ import annotations

from decimal import Decimal

from memecoin_trader.portfolio.ledger import Ledger


def _mark_price(ledger: Ledger, token_address: str, fallback: Decimal) -> Decimal:
    price = ledger.get_latest_price(token_address)
    return price if price is not None else fallback


def build_summary(ledger: Ledger) -> dict:
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
        "starting_balance_usd": float(state.starting_balance_usd),
        "cash_usd": float(state.cash_usd),
        "positions_value_usd": float(positions_value),
        "equity_usd": float(equity),
        "realized_pnl_usd": float(state.realized_pnl_usd),
        "total_return_usd": float(total_return_usd),
        "total_return_pct": total_return_pct,
        "open_positions": position_rows,
        "recent_trades": recent_trades,
        "equity_curve": ledger.get_equity_curve(limit=1000),
        "performance_by_source": ledger.get_performance_by_source(),
    }
