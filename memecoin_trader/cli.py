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
