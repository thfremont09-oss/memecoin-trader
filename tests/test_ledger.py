from decimal import Decimal

import pytest

from memecoin_trader.execution.base import FillResult
from memecoin_trader.portfolio.ledger import InsufficientCashError
from tests.conftest import make_signal


def buy_fill(price="1.0", quantity="20", amount="20", fee="0.2"):
    return FillResult(
        price_usd=Decimal(price),
        quantity=Decimal(quantity),
        amount_usd=Decimal(amount),
        fee_usd=Decimal(fee),
        tx_id=None,
    )


def test_starting_cash_is_100(ledger):
    assert ledger.get_cash_usd() == Decimal("100")


def test_open_position_deducts_cash_including_fee(ledger):
    signal = make_signal()
    fill = buy_fill(price="1.0", quantity="20", amount="20", fee="0.2")
    ledger.open_position(
        token_address=signal.token_address,
        symbol="MEME",
        chain_id="solana",
        fill=fill,
        signal=signal,
        entry_liquidity_usd=Decimal("20000"),
        mode="paper",
    )
    assert ledger.get_cash_usd() == Decimal("100") - Decimal("20") - Decimal("0.2")

    positions = ledger.get_open_positions()
    assert len(positions) == 1
    assert positions[0].quantity == Decimal("20")
    assert positions[0].cost_basis_usd == Decimal("20.2")


def test_open_position_rejects_insufficient_cash(ledger):
    signal = make_signal()
    fill = buy_fill(price="1.0", quantity="200", amount="200", fee="2")
    with pytest.raises(InsufficientCashError):
        ledger.open_position(
            token_address=signal.token_address,
            symbol="MEME",
            chain_id="solana",
            fill=fill,
            signal=signal,
            entry_liquidity_usd=Decimal("20000"),
            mode="paper",
        )
    assert ledger.get_cash_usd() == Decimal("100")  # untouched on failure


def _open(ledger, quantity="20", amount="20", fee="0.2", source="twitter_mock", token_address=None):
    signal = make_signal(source=source, token_address=token_address) if token_address else make_signal(source=source)
    fill = buy_fill(quantity=quantity, amount=amount, fee=fee)
    return ledger.open_position(
        token_address=signal.token_address,
        symbol="MEME",
        chain_id="solana",
        fill=fill,
        signal=signal,
        entry_liquidity_usd=Decimal("20000"),
        mode="paper",
    )


def test_full_sell_realizes_pnl_and_closes_position(ledger):
    position = _open(ledger, quantity="20", amount="20", fee="0.2")  # cost basis 20.2, entry price 1.0

    sell_fill = FillResult(
        price_usd=Decimal("2.0"), quantity=Decimal("20"), amount_usd=Decimal("40"), fee_usd=Decimal("0.4"), tx_id=None
    )
    trade = ledger.apply_sell(
        position=position, fraction=Decimal(1), fill=sell_fill, reason="take_profit_partial",
        mark_take_profit_taken=True, mode="paper",
    )

    expected_pnl = (Decimal("40") - Decimal("0.4")) - Decimal("20.2")
    assert trade.realized_pnl_usd == expected_pnl
    assert ledger.get_open_positions() == []

    state = ledger.get_portfolio_state()
    assert state.realized_pnl_usd == expected_pnl
    assert state.cash_usd == Decimal("100") - Decimal("20.2") + (Decimal("40") - Decimal("0.4"))


def test_partial_sell_keeps_position_open_with_reduced_quantity(ledger):
    position = _open(ledger, quantity="20", amount="20", fee="0.2")

    sell_fill = FillResult(
        price_usd=Decimal("2.0"), quantity=Decimal("10"), amount_usd=Decimal("20"), fee_usd=Decimal("0.2"), tx_id=None
    )
    ledger.apply_sell(
        position=position, fraction=Decimal("0.5"), fill=sell_fill, reason="take_profit_partial",
        mark_take_profit_taken=True, mode="paper",
    )

    positions = ledger.get_open_positions()
    assert len(positions) == 1
    remaining = positions[0]
    assert remaining.quantity == Decimal("10")
    assert remaining.take_profit_taken is True
    # half the cost basis (20.2 / 2 = 10.1) should remain against the remaining half
    assert remaining.cost_basis_usd == Decimal("10.1")


