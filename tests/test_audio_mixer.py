from types import SimpleNamespace

import numpy as np

from modules.backend.audio_mixer import AudioMixer


def _track(**kwargs):
    values = {
        "audio_path": "",
        "playback_path": "",
        "volume": 1.0,
        "pan": 0.0,
        "is_muted": False,
        "is_solo": False,
    }
    values.update(kwargs)
    return SimpleNamespace(**values)


def test_mute_and_solo_activity_rules():
    mixer = AudioMixer()
    normal = _track()
    muted = _track(is_muted=True)
    solo = _track(is_solo=True)

    assert mixer._track_is_active(normal, solo_exists=False)
    assert not mixer._track_is_active(muted, solo_exists=False)
    assert mixer._track_is_active(solo, solo_exists=True)
    assert not mixer._track_is_active(normal, solo_exists=True)


def test_pan_is_centered_and_hard_panned():
    left_center = AudioMixer._pan_gains(-1.0)
    center = AudioMixer._pan_gains(0.0)
    right_center = AudioMixer._pan_gains(1.0)

    assert np.allclose(left_center, (1.0, 0.0), atol=1e-6)
    assert np.allclose(center, (np.sqrt(0.5), np.sqrt(0.5)), atol=1e-6)
    assert np.allclose(right_center, (0.0, 1.0), atol=1e-6)


def test_mono_audio_is_expanded_to_stereo():
    mono = np.asarray([[0.25], [-0.5]], dtype=np.float32)
    stereo = AudioMixer._to_stereo(mono)

    assert stereo.shape == (2, 2)
    assert np.allclose(stereo[:, 0], mono[:, 0])
    assert np.allclose(stereo[:, 1], mono[:, 0])


def test_resample_preserves_channel_count():
    mixer = AudioMixer(sample_rate=44100)
    source = np.zeros((100, 2), dtype=np.float32)
    source[:, 0] = 1.0
    result = mixer._resample(source, 22050)

    assert result.shape == (200, 2)
    assert np.allclose(result[:, 0], 1.0)
    assert np.allclose(result[:, 1], 0.0)


def test_callback_mixes_multiple_tracks_with_mute_and_volume():
    mixer = AudioMixer(sample_rate=4, block_size=4)
    audible = _track(volume=0.5)
    muted = _track(volume=1.0, is_muted=True)

    mixer._tracks = [audible, muted]
    mixer._buffers = {
        id(audible): np.ones((4, 2), dtype=np.float32),
        id(muted): np.ones((4, 2), dtype=np.float32) * 0.75,
    }

    out = np.zeros((4, 2), dtype=np.float32)
    mixer._callback(out, 4, None, None)

    assert np.allclose(out, 0.5)


def test_callback_solo_excludes_non_solo_tracks():
    mixer = AudioMixer(sample_rate=4, block_size=4)
    normal = _track(volume=1.0)
    solo = _track(volume=0.25, is_solo=True)

    mixer._tracks = [normal, solo]
    mixer._buffers = {
        id(normal): np.ones((4, 2), dtype=np.float32),
        id(solo): np.ones((4, 2), dtype=np.float32),
    }

    out = np.zeros((4, 2), dtype=np.float32)
    mixer._callback(out, 4, None, None)

    assert np.allclose(out, 0.25)
