from dataclasses import replace
from decimal import Decimal

from memecoin_trader.analysis.entry_strategy import EntryContext, effective_score, evaluate_entry
from memecoin_trader.config import EntryConfig, MlConfig, RugCheckConfig
from tests.conftest import make_pair, make_rug_report, make_signal

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
    min_liquidity_to_fdv_pct=0.0,
    max_price_change_5m_pct=100000.0,
    min_buy_sell_ratio=0.0,
    corroboration_bonus_score=15.0,
    corroboration_window_minutes=30.0,
    rug_check=RugCheckConfig(
        enabled=False, fail_closed=True, max_danger_flags=0, max_warning_flags=4, min_lp_locked_pct=50.0
    ),
    ml=MlConfig(enabled=False, min_confidence=0.55, min_training_trades=30, retrain_check_interval_minutes=60),
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


def test_rejects_thin_liquidity_relative_to_fdv():
    config = replace(CONFIG, min_liquidity_to_fdv_pct=10.0)
    signal = make_signal(score=80)
    # liquidity $20,000 on a $1,000,000 fdv = 2%, below the 10% floor
    market = make_pair(liquidity_usd="20000", fdv_usd="1000000")
    assert evaluate_entry(signal, market, DEFAULT_CTX, config) is None


def test_allows_healthy_liquidity_relative_to_fdv():
    config = replace(CONFIG, min_liquidity_to_fdv_pct=10.0)
    signal = make_signal(score=80)
    market = make_pair(liquidity_usd="200000", fdv_usd="1000000")  # 20%
    assert evaluate_entry(signal, market, DEFAULT_CTX, config) is not None


def test_unknown_fdv_does_not_block_trade():
    config = replace(CONFIG, min_liquidity_to_fdv_pct=10.0)
    signal = make_signal(score=80)
    market = make_pair(fdv_usd=None)
    assert evaluate_entry(signal, market, DEFAULT_CTX, config) is not None


def test_rejects_token_that_already_spiked():
    config = replace(CONFIG, max_price_change_5m_pct=100.0)
    signal = make_signal(score=80)
    market = make_pair(price_change_5m_pct=250.0)
    assert evaluate_entry(signal, market, DEFAULT_CTX, config) is None


def test_rug_check_disabled_ignores_danger_flags():
    signal = make_signal(score=80)
    market = make_pair()
    report = make_rug_report(danger_flags=("Mint authority not renounced",))
    assert evaluate_entry(signal, market, DEFAULT_CTX, CONFIG, rug_report=report) is not None


def test_rug_check_blocks_danger_flags_when_enabled():
    config = replace(CONFIG, rug_check=replace(CONFIG.rug_check, enabled=True))
    signal = make_signal(score=80)
    market = make_pair()
    report = make_rug_report(danger_flags=("Mint authority not renounced",))
    assert evaluate_entry(signal, market, DEFAULT_CTX, config, rug_report=report) is None


def test_rug_check_allows_clean_report_when_enabled():
    config = replace(CONFIG, rug_check=replace(CONFIG.rug_check, enabled=True))
    signal = make_signal(score=80)
    market = make_pair()
    report = make_rug_report(danger_flags=(), lp_locked_pct=95.0)
    assert evaluate_entry(signal, market, DEFAULT_CTX, config, rug_report=report) is not None


def test_rug_check_blocks_low_lp_locked_pct():
    config = replace(CONFIG, rug_check=replace(CONFIG.rug_check, enabled=True, min_lp_locked_pct=50.0))
    signal = make_signal(score=80)
    market = make_pair()
    report = make_rug_report(danger_flags=(), lp_locked_pct=10.0)
    assert evaluate_entry(signal, market, DEFAULT_CTX, config, rug_report=report) is None


def test_rug_check_fail_closed_blocks_when_report_unavailable():
    config = replace(CONFIG, rug_check=replace(CONFIG.rug_check, enabled=True, fail_closed=True))
    signal = make_signal(score=80)
    market = make_pair()
    assert evaluate_entry(signal, market, DEFAULT_CTX, config, rug_report=None) is None


def test_rug_check_fail_open_allows_when_report_unavailable():
    config = replace(CONFIG, rug_check=replace(CONFIG.rug_check, enabled=True, fail_closed=False))
    signal = make_signal(score=80)
    market = make_pair()
    assert evaluate_entry(signal, market, DEFAULT_CTX, config, rug_report=None) is not None


def test_rug_check_blocks_too_many_warning_flags():
    config = replace(CONFIG, rug_check=replace(CONFIG.rug_check, enabled=True, max_warning_flags=2))
    signal = make_signal(score=80)
    market = make_pair()
    report = make_rug_report(danger_flags=(), warning_flags=("a", "b", "c"))
    assert evaluate_entry(signal, market, DEFAULT_CTX, config, rug_report=report) is None


