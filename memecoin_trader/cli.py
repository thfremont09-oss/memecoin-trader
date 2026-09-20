from __future__ import annotations

import argparse
import logging
import sys
from decimal import Decimal

from memecoin_trader.config import DB_PATH, MODEL_PATH, load_settings
from memecoin_trader.logging_setup import setup_logging
from memecoin_trader.portfolio.db import get_connection, init_db, reset_db
from memecoin_trader.portfolio.ledger import Ledger
from memecoin_trader.reporting import build_summary

logger = logging.getLogger(__name__)


def cmd_run(args: argparse.Namespace) -> int:
    from memecoin_trader.engine import create_engine

    setup_logging()
    settings = load_settings()

    if args.live:
        settings = _force_live_mode(settings)

    if settings.is_live:
        print("=" * 70)
        print(" LIVE TRADING MODE — this will spend real SOL on real swaps.")
        print("=" * 70)
        if not settings.secrets.live_trading_confirmed:
            print("Refusing to start: I_UNDERSTAND_LIVE_TRADING_RISK is not set in .env.")
            return 1

    engine = create_engine(settings)
    try:
        engine.run_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    return 0


def _force_live_mode(settings):
    from dataclasses import replace

    return replace(settings, mode="live")


def cmd_status(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = get_connection(DB_PATH)
    init_db(conn, Decimal(str(settings.starting_balance_usd)))
    ledger = Ledger(conn)
    summary = build_summary(ledger)

    print(f"Mode:              {settings.mode}")
    print(f"Starting balance:  ${summary['starting_balance_usd']:.2f}")
    print(f"Cash:              ${summary['cash_usd']:.2f}")
    print(f"Open positions val:${summary['positions_value_usd']:.2f}")
    print(f"Equity:            ${summary['equity_usd']:.2f}")
    print(
        f"Total return:      ${summary['total_return_usd']:+.2f} "
        f"({summary['total_return_pct']:+.1f}%)"
    )
    print(f"Realized P&L:      ${summary['realized_pnl_usd']:+.2f}")

    if summary["open_positions"]:
        print("\nOpen positions:")
        for p in summary["open_positions"]:
            print(
                f"  {p['symbol']:<10} qty={p['quantity']:<14.4f} "
                f"entry=${p['entry_price_usd']:<10.6f} now=${p['current_price_usd']:<10.6f} "
                f"pnl=${p['unrealized_pnl_usd']:+.2f} ({p['unrealized_pnl_pct']:+.1f}%)"
            )
    else:
        print("\nNo open positions.")

    if summary["recent_trades"]:
        print("\nRecent trades:")
        for t in summary["recent_trades"][:10]:
            pnl = f" pnl=${t['realized_pnl_usd']:+.2f}" if t["realized_pnl_usd"] is not None else ""
            print(
                f"  {t['executed_at']}  {t['side']:<4} {t['symbol']:<10} "
                f"${t['amount_usd']:.2f} @ ${t['price_usd']:.6f}  [{t['reason']}]{pnl}"
            )

    if summary["performance_by_source"]:
        print("\nPerformance by signal source (closed trades only):")
        for row in summary["performance_by_source"]:
            print(
                f"  {row['source']:<20} trades={row['closed_trades']:<5} "
                f"win_rate={row['win_rate_pct']:>5.1f}%  "
                f"total_pnl=${row['total_pnl_usd']:+.2f}  avg_pnl=${row['avg_pnl_usd']:+.2f}"
            )
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    import uvicorn

    setup_logging()
    print(f"Starting dashboard at http://{args.host}:{args.port} (Ctrl+C to stop)")
    uvicorn.run("memecoin_trader.dashboard.server:app", host=args.host, port=args.port, log_level="info")
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    from memecoin_trader.ml.model import TradeQualityModel

    settings = load_settings()
    conn = get_connection(DB_PATH)
    init_db(conn, Decimal(str(settings.starting_balance_usd)))
    ledger = Ledger(conn)

    dataset = ledger.get_training_dataset()
    min_required = settings.entry.ml.min_training_trades
    if len(dataset) < min_required:
        print(
            f"Only {len(dataset)} closed, feature-tagged trade(s) so far — "
            f"need at least {min_required} (entry.ml.min_training_trades in config.yaml) before training."
        )
        print("Keep the bot running; features are captured automatically on every buy.")
        return 1

    features, labels = zip(*dataset)
    profitable = sum(labels)
    print(f"Training on {len(dataset)} closed trades ({profitable} profitable, {len(dataset) - profitable} not)...")

    model = TradeQualityModel()
    try:
        stats = model.train(list(features), list(labels))
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return 1
    except ValueError as exc:
        print(f"Can't train yet: {exc}")
        return 1

    model.save(MODEL_PATH)
    print(f"Model saved to {MODEL_PATH}")
    print(f"Accuracy on held-out data: {stats['accuracy']:.1%} (n={stats['n_samples']})")
    if not settings.entry.ml.enabled:
        print("Note: entry.ml.enabled is false in config.yaml — set it to true to actually use this model.")
    return 0


def cmd_liquidate(args: argparse.Namespace) -> int:
    """Sells every open position right now, at current market price.

    A deliberate, on-demand safety valve for "I'm about to go offline and
    want to be in cash" — run this yourself before stepping away. It is
    never triggered automatically (not on shutdown, not on restart): a
    background task cannot reliably catch a PC shutdown or a scheduled-task
    stop to run this in time anyway, and wiring it into routine
    restarts/updates would sell everything on every `update.ps1` run.
    """
    from memecoin_trader.engine import create_engine

    setup_logging()
    settings = load_settings()
    engine = create_engine(settings)

    open_positions = engine.ledger.get_open_positions()
    if not open_positions:
        print("No open positions — nothing to liquidate.")
        return 0

    print(f"About to sell {len(open_positions)} open position(s) at current market price:")
    for p in open_positions:
        print(f"  {p.symbol} ({p.token_address}) qty={p.quantity}")

    if not args.yes:
        answer = input("Type 'yes' to liquidate all of the above now: ")
        if answer.strip().lower() != "yes":
            print("aborted.")
            return 1

    closed = engine.liquidate_all()
    print(f"Liquidated {closed}/{len(open_positions)} position(s).")
    if closed < len(open_positions):
        print("Some positions couldn't be sold (see logs) — likely missing market data right now. Try again shortly.")
        return 1
    return 0


def cmd_sell(args: argparse.Namespace) -> int:
    """Sells one open position right now, at current market price."""
    from memecoin_trader.engine import create_engine

    setup_logging()
    settings = load_settings()
    engine = create_engine(settings)

    position = engine.ledger.get_open_position_for_token(args.token_address)
    if position is None:
        print(f"No open position for {args.token_address}.")
        return 1

    if not args.yes:
        answer = input(f"Sell {position.symbol} ({args.token_address}) qty={position.quantity} now? Type 'yes': ")
        if answer.strip().lower() != "yes":
            print("aborted.")
            return 1

    if engine.liquidate_position(args.token_address):
        print(f"Sold {position.symbol}.")
        return 0
    print("Couldn't sell — no market data available right now. Try again shortly.")
    return 1


def cmd_offline(args: argparse.Namespace) -> int:
    """Sells everything, then stops the engine from opening new positions.

    The engine process itself keeps running (so it can still protect any
    position that couldn't be sold, and so `online` can flip it back) — this
    is a "stop trading" switch, not a process kill.
    """
    from memecoin_trader.engine import create_engine

    setup_logging()
    settings = load_settings()
    engine = create_engine(settings)

    open_positions = engine.ledger.get_open_positions()
    if open_positions:
        print(f"About to sell {len(open_positions)} open position(s) at current market price:")
        for p in open_positions:
            print(f"  {p.symbol} ({p.token_address}) qty={p.quantity}")
        if not args.yes:
            answer = input("Type 'yes' to sell all of the above and go offline: ")
            if answer.strip().lower() != "yes":
                print("aborted.")
                return 1

    closed = engine.liquidate_all() if open_positions else 0
    engine.ledger.set_trading_enabled(False)
    print(f"Offline. Sold {closed}/{len(open_positions)} position(s). No new trades until `online`.")
    if closed < len(open_positions):
        print("Some positions couldn't be sold (see logs) — likely missing market data right now.")
        return 1
    return 0


def cmd_online(args: argparse.Namespace) -> int:
    settings = load_settings()
    conn = get_connection(DB_PATH)
    init_db(conn, Decimal(str(settings.starting_balance_usd)))
    Ledger(conn).set_trading_enabled(True)
    print("Online. The engine will resume looking for new trades on its next check.")
    return 0


def cmd_caution(args: argparse.Namespace) -> int:
    from memecoin_trader.portfolio.ledger import CAUTION_LEVEL_LABELS

    settings = load_settings()
    conn = get_connection(DB_PATH)
    init_db(conn, Decimal(str(settings.starting_balance_usd)))
    ledger = Ledger(conn)

    if args.level is None:
        level = ledger.get_caution_level()
        print(f"Current caution level: {level} ({CAUTION_LEVEL_LABELS.get(level, level)})")
        print("Levels: " + ", ".join(f"{lvl}={label}" for lvl, label in CAUTION_LEVEL_LABELS.items()))
        return 0

    stored = ledger.set_caution_level(args.level)
    print(f"Caution level set to {stored} ({CAUTION_LEVEL_LABELS.get(stored, stored)}).")
    if stored != args.level:
        print(f"(clamped from {args.level} — valid range is 1-5)")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    settings = load_settings()
    if not args.yes:
        answer = input(
            f"This will permanently delete all simulated trade history and reset cash to "
            f"${settings.starting_balance_usd:.2f}. Type 'yes' to continue: "
        )
        if answer.strip().lower() != "yes":
            print("aborted.")
            return 1
    conn = get_connection(DB_PATH)
    reset_db(conn, Decimal(str(settings.starting_balance_usd)))
    print(f"reset complete. cash = ${settings.starting_balance_usd:.2f}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="memecoin-trader", description="Memecoin FOMO trading bot (simulation-first).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="start the trading engine loop")
    p_run.add_argument(
        "--live", action="store_true", help="force live mode (still requires .env safety flags)"
    )
    p_run.set_defaults(func=cmd_run)

    p_status = sub.add_parser("status", help="print current balance, positions and P&L")
    p_status.set_defaults(func=cmd_status)

    p_dash = sub.add_parser("dashboard", help="serve the local web dashboard")
    p_dash.add_argument("--host", default="127.0.0.1")
    p_dash.add_argument("--port", type=int, default=8787)
    p_dash.set_defaults(func=cmd_dashboard)

    p_reset = sub.add_parser("reset", help="wipe simulation data and restart from the starting balance")
    p_reset.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p_reset.set_defaults(func=cmd_reset)

    p_liquidate = sub.add_parser(
        "liquidate", help="sell all open positions immediately at current market price"
    )
    p_liquidate.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p_liquidate.set_defaults(func=cmd_liquidate)

    p_sell = sub.add_parser("sell", help="sell one open position immediately at current market price")
    p_sell.add_argument("token_address")
    p_sell.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p_sell.set_defaults(func=cmd_sell)

    p_offline = sub.add_parser(
        "offline", help="sell everything and stop the engine from opening new positions"
    )
    p_offline.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    p_offline.set_defaults(func=cmd_offline)

    p_online = sub.add_parser("online", help="let the engine open new positions again")
    p_online.set_defaults(func=cmd_online)

    p_caution = sub.add_parser(
        "caution", help="show or set the caution level (1-5) controlling how often the bot buys"
    )
    p_caution.add_argument(
        "level", type=int, nargs="?", default=None, help="1 (very cautious) to 5 (aggressive); omit to just show the current level"
    )
    p_caution.set_defaults(func=cmd_caution)

    p_train = sub.add_parser(
        "train", help="train the ML trade-quality model from the bot's own closed-trade history"
    )
    p_train.set_defaults(func=cmd_train)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
