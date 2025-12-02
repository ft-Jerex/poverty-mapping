"""Deterministic poverty model stub used for testing and demos."""
from __future__ import annotations

from dataclasses import dataclass
import numpy as np


@dataclass
class PovertyModelStub:
    """Simple linear model + sigmoid for deterministic predictions.

    This replaces the real ML model in tests/CI. You can later swap in your
    trained model behind the same interface.
    """

    weights: np.ndarray
    bias: float = 0.0

    def predict(self, features: np.ndarray) -> np.ndarray:
        """Predict scores for a [N, F] or [H, W, F] feature array.

        Returns a score in (0, 1) using a sigmoid.
        """
        feats = np.asarray(features, dtype=float)
        flat = feats.reshape(-1, feats.shape[-1])
        logits = flat @ self.weights + self.bias
        scores = 1.0 / (1.0 + np.exp(-logits))
        return scores.reshape(feats.shape[:-1])


def load_model(num_features: int) -> PovertyModelStub:
    """Create a deterministic model stub with fixed weights.

    The weights are derived from a fixed random seed for reproducibility.
    """
    rng = np.random.default_rng(42)
    weights = rng.normal(loc=0.0, scale=0.5, size=(num_features,))
    bias = 0.1
    return PovertyModelStub(weights=weights, bias=bias)
