from types import SimpleNamespace

from modules.audio.vo_se_engine_patch import _note_duration_frames


def test_note_duration_frames_preserves_real_duration():
    assert _note_duration_frames(SimpleNamespace(duration=0.1)) == 20
    assert _note_duration_frames(SimpleNamespace(duration=0.635)) == 127
    assert _note_duration_frames(SimpleNamespace(duration=1.0)) == 200


def test_note_duration_frames_handles_invalid_duration():
    assert _note_duration_frames(SimpleNamespace(duration=0.0)) == 1
    assert _note_duration_frames(SimpleNamespace(duration=None)) == 1
    assert _note_duration_frames(SimpleNamespace(duration="invalid")) == 1
