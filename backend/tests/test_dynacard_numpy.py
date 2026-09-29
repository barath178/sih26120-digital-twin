"""The torch-free CNN used on small hosts must give the same answers as the PyTorch model."""
import numpy as np
import pytest

from app.ml import dynacard as d


@pytest.mark.skipif(not d.TORCH_OK or not d.NPZ_PATH.exists() or not d.MODEL_PATH.exists(), reason="needs both model files")
def test_numpy_cnn_matches_torch():
    ref = d.DynacardClassifier()
    assert ref.load() and ref.kind == "cnn"
    x, _ = d.generate_dataset(n_per_class=8, seed=0)
    alt = d.DynacardClassifier()
    alt.model, alt.kind = d.NumpyCardCNN(), "cnn-numpy"
    pt, pn = ref.predict(x), alt.predict(x)
    assert np.allclose(pn.sum(axis=1), 1.0, atol=1e-5)
    assert np.abs(pt - pn).max() < 1e-4
    assert (pt.argmax(1) == pn.argmax(1)).all()
