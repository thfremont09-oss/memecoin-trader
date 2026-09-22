"""End-to-end test of one full buy-then-exit cycle through the real engine
wiring (ledger + strategies + paper executor), with only the two outside
inputs (Twitter signal, DexScreener market data) replaced by fakes."""
import time
from dataclasses import replace
from decimal import Decimal

from memecoin_trader.config import load_settings
from memecoin_trader.engine import TradingEngine, apply_caution_level
from memecoin_trader.execution.paper_executor import PaperExecutor
from tests.conftest import make_pair, make_rug_report, make_signal


class FakeSignalSource:
    def __init__(self):
        self.queue = []

    def poll(self):
        out, self.queue = self.queue, []
        return out


class FakeMarket:
    def __init__(self):
        self.pairs = {}

    def get_best_pair_for_token(self, chain_id, token_address):
        return self.pairs.get(token_address)


class FakeRugCheckClient:
    """Stands in for the real (network-calling) RugCheckClient in tests —
    reports every token as clean unless a test explicitly overrides it."""

    def __init__(self):
        self.reports = {}

    def get_risk_report(self, token_address):
        if token_address in self.reports:
            return self.reports[token_address]
        return make_rug_report(token_address=token_address)


def build_engine(conn):
    settings = load_settings()
    signal_source = FakeSignalSource()
    market = FakeMarket()
    executor = PaperExecutor(settings.paper_execution)
    rug_client = FakeRugCheckClient()
    engine = TradingEngine(settings, conn, market, signal_source, executor, rug_client=rug_client)
    return engine, signal_source, market


def test_full_cycle_buy_then_stop_loss_sell(conn):
    engine, signal_source, market = build_engine(conn)
    initial_cash = engine.ledger.get_cash_usd()
    token = "TOKEN1111111111111111111111111111111111111"

    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
    signal_source.queue = [make_signal(token_address=token, score=90)]

    engine._poll_signals()

    positions = engine.ledger.get_open_positions()
    assert len(positions) == 1
    assert engine.ledger.get_cash_usd() < initial_cash

    # price crashes 30% -> stop loss should fire
    market.pairs[token] = make_pair(token_address=token, price_usd="0.70")
    engine._manage_open_positions()

    assert engine.ledger.get_open_positions() == []
    trades = engine.ledger.get_recent_trades()
    sell_trades = [t for t in trades if t.side == "sell"]
    assert len(sell_trades) == 1
    assert sell_trades[0].reason == "stop_loss"
    assert sell_trades[0].realized_pnl_usd < 0

    engine._record_equity()
    curve = engine.ledger.get_equity_curve()
    assert len(curve) == 1
    assert curve[0]["equity_usd"] < float(initial_cash)  # lost money net of fees+slippage, as expected


def test_low_quality_signal_is_rejected_and_logged(conn):
    engine, signal_source, market = build_engine(conn)
    initial_cash = engine.ledger.get_cash_usd()
    token = "TOKEN2222222222222222222222222222222222222"

    market.pairs[token] = make_pair(token_address=token, price_usd="1.0", liquidity_usd="100")
    signal_source.queue = [make_signal(token_address=token, score=90)]  # good score, bad liquidity

    engine._poll_signals()

    assert engine.ledger.get_open_positions() == []
    assert engine.ledger.get_cash_usd() == initial_cash
    signals_logged = engine.ledger.get_recent_signals()
    assert len(signals_logged) == 1
    assert signals_logged[0]["acted_on"] == 0


def test_dangerous_rug_report_blocks_the_buy(conn):
    engine, signal_source, market = build_engine(conn)
    initial_cash = engine.ledger.get_cash_usd()
    token = "TOKEN3333333333333333333333333333333333333"

    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
    engine.rug_client.reports[token] = make_rug_report(
        token_address=token, danger_flags=("Mint authority not renounced",)
    )
    signal_source.queue = [make_signal(token_address=token, score=90)]

    engine._poll_signals()

    assert engine.ledger.get_open_positions() == []
    assert engine.ledger.get_cash_usd() == initial_cash


