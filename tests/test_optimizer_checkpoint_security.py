import os
import pickle
from pathlib import Path

from modules.tools.batch_voice_optimizer import OtoPredictor


class _UnpickleCommand:
    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def __reduce__(self):
        return (os.mkdir, (str(self.marker),))


def test_optimizer_pickle_requires_explicit_trust_opt_in(tmp_path, monkeypatch):
    marker = tmp_path / "pickle-executed"
    checkpoint = tmp_path / "untrusted-model.pkl"
    checkpoint.write_bytes(pickle.dumps(_UnpickleCommand(marker)))
    monkeypatch.delenv("VOSE_TRUST_PICKLE_MODELS", raising=False)

    predictor = OtoPredictor()
    predictor.load(str(checkpoint))

    assert predictor.model is None
    assert predictor.is_trained is False
    assert not marker.exists()
