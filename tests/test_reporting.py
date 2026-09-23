from datetime import datetime, timedelta, timezone
from decimal import Decimal

from memecoin_trader.config import load_settings
from memecoin_trader.execution.base import FillResult
from memecoin_trader.reporting import (
    EQUITY_CURVE_RANGES,
    _mark_price,
    _range_since_iso,
    build_position_detail,
    build_summary,
)
from tests.conftest import make_signal

SETTINGS = load_settings()


class FakeLivePair:
    def __init__(self, price_usd):
        self.price_usd = price_usd


class FakeMarketClient:
    """Stands in for DexScreenerClient in _mark_price's live-lookup path."""

    def __init__(self, result=None, raises=False):
        self._result = result
        self._raises = raises
        self.calls = []

    def get_best_pair_for_token(self, chain_id, token_address, quick=False):
        self.calls.append((chain_id, token_address, quick))
        if self._raises:
            raise RuntimeError("network hiccup")
        return self._result


def test_equity_curve_ranges_lists_every_button_key():
    assert EQUITY_CURVE_RANGES == ["1min", "5m", "1h", "1d", "1w", "1m", "ytd", "all"]


def test_range_since_iso_all_has_no_cutoff():
    assert _range_since_iso("all") is None


def test_range_since_iso_unknown_falls_back_to_no_cutoff():
    assert _range_since_iso("not-a-real-range") is None


def test_range_since_iso_ytd_is_january_first_this_year():
    since = datetime.fromisoformat(_range_since_iso("ytd"))
    now = datetime.now(timezone.utc)
    assert since.year == now.year
    assert since.month == 1
    assert since.day == 1


def test_range_since_iso_1h_is_roughly_one_hour_ago():
    since = datetime.fromisoformat(_range_since_iso("1h"))
    now = datetime.now(timezone.utc)
    assert timedelta(minutes=59) < (now - since) < timedelta(minutes=61)


def test_range_since_iso_1min_is_roughly_one_minute_ago():
    since = datetime.fromisoformat(_range_since_iso("1min"))
    now = datetime.now(timezone.utc)
    assert timedelta(seconds=55) < (now - since) < timedelta(seconds=65)


def test_build_summary_defaults_to_all_range(ledger):
    summary = build_summary(ledger, SETTINGS)
    assert summary["equity_range"] == "all"


def test_build_summary_rejects_unknown_range_by_reporting_all(ledger):
    summary = build_summary(ledger, SETTINGS, equity_range="bogus")
    assert summary["equity_range"] == "all"


def test_build_summary_echoes_a_valid_requested_range(ledger):
    summary = build_summary(ledger, SETTINGS, equity_range="1w")
    assert summary["equity_range"] == "1w"


def test_build_summary_open_positions_carry_their_id(ledger):
    signal = make_signal()
    fill = FillResult(
        price_usd=Decimal("1.0"), quantity=Decimal("20"), amount_usd=Decimal("20"), fee_usd=Decimal("0.2"), tx_id=None
    )
    position = ledger.open_position(
        token_address=signal.token_address, symbol="MEME", chain_id="solana", fill=fill,
        signal=signal, entry_liquidity_usd=Decimal("20000"), mode="paper",
    )

    summary = build_summary(ledger, SETTINGS)
    assert summary["open_positions"][0]["id"] == position.id
    assert summary["recent_trades"][0]["position_id"] == position.id


def test_build_summary_big_risk_defaults_to_idle(ledger):
    summary = build_summary(ledger, SETTINGS)
    assert summary["big_risk"] == {
        "mode": "idle",
        "started_at": None,
        "search_window_seconds": SETTINGS.big_risk.search_window_seconds,
        "max_position_usd": SETTINGS.big_risk.max_position_usd,
        "position": None,
    }


def test_build_summary_big_risk_reports_the_invested_position(ledger):
    signal = make_signal()
    fill = FillResult(
        price_usd=Decimal("1.0"), quantity=Decimal("20"), amount_usd=Decimal("20"), fee_usd=Decimal("0.2"), tx_id=None
    )
    position = ledger.open_position(
        token_address=signal.token_address, symbol="MEME", chain_id="solana", fill=fill,
        signal=signal, entry_liquidity_usd=Decimal("20000"), mode="paper",
    )
    ledger.set_big_risk_invested(position.id)
    ledger.record_price_snapshot(signal.token_address, Decimal("1.5"), Decimal("20000"), Decimal("50000"))

    summary = build_summary(ledger, SETTINGS)
    assert summary["big_risk"]["mode"] == "invested"
    assert summary["big_risk"]["position"]["id"] == position.id
    assert summary["big_risk"]["position"]["current_price_usd"] == 1.5
    assert summary["big_risk"]["position"]["unrealized_pnl_usd"] > 0


def test_build_position_detail_returns_none_for_unknown_id(ledger):
    assert build_position_detail(ledger, 999999) is None


