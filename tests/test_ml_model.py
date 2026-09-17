import random

import pytest

from memecoin_trader.ml.features import FEATURE_NAMES
from memecoin_trader.ml.model import TradeQualityModel


def _blank_features(**overrides) -> dict:
    features = {name: 0.0 for name in FEATURE_NAMES}
    features.update(overrides)
    return features


def _synthetic_dataset(n=60, seed=42):
    """Signal score cleanly predicts the label, everything else is noise --
    a model that can't learn this relationship is broken."""
    rng = random.Random(seed)
    rows, labels = [], []
    for _ in range(n):
        score = rng.uniform(0, 100)
        noise = rng.uniform(-5, 5)
        rows.append(_blank_features(signal_score=score, liquidity_usd=rng.uniform(1000, 100000)))
        labels.append(1 if score + noise > 50 else 0)
    return rows, labels


def test_predict_before_training_raises():
    model = TradeQualityModel()
    with pytest.raises(RuntimeError):
        model.predict_proba(_blank_features())


def test_train_requires_both_classes():
    model = TradeQualityModel()
    rows = [_blank_features(signal_score=90.0)] * 5
    with pytest.raises(ValueError):
        model.train(rows, [1, 1, 1, 1, 1])


def test_train_and_predict_roundtrip_learns_the_pattern():
    rows, labels = _synthetic_dataset()
    model = TradeQualityModel()
    stats = model.train(rows, labels)

    assert stats["n_samples"] == len(rows)
    assert 0.0 <= stats["accuracy"] <= 1.0
    assert model.is_trained

    high_confidence = model.predict_proba(_blank_features(signal_score=95.0))
    low_confidence = model.predict_proba(_blank_features(signal_score=5.0))
    assert high_confidence > low_confidence


def test_save_and_load_roundtrip(tmp_path):
    rows, labels = _synthetic_dataset()
    model = TradeQualityModel()
    model.train(rows, labels)

    path = tmp_path / "model.joblib"
    model.save(path)
    assert path.exists()

    loaded = TradeQualityModel.load(path)
    assert loaded is not None
    assert loaded.is_trained

    sample = _blank_features(signal_score=80.0)
    assert loaded.predict_proba(sample) == pytest.approx(model.predict_proba(sample))


def test_load_missing_file_returns_none(tmp_path):
    assert TradeQualityModel.load(tmp_path / "does_not_exist.joblib") is None