def test_rug_check_allows_warning_flags_within_limit():
    config = replace(CONFIG, rug_check=replace(CONFIG.rug_check, enabled=True, max_warning_flags=2))
    signal = make_signal(score=80)
    market = make_pair()
    report = make_rug_report(danger_flags=(), warning_flags=("a", "b"))
    assert evaluate_entry(signal, market, DEFAULT_CTX, config, rug_report=report) is not None


def test_rug_check_blocks_unrenounced_mint_authority_even_without_danger_flag():
    config = replace(CONFIG, rug_check=replace(CONFIG.rug_check, enabled=True))
    signal = make_signal(score=80)
    market = make_pair()
    report = replace(make_rug_report(danger_flags=()), mint_authority_renounced=False)
    assert evaluate_entry(signal, market, DEFAULT_CTX, config, rug_report=report) is None


def test_rug_check_blocks_active_freeze_authority_even_without_danger_flag():
    config = replace(CONFIG, rug_check=replace(CONFIG.rug_check, enabled=True))
    signal = make_signal(score=80)
    market = make_pair()
    report = replace(make_rug_report(danger_flags=()), freeze_authority_renounced=False)
    assert evaluate_entry(signal, market, DEFAULT_CTX, config, rug_report=report) is None


def test_rug_check_allows_unknown_mint_freeze_authority_status():
    config = replace(CONFIG, rug_check=replace(CONFIG.rug_check, enabled=True))
    signal = make_signal(score=80)
    market = make_pair()
    report = replace(
        make_rug_report(danger_flags=()), mint_authority_renounced=None, freeze_authority_renounced=None
    )
    assert evaluate_entry(signal, market, DEFAULT_CTX, config, rug_report=report) is not None


def test_ml_gate_blocks_low_confidence_when_enabled():
    config = replace(CONFIG, ml=replace(CONFIG.ml, enabled=True, min_confidence=0.6))
    signal = make_signal(score=80)
    market = make_pair()
    assert evaluate_entry(signal, market, DEFAULT_CTX, config, ml_confidence=0.4) is None


def test_ml_gate_allows_high_confidence_when_enabled():
    config = replace(CONFIG, ml=replace(CONFIG.ml, enabled=True, min_confidence=0.6))
    signal = make_signal(score=80)
    market = make_pair()
    assert evaluate_entry(signal, market, DEFAULT_CTX, config, ml_confidence=0.9) is not None


def test_ml_gate_is_a_noop_without_a_trained_model():
    # enabled=True but no model exists yet -> engine passes ml_confidence=None
    config = replace(CONFIG, ml=replace(CONFIG.ml, enabled=True, min_confidence=0.6))
    signal = make_signal(score=80)
    market = make_pair()
    assert evaluate_entry(signal, market, DEFAULT_CTX, config, ml_confidence=None) is not None


def test_rejects_more_sells_than_buys():
    config = replace(CONFIG, min_buy_sell_ratio=1.0)
    signal = make_signal(score=80)
    market = make_pair(buys_24h=5, sells_24h=20)  # heavy sell pressure
    assert evaluate_entry(signal, market, DEFAULT_CTX, config) is None


def test_allows_healthy_buy_sell_ratio():
    config = replace(CONFIG, min_buy_sell_ratio=1.0)
    signal = make_signal(score=80)
    market = make_pair(buys_24h=50, sells_24h=10)
    assert evaluate_entry(signal, market, DEFAULT_CTX, config) is not None


def test_effective_score_unboosted_with_fewer_than_two_sources():
    signal = make_signal(score=40)
    assert effective_score(signal, corroborating_sources=1, config=CONFIG) == 40
    assert effective_score(signal, corroborating_sources=0, config=CONFIG) == 40


def test_effective_score_boosted_with_two_or_more_sources():
    signal = make_signal(score=40)
    assert effective_score(signal, corroborating_sources=2, config=CONFIG) == 55  # +15 bonus


def test_effective_score_boost_caps_at_100():
    signal = make_signal(score=95)
    assert effective_score(signal, corroborating_sources=2, config=CONFIG) == 100


def test_corroboration_lets_a_below_threshold_signal_through():
    signal = make_signal(score=45)  # below CONFIG.mention_score_threshold (55) alone
    market = make_pair()
    assert evaluate_entry(signal, market, DEFAULT_CTX, CONFIG, corroborating_sources=1) is None
    assert evaluate_entry(signal, market, DEFAULT_CTX, CONFIG, corroborating_sources=2) is not None
