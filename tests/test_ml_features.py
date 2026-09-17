from memecoin_trader.ml.features import FEATURE_NAMES, extract_features, features_to_vector
from tests.conftest import make_pair, make_rug_report, make_signal


def test_extract_features_happy_path():
    signal = make_signal(score=77.0)
    market = make_pair(liquidity_usd="50000", fdv_usd="500000", price_change_5m_pct=12.5)
    report = make_rug_report(score=8.0, danger_flags=(), lp_locked_pct=95.0)

    features = extract_features(signal, market, report)

    assert features["signal_score"] == 77.0
    assert features["liquidity_usd"] == 50000.0
    assert features["fdv_usd"] == 500000.0
    assert features["liquidity_to_fdv_pct"] == 10.0
    assert features["price_change_5m_pct"] == 12.5
    assert features["rug_score"] == 8.0
    assert features["rug_danger_flag_count"] == 0.0
    assert features["rug_lp_locked_pct"] == 95.0
    assert set(features.keys()) == set(FEATURE_NAMES)


def test_extract_features_without_rug_report_defaults_to_zero():
    signal = make_signal()
    market = make_pair()
    features = extract_features(signal, market, rug_report=None)
    assert features["rug_score"] == 0.0
    assert features["rug_danger_flag_count"] == 0.0
    assert features["rug_lp_locked_pct"] == 0.0


def test_unknown_fdv_gives_zero_ratio_not_a_crash():
    signal = make_signal()
    market = make_pair(fdv_usd=None)
    features = extract_features(signal, market)
    assert features["fdv_usd"] == 0.0
    assert features["liquidity_to_fdv_pct"] == 0.0


def test_features_to_vector_is_ordered_and_tolerant_of_missing_keys():
    vector = features_to_vector({"signal_score": 1.0})
    assert len(vector) == len(FEATURE_NAMES)
    assert vector[FEATURE_NAMES.index("signal_score")] == 1.0
    assert vector[FEATURE_NAMES.index("liquidity_usd")] == 0.0
