from datetime import datetime, timedelta, timezone
from decimal import Decimal

from memecoin_trader.analysis.exit_strategy import evaluate_exit
from memecoin_trader.config import ExitConfig
from memecoin_trader.portfolio.models import Position

CONFIG = ExitConfig(
    stop_loss_pct=0.25,
    take_profit_pct=0.50,
    take_profit_sell_fraction=0.5,
    trailing_stop_pct=0.20,
    max_hold_minutes=240,
    liquidity_rug_fraction=0.4,
    sudden_liquidity_drop_pct=0.5,
)


def make_position(
    entry_price="1.0",
    quantity="100",
    peak_price=None,
    entry_liquidity="20000",
    take_profit_taken=False,
    opened_minutes_ago=10,
) -> Position:
    return Position(
        id=1,
        token_address="TOKEN1",
        symbol="MEME",
        chain_id="solana",
        entry_price_usd=Decimal(entry_price),
        quantity=Decimal(quantity),
        original_quantity=Decimal(quantity),
        cost_basis_usd=Decimal(entry_price) * Decimal(quantity),
        fees_paid_usd=Decimal("0"),
        peak_price_usd=Decimal(peak_price if peak_price is not None else entry_price),
        entry_liquidity_usd=Decimal(entry_liquidity),
        take_profit_taken=take_profit_taken,
        opened_at=datetime.now(timezone.utc) - timedelta(minutes=opened_minutes_ago),
        signal_source="twitter_mock",
        signal_score=80.0,
        status="open",
    )


def test_no_exit_when_flat():
    position = make_position()
    decision = evaluate_exit(position, Decimal("1.0"), Decimal("20000"), CONFIG)
    assert decision is None


def test_stop_loss_triggers_full_exit():
    position = make_position(entry_price="1.0")
    decision = evaluate_exit(position, Decimal("0.74"), Decimal("20000"), CONFIG)
    assert decision is not None
    assert decision.reason == "stop_loss"
    assert decision.fraction == Decimal(1)


def test_take_profit_triggers_partial_exit_once():
    position = make_position(entry_price="1.0")
    decision = evaluate_exit(position, Decimal("1.51"), Decimal("20000"), CONFIG)
    assert decision is not None
    assert decision.reason == "take_profit_partial"
    assert decision.fraction == Decimal("0.5")
    assert decision.mark_take_profit_taken is True


def test_take_profit_does_not_retrigger_once_taken():
    position = make_position(entry_price="1.0", take_profit_taken=True, peak_price="1.6")
    decision = evaluate_exit(position, Decimal("1.55"), Decimal("20000"), CONFIG)
    # not a fresh take-profit trigger; not far enough from peak (1.6) for trailing stop (20%) either
    assert decision is None


def test_trailing_stop_triggers_after_drop_from_peak():
    position = make_position(entry_price="1.0", peak_price="2.0", take_profit_taken=True)
    decision = evaluate_exit(position, Decimal("1.5"), Decimal("20000"), CONFIG)  # 25% off peak
    assert decision is not None
    assert decision.reason == "trailing_stop"
    assert decision.fraction == Decimal(1)


def test_trailing_stop_does_not_trigger_if_never_profitable():
    position = make_position(entry_price="1.0", peak_price="1.0")
    decision = evaluate_exit(position, Decimal("0.9"), Decimal("20000"), CONFIG)
    assert decision is None  # 10% loss, below stop-loss threshold, no peak to trail from


def test_time_exit_after_max_hold():
    position = make_position(entry_price="1.0", opened_minutes_ago=241)
    decision = evaluate_exit(position, Decimal("1.0"), Decimal("20000"), CONFIG)
    assert decision is not None
    assert decision.reason == "time_exit"


def test_liquidity_rug_triggers_emergency_exit():
    position = make_position(entry_price="1.0", entry_liquidity="20000")
    decision = evaluate_exit(position, Decimal("1.0"), Decimal("5000"), CONFIG)  # liquidity fell to 25%
    assert decision is not None
    assert decision.reason == "liquidity_rug"
    assert decision.fraction == Decimal(1)


def test_liquidity_rug_takes_priority_over_take_profit():
    position = make_position(entry_price="1.0", entry_liquidity="20000")
    decision = evaluate_exit(position, Decimal("2.0"), Decimal("5000"), CONFIG)
    assert decision.reason == "liquidity_rug"


def test_sudden_liquidity_drop_triggers_before_entry_relative_floor():
    # still well above the entry-relative liquidity_rug_fraction floor (40% of 20000 = 8000),
    # but liquidity just halved between two consecutive checks -- catch it immediately.
    position = make_position(entry_price="1.0", entry_liquidity="20000")
    decision = evaluate_exit(
        position, Decimal("1.0"), Decimal("9000"), CONFIG, previous_liquidity_usd=Decimal("20000")
    )
    assert decision is not None
    assert decision.reason == "liquidity_rug_sudden"
    assert decision.fraction == Decimal(1)


def test_no_sudden_drop_exit_for_gradual_decline():
    position = make_position(entry_price="1.0", entry_liquidity="20000")
    decision = evaluate_exit(
        position, Decimal("1.0"), Decimal("19000"), CONFIG, previous_liquidity_usd=Decimal("20000")
    )
    assert decision is None  # only a 5% drop, well under the 50% sudden-drop threshold


def test_sudden_drop_ignored_without_a_previous_snapshot():
    position = make_position(entry_price="1.0", entry_liquidity="20000")
    decision = evaluate_exit(position, Decimal("1.0"), Decimal("9000"), CONFIG, previous_liquidity_usd=None)
    assert decision is None  # nothing to compare against yet (first check on this position)
