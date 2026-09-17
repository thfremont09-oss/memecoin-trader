"""A small, interpretable classifier predicting P(this trade is profitable)
from its entry-time features.

Because a rug pull is always a large loss, a model trained to predict plain
profitability is implicitly learning to avoid rug-like entry patterns too —
there's no separate "is this a rug" label needed. It starts out useless:
with zero trade history there's nothing to learn from, so `ml.enabled` in
config.yaml should stay off (or the engine will just skip the ML gate, since
it treats "no model on disk" as "no opinion") until `memecoin-trader train`
has run against a meaningful number of the bot's own closed trades.

scikit-learn/joblib are optional dependencies (requirements-ml.txt) and are
only imported when actually training or loading a model.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from memecoin_trader.ml.features import FEATURE_NAMES

logger = logging.getLogger(__name__)


class TradeQualityModel:
    def __init__(self, sklearn_model: Any = None, feature_names: list[str] | None = None):
        self._model = sklearn_model
        self._feature_names = feature_names or list(FEATURE_NAMES)

    @property
    def is_trained(self) -> bool:
        return self._model is not None

    def predict_proba(self, features: dict[str, float]) -> float:
        """Returns P(profitable), 0.0-1.0."""
        if self._model is None:
            raise RuntimeError("model has not been trained or loaded yet")
        vector = [[features.get(name, 0.0) for name in self._feature_names]]
        return float(self._model.predict_proba(vector)[0][1])

    def train(self, feature_rows: list[dict[str, float]], labels: list[int]) -> dict[str, Any]:
        """Trains on (feature_dict, label) pairs, label=1 if the trade was profitable."""
        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.metrics import accuracy_score
            from sklearn.model_selection import train_test_split
        except ImportError as exc:
            raise RuntimeError(
                "training needs scikit-learn: pip install -r requirements-ml.txt"
            ) from exc

        if len(feature_rows) != len(labels):
            raise ValueError("feature_rows and labels must be the same length")
        if len(set(labels)) < 2:
            raise ValueError(
                "need at least one profitable and one unprofitable closed trade to train a classifier"
            )

        X = [[row.get(name, 0.0) for name in self._feature_names] for row in feature_rows]
        y = list(labels)

        try:
            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.25, random_state=42, stratify=y
            )
        except ValueError:
            # too little data (or a class with only one example) to hold out a test split
            X_train, y_train = X, y
            X_test, y_test = X, y

        model = LogisticRegression(max_iter=1000, class_weight="balanced")
        model.fit(X_train, y_train)
        accuracy = float(accuracy_score(y_test, model.predict(X_test)))

        self._model = model
        return {"n_samples": len(X), "accuracy": accuracy}

    def save(self, path: Path) -> None:
        from joblib import dump

        path.parent.mkdir(parents=True, exist_ok=True)
        dump({"model": self._model, "feature_names": self._feature_names}, path)

    @classmethod
    def load(cls, path: Path) -> "TradeQualityModel | None":
        if not path.exists():
            return None
        try:
            from joblib import load

            data = load(path)
            return cls(sklearn_model=data["model"], feature_names=data["feature_names"])
        except Exception:
            logger.exception("failed to load ML model from %s", path)
            return None
