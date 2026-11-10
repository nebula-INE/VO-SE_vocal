from types import SimpleNamespace

import numpy as np
import soundfile as sf
import pytest

from modules.audio.project_mixdown import mixdown_tracks_to_wav
from modules.gui.mixins.project_io_mixin import _ProjectMixdownWorker


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
        # A path-less track remains in the track-state list but adds no source audio.
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
    expected = np.asarray([0.0, 0.5, 1.0, 1.0], dtype=np.float32) * np.sqrt(0.5)
    assert np.allclose(data[:, 0], expected, atol=2e-5)


def test_project_mixdown_does_not_replace_existing_output_on_invalid_source(tmp_path):
    output = tmp_path / "mix.wav"
    output.write_bytes(b"existing-output")
    missing = tmp_path / "missing.wav"

    with pytest.raises(RuntimeError):
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


def test_project_mixdown_clips_the_combined_signal_like_live_playback(tmp_path):
    first = tmp_path / "first.wav"
    second = tmp_path / "second.wav"
    _write(first, np.full((8, 2), 0.8, dtype=np.float32))
    _write(second, np.full((8, 2), 0.8, dtype=np.float32))
    output = tmp_path / "mix.wav"

    mixdown_tracks_to_wav(
        [_track(first), _track(second)],
        output,
        sample_rate=8000,
        block_size=3,
    )
    data, _ = sf.read(output, dtype="float32", always_2d=True)

    assert data.shape == (8, 2)
    assert np.all(data <= 1.0)
    assert np.all(data >= -1.0)
    assert np.allclose(data, 1.0, atol=2e-5)


def test_project_mixdown_uses_the_longest_track_as_project_duration(tmp_path):
    short_track = tmp_path / "short.wav"
    long_track = tmp_path / "long.wav"
    _write(short_track, np.full((4, 2), 0.25, dtype=np.float32))
    _write(long_track, np.full((9, 2), 0.25, dtype=np.float32))
    output = tmp_path / "mix.wav"

    mixdown_tracks_to_wav(
        [_track(short_track), _track(long_track)],
        output,
        sample_rate=8000,
        block_size=4,
    )
    data, _ = sf.read(output, dtype="float32", always_2d=True)

    assert data.shape == (9, 2)
    assert np.allclose(data[:4], 0.5 * np.sqrt(0.5), atol=2e-5)
    assert np.allclose(data[4:], 0.25 * np.sqrt(0.5), atol=2e-5)


def test_project_mixdown_protects_original_audio_path_behind_vocal_cache(tmp_path):
    original = tmp_path / "guide.wav"
    cached_render = tmp_path / "rendered-vocal.wav"
    _write(original, np.full((8, 2), 0.2, dtype=np.float32))
    _write(cached_render, np.full((8, 2), 0.4, dtype=np.float32))
    vocal = _track(
        original,
        track_type="vocal",
        notes=[object()],
        playback_path=str(cached_render),
    )
    original_bytes = original.read_bytes()

    with pytest.raises(ValueError, match="入力音声ファイル"):
        mixdown_tracks_to_wav(
            [vocal], original, sample_rate=8000, block_size=4
        )

    assert original.read_bytes() == original_bytes


def test_project_worker_renders_each_vocal_then_mixes_audio_tracks(tmp_path):
    audio_path = tmp_path / "instrument.wav"
    _write(audio_path, np.full((16, 2), 0.3, dtype=np.float32), sample_rate=44100)

    vocal_a = _track("", track_type="vocal", audio_path="", notes=[object()], name="Vocal A")
    vocal_b = _track("", track_type="vocal", audio_path="", notes=[object()], name="Vocal B")
    instrument = _track(audio_path, name="Instrument")
    output = tmp_path / "project.wav"

    class FakeEngine:
        def __init__(self):
            self.calls = []

        def export_to_wav_v2(self, notes, parameters, file_path, **kwargs):
            self.calls.append({
                "notes": notes,
                "parameters": parameters,
                "mode_flag": kwargs.get("mode_flag"),
                "tempo_bpm": kwargs.get("tempo_bpm"),
            })
            value = 0.1 if len(self.calls) == 1 else 0.2
            sf.write(
                file_path,
                np.full((16, 2), value, dtype=np.float32),
                44100,
                subtype="FLOAT",
            )
            callback = kwargs.get("progress_callback")
            if callable(callback):
                callback(100)
            return file_path

    engine = FakeEngine()
    completed = []
    failed = []
    worker = _ProjectMixdownWorker(
        engine,
        [vocal_a, vocal_b, instrument],
        {"Pitch": [], "Gender": [], "Tension": [], "Breath": []},
        120.0,
        str(output),
        mode_flag=1,
    )
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)

    # Calling run() directly gives deterministic unit coverage without audio
    # devices or a real native synthesis library.
    worker.run()

    assert failed == []
    assert completed == [str(output)]
    assert len(engine.calls) == 2
    assert [call["mode_flag"] for call in engine.calls] == [1, 1]
    assert [call["tempo_bpm"] for call in engine.calls] == [120.0, 120.0]

    data, rate = sf.read(output, dtype="float32", always_2d=True)
    assert rate == 44100
    assert data.shape == (16, 2)
    assert np.allclose(data, 0.6 * np.sqrt(0.5), atol=2e-5)