def test_cooldown_after_closing_a_position(ledger):
    position = _open(ledger)
    signal = make_signal()
    assert ledger.is_token_on_cooldown(signal.token_address, cooldown_minutes=60) is False

    sell_fill = FillResult(
        price_usd=Decimal("1.0"), quantity=Decimal("20"), amount_usd=Decimal("20"), fee_usd=Decimal("0.2"), tx_id=None
    )
    ledger.apply_sell(
        position=position, fraction=Decimal(1), fill=sell_fill, reason="stop_loss",
        mark_take_profit_taken=False, mode="paper",
    )
    assert ledger.is_token_on_cooldown(signal.token_address, cooldown_minutes=60) is True
    assert ledger.is_token_on_cooldown(signal.token_address, cooldown_minutes=0) is False


def test_equity_snapshot_and_curve(ledger):
    _open(ledger)
    ledger.record_equity_snapshot(Decimal("25"))
    curve = ledger.get_equity_curve()
    assert len(curve) == 1
    assert curve[0]["equity_usd"] == pytest.approx(79.8 + 25)  # 100 - 20.2 cash + 25 positions value


def _insert_equity_snapshot(conn, recorded_at: str, equity: str = "100"):
    conn.execute(
        "INSERT INTO equity_history (cash_usd, positions_value_usd, equity_usd, recorded_at) VALUES (?, ?, ?, ?)",
        (equity, "0", equity, recorded_at),
    )


def test_get_equity_curve_since_iso_excludes_older_snapshots(conn, ledger):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    _insert_equity_snapshot(conn, (now - timedelta(hours=2)).isoformat(), "90")
    _insert_equity_snapshot(conn, (now - timedelta(minutes=1)).isoformat(), "110")

    curve = ledger.get_equity_curve(since_iso=(now - timedelta(hours=1)).isoformat())

    assert len(curve) == 1
    assert curve[0]["equity_usd"] == 110.0


def test_get_equity_curve_downsamples_to_max_points(conn, ledger):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    for i in range(50):
        _insert_equity_snapshot(conn, (now - timedelta(minutes=50 - i)).isoformat(), str(100 + i))

    curve = ledger.get_equity_curve(max_points=10)

    assert len(curve) <= 10
    # the most recent point is always kept exactly, never smoothed away
    assert curve[-1]["equity_usd"] == 149.0


def test_price_history_is_ordered_oldest_first(ledger):
    ledger.record_price_snapshot("TOKEN1", Decimal("1.0"), Decimal("20000"), Decimal("50000"))
    ledger.record_price_snapshot("TOKEN1", Decimal("1.1"), Decimal("20000"), Decimal("50000"))
    history = ledger.get_price_history("TOKEN1")
    assert history == [Decimal("1.0"), Decimal("1.1")]


def test_latest_liquidity_tracks_most_recent_snapshot(ledger):
    assert ledger.get_latest_liquidity("TOKEN1") is None
    ledger.record_price_snapshot("TOKEN1", Decimal("1.0"), Decimal("20000"), Decimal("50000"))
    assert ledger.get_latest_liquidity("TOKEN1") == Decimal("20000")
    ledger.record_price_snapshot("TOKEN1", Decimal("1.0"), Decimal("15000"), Decimal("50000"))
    assert ledger.get_latest_liquidity("TOKEN1") == Decimal("15000")


def test_training_dataset_excludes_open_positions(ledger):
    position = _open(ledger)
    ledger.save_trade_features(position.id, {"signal_score": 80.0})
    assert ledger.get_training_dataset() == []  # still open


def test_training_dataset_labels_profitable_close_as_1(ledger):
    position = _open(ledger, quantity="20", amount="20", fee="0.2")
    ledger.save_trade_features(position.id, {"signal_score": 80.0})
    sell_fill = FillResult(
        price_usd=Decimal("2.0"), quantity=Decimal("20"), amount_usd=Decimal("40"), fee_usd=Decimal("0.4"), tx_id=None
    )
    ledger.apply_sell(
        position=position, fraction=Decimal(1), fill=sell_fill, reason="take_profit_partial",
        mark_take_profit_taken=True, mode="paper",
    )
    dataset = ledger.get_training_dataset()
    assert len(dataset) == 1
    features, label = dataset[0]
    assert label == 1
    assert features == {"signal_score": 80.0}