def test_successful_buy_stores_trade_features_for_training(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKEN4444444444444444444444444444444444444"

    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
    signal_source.queue = [make_signal(token_address=token, score=90)]
    engine._poll_signals()

    position = engine.ledger.get_open_positions()[0]

    # close it out so it shows up in the training dataset
    market.pairs[token] = make_pair(token_address=token, price_usd="0.70")
    engine._manage_open_positions()

    dataset = engine.ledger.get_training_dataset()
    assert len(dataset) == 1
    features, label = dataset[0]
    assert label == 0  # closed at a loss
    assert features["signal_score"] == 90.0


def test_liquidate_all_sells_every_open_position(conn):
    engine, signal_source, market = build_engine(conn)
    token_a = "TOKEN5555555555555555555555555555555555555"
    token_b = "TOKEN6666666666666666666666666666666666666"

    for token in (token_a, token_b):
        market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
        signal_source.queue = [make_signal(token_address=token, score=90)]
        engine._poll_signals()

    assert len(engine.ledger.get_open_positions()) == 2
    cash_before = engine.ledger.get_cash_usd()

    closed = engine.liquidate_all()

    assert closed == 2
    assert engine.ledger.get_open_positions() == []
    assert engine.ledger.get_cash_usd() > cash_before  # proceeds landed back in cash
    trades = [t for t in engine.ledger.get_recent_trades() if t.side == "sell"]
    assert all(t.reason == "manual_liquidation" for t in trades)


def test_liquidate_all_skips_positions_with_no_market_data(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKEN7777777777777777777777777777777777777"

    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
    signal_source.queue = [make_signal(token_address=token, score=90)]
    engine._poll_signals()

    del market.pairs[token]  # simulate no market data available (e.g. delisted, network issue)

    closed = engine.liquidate_all()

    assert closed == 0
    assert len(engine.ledger.get_open_positions()) == 1  # left open, not lost


def test_liquidate_all_is_a_noop_with_no_open_positions(conn):
    engine, signal_source, market = build_engine(conn)
    assert engine.liquidate_all() == 0


def test_liquidate_position_sells_just_that_token(conn):
    engine, signal_source, market = build_engine(conn)
    token_a = "TOKEN8888888888888888888888888888888888888"
    token_b = "TOKEN9999999999999999999999999999999999999"

    for token in (token_a, token_b):
        market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
        signal_source.queue = [make_signal(token_address=token, score=90)]
        engine._poll_signals()

    assert engine.liquidate_position(token_a) is True

    remaining = engine.ledger.get_open_positions()
    assert len(remaining) == 1
    assert remaining[0].token_address == token_b

    sell_trades = [t for t in engine.ledger.get_recent_trades() if t.side == "sell"]
    assert len(sell_trades) == 1
    assert sell_trades[0].reason == "manual_sell"


def test_liquidate_position_returns_false_for_unknown_token(conn):
    engine, signal_source, market = build_engine(conn)
    assert engine.liquidate_position("NOT_A_REAL_TOKEN") is False


def test_trading_enabled_defaults_true_and_can_be_toggled(conn):
    engine, signal_source, market = build_engine(conn)
    assert engine.ledger.is_trading_enabled() is True

    engine.ledger.set_trading_enabled(False)
    assert engine.ledger.is_trading_enabled() is False

    engine.ledger.set_trading_enabled(True)
    assert engine.ledger.is_trading_enabled() is True


def test_tick_skips_new_buys_while_offline_but_still_protects_open_positions(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKEN0000000000000000000000000000000000001"
    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")

    engine.ledger.set_trading_enabled(False)
    queued_signal = make_signal(token_address=token, score=90)
    signal_source.queue = [queued_signal]
    engine._last_signal_poll = 0.0
    engine._last_position_check = 0.0
    engine._last_equity_snapshot = 0.0

    engine.tick()

    # offline: the queued signal was never even polled, so nothing got bought
    assert engine.ledger.get_open_positions() == []
    assert signal_source.queue == [queued_signal]  # poll() never called, signal still queued
    # but equity is still being tracked while offline
    assert len(engine.ledger.get_equity_curve()) == 1


def test_tick_still_polls_and_buys_when_online(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKEN0000000000000000000000000000000000002"
    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")

    signal_source.queue = [make_signal(token_address=token, score=90)]
    engine._last_signal_poll = 0.0
    engine._last_position_check = 0.0
    engine._last_equity_snapshot = 0.0

    engine.tick()

    assert len(engine.ledger.get_open_positions()) == 1


def test_corroborating_source_count_tracks_distinct_recent_sources(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKENAAAA111111111111111111111111111111111"

    assert engine._corroborating_source_count(make_signal(token_address=token, source="reddit")) == 1
    # a different source flagging the same token recently -> now corroborated
    assert engine._corroborating_source_count(make_signal(token_address=token, source="birdeye_trending")) == 2
    # the same source again is not a *new* corroborating source
    assert engine._corroborating_source_count(make_signal(token_address=token, source="reddit")) == 2


def test_corroborating_source_count_expires_outside_the_window(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKENBBBB111111111111111111111111111111111"

    engine._corroborating_source_count(make_signal(token_address=token, source="reddit"))
    # simulate that mention having happened outside the corroboration window
    window_seconds = engine.settings.entry.corroboration_window_minutes * 60
    engine._recent_signal_sources[token]["reddit"] -= window_seconds + 1

    assert engine._corroborating_source_count(make_signal(token_address=token, source="birdeye_trending")) == 1


def test_engine_auto_trains_ml_model_once_enough_closed_trades_exist(conn, tmp_path, monkeypatch):
    # _maybe_retrain_ml_model saves to the module-level MODEL_PATH constant --
    # redirect it so this test writes to a throwaway file, never the real
    # data/model.joblib.
    monkeypatch.setattr("memecoin_trader.engine.MODEL_PATH", tmp_path / "model.joblib")

    engine, signal_source, market = build_engine(conn)
    engine.settings = replace(
        engine.settings,
        entry=replace(
            engine.settings.entry,
            ml=replace(engine.settings.entry.ml, enabled=True, min_training_trades=2),
        ),
    )

    losing_token = "TOKENCCCC111111111111111111111111111111111"
    winning_token = "TOKENDDDD111111111111111111111111111111111"

    market.pairs[losing_token] = make_pair(token_address=losing_token, price_usd="1.0")
    signal_source.queue = [make_signal(token_address=losing_token, score=90)]
    engine._poll_signals()
    market.pairs[losing_token] = make_pair(token_address=losing_token, price_usd="0.5")  # stop-loss territory
    engine._manage_open_positions()

    market.pairs[winning_token] = make_pair(token_address=winning_token, price_usd="1.0")
    signal_source.queue = [make_signal(token_address=winning_token, score=90)]
    engine._poll_signals()
    market.pairs[winning_token] = make_pair(token_address=winning_token, price_usd="3.0")  # up big
    engine.liquidate_position(winning_token)

    assert engine.ml_model is None  # nothing trained yet
    engine._maybe_retrain_ml_model()

    assert engine.ml_model is not None
    assert engine.ml_model.is_trained


def test_ml_retrain_is_a_noop_without_new_closed_trades(conn):
    engine, signal_source, market = build_engine(conn)
    engine.settings = replace(
        engine.settings,
        entry=replace(
            engine.settings.entry,
            ml=replace(engine.settings.entry.ml, enabled=True, min_training_trades=100),
        ),
    )
    engine._maybe_retrain_ml_model()
    assert engine.ml_model is None  # nowhere near min_training_trades


def test_apply_caution_level_default_matches_config_baseline():
    settings = load_settings()
    entry, cooldown = apply_caution_level(settings.entry, settings.timing.token_cooldown_minutes, 3)
    assert entry.mention_score_threshold == settings.entry.mention_score_threshold
    assert cooldown == settings.timing.token_cooldown_minutes


def test_apply_caution_level_more_cautious_raises_threshold_and_cooldown():
    settings = load_settings()
    entry, cooldown = apply_caution_level(settings.entry, settings.timing.token_cooldown_minutes, 1)
    assert entry.mention_score_threshold > settings.entry.mention_score_threshold
    assert cooldown > settings.timing.token_cooldown_minutes


def test_apply_caution_level_less_cautious_lowers_threshold_and_cooldown():
    settings = load_settings()
    entry, cooldown = apply_caution_level(settings.entry, settings.timing.token_cooldown_minutes, 5)
    assert entry.mention_score_threshold < settings.entry.mention_score_threshold
    assert cooldown < settings.timing.token_cooldown_minutes


def test_apply_caution_level_clamps_extreme_baselines():
    extreme = replace(load_settings().entry, mention_score_threshold=95.0)
    entry, _ = apply_caution_level(extreme, 5.0, 1)  # +10 offset would push this to 105
    assert entry.mention_score_threshold <= 90.0

    tiny_cooldown_entry, cooldown = apply_caution_level(load_settings().entry, 10.0, 5)  # -30 offset would go negative
    assert cooldown >= 5.0


def test_unknown_caution_level_falls_back_to_default():
    settings = load_settings()
    entry, cooldown = apply_caution_level(settings.entry, settings.timing.token_cooldown_minutes, 999)
    assert entry.mention_score_threshold == settings.entry.mention_score_threshold
    assert cooldown == settings.timing.token_cooldown_minutes


def test_recent_tick_liquidity_drop_does_not_trigger_sudden_rug(conn):
    # A single position-check-interval-apart (5s) liquidity comparison is
    # noise-prone in thin pools -- the sudden-rug check should only compare
    # against a snapshot at least liquidity_rug_check_interval_seconds old,
    # so a drop recorded between two back-to-back _manage_open_positions()
    # calls must NOT trigger it.
    engine, signal_source, market = build_engine(conn)
    token = "TOKENFFFF111111111111111111111111111111111"

    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")  # default liquidity_usd="50000"
    signal_source.queue = [make_signal(token_address=token, score=90)]
    engine._poll_signals()
    assert len(engine.ledger.get_open_positions()) == 1

    # first management tick: records the only price snapshot so far
    engine._manage_open_positions()
    assert len(engine.ledger.get_open_positions()) == 1

    # liquidity halves an instant later -- too recent to count as the
    # "previous" reading for the sudden-drop check
    market.pairs[token] = make_pair(token_address=token, price_usd="1.0", liquidity_usd="25000")
    engine._manage_open_positions()

    positions = engine.ledger.get_open_positions()
    assert len(positions) == 1
    assert positions[0].token_address == token


def test_catastrophic_liquidity_drop_triggers_instant_exit(conn):
    # Unlike the windowed sudden-drop check, the catastrophic check compares
    # against the literal immediately-previous poll (however recent) so a
    # true one-tick LP drain is still caught fast even right after entry.
    engine, signal_source, market = build_engine(conn)
    token = "TOKENHHHH111111111111111111111111111111111"

    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")  # default liquidity_usd="50000"
    signal_source.queue = [make_signal(token_address=token, score=90)]
    engine._poll_signals()
    assert len(engine.ledger.get_open_positions()) == 1

    # first management tick: records the only price snapshot so far
    engine._manage_open_positions()
    assert len(engine.ledger.get_open_positions()) == 1

    # liquidity craters 80% an instant later -- past the 70% catastrophic bar
    market.pairs[token] = make_pair(token_address=token, price_usd="1.0", liquidity_usd="10000")
    engine._manage_open_positions()

    assert engine.ledger.get_open_positions() == []
    sell_trades = [t for t in engine.ledger.get_recent_trades() if t.side == "sell"]
    assert len(sell_trades) == 1
    assert sell_trades[0].reason == "liquidity_rug_catastrophic"


def test_old_enough_liquidity_drop_still_triggers_sudden_rug(conn):
    from datetime import datetime, timedelta, timezone

    engine, signal_source, market = build_engine(conn)
    token = "TOKENGGGG111111111111111111111111111111111"

    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")  # default liquidity_usd="50000"
    signal_source.queue = [make_signal(token_address=token, score=90)]
    engine._poll_signals()
    assert len(engine.ledger.get_open_positions()) == 1

    # backdate a snapshot old enough to clear liquidity_rug_check_interval_seconds
    backdated = datetime.now(timezone.utc) - timedelta(seconds=60)
    conn.execute(
        "INSERT INTO price_snapshots (token_address, price_usd, liquidity_usd, volume_24h_usd, captured_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (token, "1.0", "50000", "50000", backdated.isoformat()),
    )
    conn.commit()

    # a genuine rug: liquidity drops by half vs. that old reading -- past the
    # 45% sudden-drop bar but under the 70% catastrophic-drop bar, so this
    # exercises the windowed check specifically, not the instant one
    market.pairs[token] = make_pair(token_address=token, price_usd="1.0", liquidity_usd="25000")
    engine._manage_open_positions()

    assert engine.ledger.get_open_positions() == []
    sell_trades = [t for t in engine.ledger.get_recent_trades() if t.side == "sell"]
    assert len(sell_trades) == 1
    assert sell_trades[0].reason == "liquidity_rug_sudden"


def test_caution_level_changes_whether_a_borderline_signal_gets_bought(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKENEEEE111111111111111111111111111111111"
    market.pairs[token] = make_pair(token_address=token)

    # score 50 clears caution level 5's lowered threshold (config baseline
    # 55 minus a 10-point offset = 45) but not level 3's baseline (55) or
    # level 1's raised threshold (65).
    signal_source.queue = [make_signal(token_address=token, score=50)]
    engine.ledger.set_caution_level(3)
    engine._poll_signals()
    assert engine.ledger.get_open_positions() == []

    signal_source.queue = [make_signal(token_address=token, score=50)]
    engine.ledger.set_caution_level(5)
    engine._poll_signals()
    assert len(engine.ledger.get_open_positions()) == 1


def test_big_risk_search_buys_first_qualifying_signal_ignoring_score_threshold(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKENRISK000000000000000000000000000000001"
    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
    # score 5 -- nowhere near the normal mention_score_threshold (55) --
    # Big Risk mode doesn't care, it just needs the safety filters to pass
    signal_source.queue = [make_signal(token_address=token, score=5)]

    engine.ledger.start_big_risk_search()
    engine._big_risk_search()

    positions = engine.ledger.get_open_positions()
    assert len(positions) == 1
    assert positions[0].token_address == token
    # went (almost) all-in rather than the normal 15%-of-cash sizing
    assert engine.ledger.get_cash_usd() < Decimal("5")

    state = engine.ledger.get_big_risk_state()
    assert state.mode == "invested"
    assert state.position_id == positions[0].id


def test_big_risk_search_still_blocks_a_dangerous_rug_report(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKENRISK000000000000000000000000000000002"
    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
    engine.rug_client.reports[token] = make_rug_report(
        token_address=token, danger_flags=("Mint authority not renounced",)
    )
    signal_source.queue = [make_signal(token_address=token, score=5)]

    engine.ledger.start_big_risk_search()
    engine._big_risk_search()

    assert engine.ledger.get_open_positions() == []
    assert engine.ledger.get_big_risk_state().mode == "searching"  # still looking


def test_big_risk_search_accepts_liquidity_below_the_normal_entry_floor(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKENRISK000000000000000000000000000000006"
    # below entry.min_liquidity_usd (5000) but above big_risk.min_liquidity_usd (2500);
    # fdv lowered too so the liquidity/FDV ratio (6%) clears both floors and isn't
    # the thing actually being tested here
    market.pairs[token] = make_pair(token_address=token, price_usd="1.0", liquidity_usd="3000", fdv_usd="50000")
    signal_source.queue = [make_signal(token_address=token, score=5)]

    engine.ledger.start_big_risk_search()
    engine._big_risk_search()

    assert len(engine.ledger.get_open_positions()) == 1
    assert engine.ledger.get_big_risk_state().mode == "invested"


def test_normal_poll_signals_rejects_the_same_liquidity_big_risk_would_accept(conn):
    # confirms the two paths actually use different floors, not just that
    # the number happens to clear both
    engine, signal_source, market = build_engine(conn)
    token = "TOKENRISK000000000000000000000000000000007"
    market.pairs[token] = make_pair(token_address=token, price_usd="1.0", liquidity_usd="3000", fdv_usd="50000")
    signal_source.queue = [make_signal(token_address=token, score=90)]

    engine._poll_signals()

    assert engine.ledger.get_open_positions() == []


def test_big_risk_search_ignores_the_ml_confidence_gate(conn, tmp_path, monkeypatch):
    monkeypatch.setattr("memecoin_trader.engine.MODEL_PATH", tmp_path / "model.joblib")

    engine, signal_source, market = build_engine(conn)
    engine.settings = replace(
        engine.settings,
        entry=replace(
            engine.settings.entry,
            ml=replace(engine.settings.entry.ml, enabled=True, min_confidence=0.99),
        ),
    )

    class AlwaysLowConfidenceModel:
        is_trained = True

        def predict_proba(self, features):
            return 0.01  # would fail entry.ml.min_confidence (0.99) by a mile

    engine.ml_model = AlwaysLowConfidenceModel()

    token = "TOKENRISK000000000000000000000000000000008"
    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
    signal_source.queue = [make_signal(token_address=token, score=5)]

    engine.ledger.start_big_risk_search()
    engine._big_risk_search()

    assert len(engine.ledger.get_open_positions()) == 1  # big_risk.ml_gate_enabled=false bypassed it


def test_tick_polls_big_risk_search_on_its_own_faster_interval(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKENRISK000000000000000000000000000000009"
    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
    signal_source.queue = [make_signal(token_address=token, score=5)]

    engine.ledger.start_big_risk_search()
    # normal signal_poll_interval_seconds (45s) hasn't elapsed, but
    # big_risk.poll_interval_seconds (15s) has -- the search should still fire
    engine._last_signal_poll = time.time()
    engine._last_big_risk_poll = time.time() - engine.settings.big_risk.poll_interval_seconds - 1
    engine._last_position_check = 0.0
    engine._last_equity_snapshot = 0.0

    engine.tick()

    assert len(engine.ledger.get_open_positions()) == 1


def test_big_risk_search_times_out_and_resumes_normal_trading(conn):
    from datetime import datetime, timedelta, timezone

    engine, signal_source, market = build_engine(conn)
    engine.ledger.start_big_risk_search()
    too_old = datetime.now(timezone.utc) - timedelta(seconds=engine.settings.big_risk.search_window_seconds + 5)
    engine.ledger._conn.execute(
        "UPDATE portfolio_state SET big_risk_started_at = ? WHERE id = 1", (too_old.isoformat(),)
    )
    engine.ledger._conn.commit()
    engine._last_signal_poll = 0.0
    engine._last_position_check = 0.0
    engine._last_equity_snapshot = 0.0

    engine.tick()

    assert engine.ledger.get_big_risk_state().mode == "idle"
    assert engine.ledger.get_open_positions() == []


def test_big_risk_search_aborts_if_trading_goes_offline_mid_search(conn):
    engine, signal_source, market = build_engine(conn)
    engine.ledger.start_big_risk_search()
    engine.ledger.set_trading_enabled(False)

    engine.tick()

    assert engine.ledger.get_big_risk_state().mode == "idle"


def test_tick_routes_to_big_risk_search_instead_of_normal_polling(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKENRISK000000000000000000000000000000003"
    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
    signal_source.queue = [make_signal(token_address=token, score=5)]  # below the normal threshold

    engine.ledger.start_big_risk_search()
    engine._last_signal_poll = 0.0
    engine._last_position_check = 0.0
    engine._last_equity_snapshot = 0.0

    engine.tick()

    positions = engine.ledger.get_open_positions()
    assert len(positions) == 1  # bought despite score 5, which normal _poll_signals would have rejected
    assert engine.ledger.get_big_risk_state().mode == "invested"


def test_big_risk_invested_position_uses_a_tighter_stop_loss(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKENRISK000000000000000000000000000000004"
    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
    signal_source.queue = [make_signal(token_address=token, score=90)]
    engine._poll_signals()  # a normal buy stands in for "the search found this one"
    position = engine.ledger.get_open_positions()[0]
    engine.ledger.set_big_risk_invested(position.id)

    # an 18% drop clears big_risk.stop_loss_pct (0.15) but not the normal
    # exit.stop_loss_pct (0.25) -- only the tighter Big Risk threshold
    # should be able to explain this exit
    market.pairs[token] = make_pair(token_address=token, price_usd="0.82")
    engine._big_risk_manage_position(engine.ledger.get_big_risk_state())

    assert engine.ledger.get_open_positions() == []
    sell_trades = [t for t in engine.ledger.get_recent_trades() if t.side == "sell"]
    assert sell_trades[0].reason == "stop_loss"
    assert engine.ledger.get_big_risk_state().mode == "idle"  # resumed normal trading


def test_big_risk_manage_position_resumes_normal_trading_once_closed_elsewhere(conn):
    engine, signal_source, market = build_engine(conn)
    token = "TOKENRISK000000000000000000000000000000005"
    market.pairs[token] = make_pair(token_address=token, price_usd="1.0")
    signal_source.queue = [make_signal(token_address=token, score=90)]
    engine._poll_signals()
    position = engine.ledger.get_open_positions()[0]
    engine.ledger.set_big_risk_invested(position.id)

    engine.liquidate_position(token, reason="manual_sell")  # e.g. the per-position Sell button

    engine._big_risk_manage_position(engine.ledger.get_big_risk_state())

    assert engine.ledger.get_big_risk_state().mode == "idle"
