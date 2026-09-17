from decimal import Decimal

from memecoin_trader.analysis.entry_strategy import EntryContext, evaluate_entry
from memecoin_trader.config import EntryConfig
from tests.conftest import make_pair, make_signal

CONFIG = EntryConfig(
    mention_score_threshold=55.0,
    min_liquidity_usd=5000,
    min_volume_24h_usd=10000,
    min_pair_age_minutes=5,
    max_pair_age_hours=72,
    max_open_positions=4,
    position_size_pct_of_cash=0.20,
    min_trade_usd=5.0,
    max_trade_usd=40.0,
)

DEFAULT_CTX = EntryContext(
    cash_usd=Decimal("100"),
    open_position_count=0,
    already_holds_token=False,
    token_on_cooldown=False,
)


def test_happy_path_buys_pct_of_cash():
    signal = make_signal(score=80)
    market = make_pair()
    decision = evaluate_entry(signal, market, DEFAULT_CTX, CONFIG)
    assert decision is not None
    assert decision.amount_usd == Decimal("20")  # 20% of $100


def test_rejects_low_score():
    signal = make_signal(score=10)
    market = make_pair()
    assert evaluate_entry(signal, market, DEFAULT_CTX, CONFIG) is None


def test_rejects_when_already_holding():
    signal = make_signal(score=80)
    market = make_pair()
    ctx = EntryContext(cash_usd=Decimal("100"), open_position_count=1, already_holds_token=True, token_on_cooldown=False)
    assert evaluate_entry(signal, market, ctx, CONFIG) is None


def test_rejects_when_on_cooldown():
    signal = make_signal(score=80)
    market = make_pair()
    ctx = EntryContext(cash_usd=Decimal("100"), open_position_count=0, already_holds_token=False, token_on_cooldown=True)
    assert evaluate_entry(signal, market, ctx, CONFIG) is None


def test_rejects_when_max_positions_reached():
    signal = make_signal(score=80)
    market = make_pair()
    ctx = EntryContext(cash_usd=Decimal("100"), open_position_count=4, already_holds_token=False, token_on_cooldown=False)
    assert evaluate_entry(signal, market, ctx, CONFIG) is None


def test_rejects_missing_market_data():
    signal = make_signal(score=80)
    assert evaluate_entry(signal, None, DEFAULT_CTX, CONFIG) is None


def test_rejects_low_liquidity():
    signal = make_signal(score=80)
    market = make_pair(liquidity_usd="100")
    assert evaluate_entry(signal, market, DEFAULT_CTX, CONFIG) is None


def test_rejects_low_volume():
    signal = make_signal(score=80)
    market = make_pair(volume_24h_usd="1")
    assert evaluate_entry(signal, market, DEFAULT_CTX, CONFIG) is None


def test_rejects_too_young_pair():
    signal = make_signal(score=80)
    market = make_pair(age_minutes=1)
    assert evaluate_entry(signal, market, DEFAULT_CTX, CONFIG) is None


def test_rejects_too_old_pair():
    signal = make_signal(score=80)
    market = make_pair(age_minutes=72 * 60 + 10)
    assert evaluate_entry(signal, market, DEFAULT_CTX, CONFIG) is None


def test_rejects_insufficient_cash():
    signal = make_signal(score=80)
    market = make_pair()
    ctx = EntryContext(cash_usd=Decimal("2"), open_position_count=0, already_holds_token=False, token_on_cooldown=False)
    assert evaluate_entry(signal, market, ctx, CONFIG) is None


def test_amount_is_capped_at_max_trade_usd():
    signal = make_signal(score=80)
    market = make_pair()
    ctx = EntryContext(cash_usd=Decimal("1000"), open_position_count=0, already_holds_token=False, token_on_cooldown=False)
    decision = evaluate_entry(signal, market, ctx, CONFIG)
    assert decision.amount_usd == Decimal("40")


def test_amount_is_floored_at_min_trade_usd():
    signal = make_signal(score=80)
    market = make_pair()
    ctx = EntryContext(cash_usd=Decimal("10"), open_position_count=0, already_holds_token=False, token_on_cooldown=False)
    decision = evaluate_entry(signal, market, ctx, CONFIG)
    # 20% of $10 = $2, floored up to min_trade_usd = $5, but capped at available cash
    assert decision.amount_usd == Decimal("5")