def test_training_dataset_sums_pnl_across_partial_and_final_sells(ledger):
    position = _open(ledger, quantity="20", amount="20", fee="0.2")
    ledger.save_trade_features(position.id, {"signal_score": 60.0})

    # partial take-profit at a gain...
    partial_fill = FillResult(
        price_usd=Decimal("2.0"), quantity=Decimal("10"), amount_usd=Decimal("20"), fee_usd=Decimal("0.2"), tx_id=None
    )
    ledger.apply_sell(
        position=position, fraction=Decimal("0.5"), fill=partial_fill, reason="take_profit_partial",
        mark_take_profit_taken=True, mode="paper",
    )
    remaining = ledger.get_open_positions()[0]

    # ...then a big loss on the rest, net negative overall
    final_fill = FillResult(
        price_usd=Decimal("0.01"), quantity=Decimal("10"), amount_usd=Decimal("0.1"), fee_usd=Decimal("0.001"), tx_id=None
    )
    ledger.apply_sell(
        position=remaining, fraction=Decimal(1), fill=final_fill, reason="stop_loss",
        mark_take_profit_taken=True, mode="paper",
    )

    dataset = ledger.get_training_dataset()
    assert len(dataset) == 1
    _, label = dataset[0]
    assert label == 0  # net loss across both sells despite the first being profitable


def _close(ledger, position, price="2.0", quantity="20", amount="40", fee="0.4", reason="take_profit_partial"):
    sell_fill = FillResult(
        price_usd=Decimal(price), quantity=Decimal(quantity), amount_usd=Decimal(amount), fee_usd=Decimal(fee), tx_id=None
    )
    return ledger.apply_sell(
        position=position, fraction=Decimal(1), fill=sell_fill, reason=reason, mark_take_profit_taken=True, mode="paper",
    )


def test_performance_by_source_excludes_open_positions(ledger):
    _open(ledger, source="reddit", token_address="TOKENAAAA111111111111111111111111111111111")
    assert ledger.get_performance_by_source() == []


def test_performance_by_source_aggregates_wins_and_losses(ledger):
    winner = _open(ledger, quantity="20", amount="20", fee="0.2", source="reddit", token_address="TOKENAAAA111111111111111111111111111111111")
    _close(ledger, winner, price="2.0", quantity="20", amount="40", fee="0.4")  # profit

    loser = _open(ledger, quantity="20", amount="20", fee="0.2", source="reddit", token_address="TOKENBBBB111111111111111111111111111111111")
    _close(ledger, loser, price="0.1", quantity="20", amount="2", fee="0.02", reason="stop_loss")  # loss

    other = _open(ledger, quantity="20", amount="20", fee="0.2", source="birdeye_trending", token_address="TOKENCCCC111111111111111111111111111111111")
    _close(ledger, other, price="2.0", quantity="20", amount="40", fee="0.4")  # profit

    rows = {row["source"]: row for row in ledger.get_performance_by_source()}

    assert rows["reddit"]["closed_trades"] == 2
    assert rows["reddit"]["wins"] == 1
    assert rows["reddit"]["win_rate_pct"] == 50.0

    assert rows["birdeye_trending"]["closed_trades"] == 1
    assert rows["birdeye_trending"]["wins"] == 1
    assert rows["birdeye_trending"]["win_rate_pct"] == 100.0
    assert rows["birdeye_trending"]["total_pnl_usd"] > 0


def test_performance_by_source_sums_partial_sells_into_one_position(ledger):
    position = _open(ledger, quantity="20", amount="20", fee="0.2", source="pumpfun_launch")
    partial_fill = FillResult(
        price_usd=Decimal("2.0"), quantity=Decimal("10"), amount_usd=Decimal("20"), fee_usd=Decimal("0.2"), tx_id=None
    )
    ledger.apply_sell(
        position=position, fraction=Decimal("0.5"), fill=partial_fill, reason="take_profit_partial",
        mark_take_profit_taken=True, mode="paper",
    )
    remaining = ledger.get_open_positions()[0]
    _close(ledger, remaining, price="2.0", quantity="10", amount="20", fee="0.2")

    rows = ledger.get_performance_by_source()
    assert len(rows) == 1
    assert rows[0]["closed_trades"] == 1  # one position, even though it took two sells to close


def test_caution_level_defaults_to_3(ledger):
    assert ledger.get_caution_level() == 3


def test_set_caution_level_round_trips(ledger):
    assert ledger.set_caution_level(1) == 1
    assert ledger.get_caution_level() == 1


def test_set_caution_level_clamps_above_max(ledger):
    assert ledger.set_caution_level(99) == 5
    assert ledger.get_caution_level() == 5


def test_set_caution_level_clamps_below_min(ledger):
    assert ledger.set_caution_level(-3) == 1
    assert ledger.get_caution_level() == 1
