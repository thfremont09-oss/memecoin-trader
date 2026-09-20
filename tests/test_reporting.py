from datetime import datetime, timedelta, timezone

from memecoin_trader.reporting import EQUITY_CURVE_RANGES, _range_since_iso, build_summary


def test_equity_curve_ranges_lists_every_button_key():
    assert EQUITY_CURVE_RANGES == ["5m", "1h", "1d", "1w", "1m", "ytd", "all"]


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


def test_build_summary_defaults_to_all_range(ledger):
    summary = build_summary(ledger)
    assert summary["equity_range"] == "all"


def test_build_summary_rejects_unknown_range_by_reporting_all(ledger):
    summary = build_summary(ledger, equity_range="bogus")
    assert summary["equity_range"] == "all"


def test_build_summary_echoes_a_valid_requested_range(ledger):
    summary = build_summary(ledger, equity_range="1w")
    assert summary["equity_range"] == "1w"
