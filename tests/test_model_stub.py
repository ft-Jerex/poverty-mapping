import numpy as np

from src.model.stub_model import PovertyModelStub, load_model


def test_poverty_model_stub_deterministic():
    features = np.ones((4, 3))
    model = load_model(num_features=3)

    out1 = model.predict(features)
    out2 = model.predict(features)

    assert out1.shape == (4,)
    assert np.allclose(out1, out2)


def test_poverty_model_stub_monotonic():
    # Higher feature sum should give higher score in expectation
    model = load_model(num_features=3)
    low = np.zeros((1, 3))
    high = np.ones((1, 3)) * 10.0

    score_low = model.predict(low)[0]
    score_high = model.predict(high)[0]

    assert score_high > score_low