def test_build_position_detail_for_an_open_position(ledger):
    signal = make_signal()
    fill = FillResult(
        price_usd=Decimal("1.0"), quantity=Decimal("20"), amount_usd=Decimal("20"), fee_usd=Decimal("0.2"), tx_id=None
    )
    position = ledger.open_position(
        token_address=signal.token_address, symbol="MEME", chain_id="solana", fill=fill,
        signal=signal, entry_liquidity_usd=Decimal("20000"), mode="paper",
    )
    ledger.record_price_snapshot(signal.token_address, Decimal("1.5"), Decimal("20000"), Decimal("50000"))

    detail = build_position_detail(ledger, position.id)
    assert detail["status"] == "open"
    assert detail["entry_price_usd"] == 1.0
    assert detail["current_price_usd"] == 1.5  # marked to the latest snapshot
    assert detail["closed_at"] is None
    assert detail["unrealized_pnl_usd"] > 0
    assert detail["realized_pnl_usd"] == 0.0
    assert len(detail["trades"]) == 1
    assert detail["trades"][0]["side"] == "buy"
    assert detail["price_series"] == [{"captured_at": detail["price_series"][0]["captured_at"], "price_usd": 1.5}]


def test_build_position_detail_for_a_closed_position(ledger):
    signal = make_signal()
    buy_fill = FillResult(
        price_usd=Decimal("1.0"), quantity=Decimal("20"), amount_usd=Decimal("20"), fee_usd=Decimal("0.2"), tx_id=None
    )
    position = ledger.open_position(
        token_address=signal.token_address, symbol="MEME", chain_id="solana", fill=buy_fill,
        signal=signal, entry_liquidity_usd=Decimal("20000"), mode="paper",
    )
    sell_fill = FillResult(
        price_usd=Decimal("2.0"), quantity=Decimal("20"), amount_usd=Decimal("40"), fee_usd=Decimal("0.4"), tx_id=None
    )
    ledger.apply_sell(
        position=position, fraction=Decimal(1), fill=sell_fill, reason="take_profit_partial",
        mark_take_profit_taken=True, mode="paper",
    )

    detail = build_position_detail(ledger, position.id)
    assert detail["status"] == "closed"
    assert detail["current_price_usd"] == 2.0  # the price it actually exited at
    assert detail["closed_at"] is not None
    assert detail["unrealized_pnl_usd"] == 0.0
    assert detail["realized_pnl_usd"] > 0
    assert [t["side"] for t in detail["trades"]] == ["buy", "sell"]


def test_mark_price_uses_db_snapshot_when_no_market_client_given(ledger):
    ledger.record_price_snapshot("TOKEN1", Decimal("1.5"), Decimal("20000"), Decimal("50000"))
    assert _mark_price(ledger, "TOKEN1", Decimal("1.0")) == Decimal("1.5")


def test_mark_price_prefers_the_live_quick_lookup_over_the_db_snapshot(ledger):
    ledger.record_price_snapshot("TOKEN1", Decimal("1.5"), Decimal("20000"), Decimal("50000"))
    market_client = FakeMarketClient(result=FakeLivePair(Decimal("1.9")))

    price = _mark_price(ledger, "TOKEN1", Decimal("1.0"), market_client, "solana")

    assert price == Decimal("1.9")
    assert market_client.calls == [("solana", "TOKEN1", True)]  # quick=True -- a display refresh, not a trade


def test_mark_price_falls_back_to_db_when_live_lookup_returns_nothing(ledger):
    ledger.record_price_snapshot("TOKEN1", Decimal("1.5"), Decimal("20000"), Decimal("50000"))
    market_client = FakeMarketClient(result=None)

    price = _mark_price(ledger, "TOKEN1", Decimal("1.0"), market_client, "solana")

    assert price == Decimal("1.5")


def test_mark_price_falls_back_to_db_when_live_lookup_raises(ledger):
    ledger.record_price_snapshot("TOKEN1", Decimal("1.5"), Decimal("20000"), Decimal("50000"))
    market_client = FakeMarketClient(raises=True)

    price = _mark_price(ledger, "TOKEN1", Decimal("1.0"), market_client, "solana")

    assert price == Decimal("1.5")  # a flaky network call never breaks the display


def test_build_summary_open_positions_reflect_the_live_price_when_given(ledger):
    signal = make_signal()
    fill = FillResult(
        price_usd=Decimal("1.0"), quantity=Decimal("20"), amount_usd=Decimal("20"), fee_usd=Decimal("0.2"), tx_id=None
    )
    ledger.open_position(
        token_address=signal.token_address, symbol="MEME", chain_id="solana", fill=fill,
        signal=signal, entry_liquidity_usd=Decimal("20000"), mode="paper",
    )
    market_client = FakeMarketClient(result=FakeLivePair(Decimal("3.0")))

    summary = build_summary(ledger, SETTINGS, market_client=market_client)

    assert summary["open_positions"][0]["current_price_usd"] == 3.0
