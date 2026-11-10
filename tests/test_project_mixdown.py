from types import SimpleNamespace

import numpy as np
import soundfile as sf
import pytest

from modules.audio.project_mixdown import mixdown_tracks_to_wav


def _track(path, **overrides):
    values = {
        "name": "Track",
        "track_type": "wave",
        "audio_path": str(path),
        "playback_path": "",
        "volume": 1.0,
        "pan": 0.0,
        "is_muted": False,
        "is_solo": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _write(path, data, sample_rate=8000):
    sf.write(path, np.asarray(data, dtype=np.float32), sample_rate, subtype="FLOAT")


def test_project_mixdown_matches_live_mixer_gain_pan_mute_and_solo(tmp_path):
    left_input = tmp_path / "left.wav"
    muted_input = tmp_path / "muted.wav"
    left = np.column_stack(
        [np.full(16, 0.5, dtype=np.float32), np.full(16, 0.25, dtype=np.float32)]
    )
    muted = np.full((16, 2), 0.75, dtype=np.float32)
    _write(left_input, left)
    _write(muted_input, muted)

    tracks = [
        _track(left_input, volume=0.5, pan=-1.0),
        _track(muted_input, is_muted=True),
        # Solo on a path-less track still follows AudioMixer's existing solo rule.
        _track("", audio_path="", is_solo=False),
    ]
    output = tmp_path / "mix.wav"

    result = mixdown_tracks_to_wav(
        tracks, output, sample_rate=8000, block_size=5
    )

    data, rate = sf.read(result, dtype="float32", always_2d=True)
    assert rate == 8000
    assert data.shape == (16, 2)
    assert np.allclose(data[:, 0], 0.25, atol=2e-5)
    assert np.allclose(data[:, 1], 0.0, atol=2e-5)


def test_project_mixdown_uses_solo_state_even_when_solo_track_is_muted(tmp_path):
    audible_input = tmp_path / "audible.wav"
    solo_input = tmp_path / "solo.wav"
    _write(audible_input, np.full((12, 2), 0.5, dtype=np.float32))
    _write(solo_input, np.full((12, 2), 0.9, dtype=np.float32))

    output = tmp_path / "mix.wav"
    tracks = [
        _track(audible_input),
        _track(solo_input, is_solo=True, is_muted=True),
    ]
    mixdown_tracks_to_wav(tracks, output, sample_rate=8000, block_size=4)

    data, _ = sf.read(output, dtype="float32", always_2d=True)
    assert np.allclose(data, 0.0, atol=2e-5)


def test_project_mixdown_resamples_mono_source_and_preserves_duration(tmp_path):
    source = tmp_path / "mono.wav"
    _write(source, np.asarray([0.0, 1.0], dtype=np.float32), sample_rate=4000)
    output = tmp_path / "mix.wav"

    mixdown_tracks_to_wav(
        [_track(source)], output, sample_rate=8000, block_size=2
    )
    data, rate = sf.read(output, dtype="float32", always_2d=True)

    assert rate == 8000
    assert data.shape == (4, 2)
    assert np.allclose(data[:, 0], data[:, 1], atol=2e-5)
    assert np.allclose(data[:, 0], [0.0, 0.5, 1.0, 1.0], atol=2e-5)


def test_project_mixdown_does_not_replace_existing_output_on_invalid_source(tmp_path):
    output = tmp_path / "mix.wav"
    output.write_bytes(b"existing-output")
    missing = tmp_path / "missing.wav"

    with pytest.raises(RuntimeError, match="音声データ"):
        mixdown_tracks_to_wav(
            [_track(missing)], output, sample_rate=8000, block_size=4
        )

    assert output.read_bytes() == b"existing-output"


def test_project_mixdown_refuses_to_overwrite_a_source_file(tmp_path):
    source = tmp_path / "source.wav"
    _write(source, np.ones((8, 2), dtype=np.float32))
    original = source.read_bytes()

    with pytest.raises(ValueError, match="入力音声ファイル"):
        mixdown_tracks_to_wav(
            [_track(source)], source, sample_rate=8000, block_size=4
        )

    assert source.read_bytes() == original


def test_project_mixdown_cancel_keeps_previous_output(tmp_path):
    source = tmp_path / "source.wav"
    _write(source, np.ones((16, 2), dtype=np.float32))
    output = tmp_path / "mix.wav"
    output.write_bytes(b"old-mix")

    with pytest.raises(RuntimeError, match="キャンセル"):
        mixdown_tracks_to_wav(
            [_track(source)],
            output,
            sample_rate=8000,
            block_size=4,
            cancel_check=lambda: True,
        )

    assert output.read_bytes() == b"old-mix"
