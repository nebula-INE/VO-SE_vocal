from types import SimpleNamespace

import numpy as np

from modules.backend.audio_mixer import AudioMixer, _TrackSource


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


def _install_memory_sources(mixer, *pairs):
    tracks = []
    sources = {}
    for track, raw in pairs:
        stereo = mixer._to_stereo(np.asarray(raw, dtype=np.float32))
        tracks.append(track)
        sources[id(track)] = _TrackSource(
            track=track,
            path="",
            frames=len(stereo),
            sample_rate=mixer.sample_rate,
            output_frames=len(stereo),
            data=stereo,
        )
    mixer._tracks = tracks
    mixer._sources = sources
    mixer._total_frames = max(source.output_frames for source in sources.values())


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
    assert np.allclose(AudioMixer._pan_gains(-1.0), (1.0, 0.0), atol=1e-6)
    assert np.allclose(
        AudioMixer._pan_gains(0.0),
        (np.sqrt(0.5), np.sqrt(0.5)),
        atol=1e-6,
    )
    assert np.allclose(AudioMixer._pan_gains(1.0), (0.0, 1.0), atol=1e-6)


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


def test_mix_block_handles_multiple_tracks_mute_and_volume():
    mixer = AudioMixer(sample_rate=4, block_size=4)
    audible = _track(volume=0.5)
    muted = _track(volume=1.0, is_muted=True)
    _install_memory_sources(
        mixer,
        (audible, np.ones((4, 2), dtype=np.float32)),
        (muted, np.ones((4, 2), dtype=np.float32) * 0.75),
    )

    out = mixer._mix_block(0, 4)
    assert np.allclose(out, 0.5 * np.sqrt(0.5))


def test_all_muted_tracks_keep_timeline_duration():
    mixer = AudioMixer(sample_rate=4, block_size=2)
    muted = _track(is_muted=True)
    _install_memory_sources(mixer, (muted, np.ones((4, 2), dtype=np.float32)))

    first = mixer._mix_block(0, 2)
    second = mixer._mix_block(2, 2)
    assert np.allclose(first, 0.0)
    assert np.allclose(second, 0.0)
    assert mixer._total_frames == 4


def test_mix_block_solo_excludes_non_solo_tracks():
    mixer = AudioMixer(sample_rate=4, block_size=4)
    normal = _track(volume=1.0)
    solo = _track(volume=0.25, is_solo=True)
    _install_memory_sources(
        mixer,
        (normal, np.ones((4, 2), dtype=np.float32)),
        (solo, np.ones((4, 2), dtype=np.float32)),
    )

    out = mixer._mix_block(0, 4)
    assert np.allclose(out, 0.25 * np.sqrt(0.5))


def test_set_tracks_reads_metadata_without_loading_full_audio(monkeypatch):
    import modules.backend.audio_mixer as audio_mixer_module

    class FakeSoundFile:
        @staticmethod
        def info(path):
            return SimpleNamespace(frames=44100 * 60 * 10, samplerate=44100, channels=2)

    monkeypatch.setattr(audio_mixer_module, "sf", FakeSoundFile)
    mixer = AudioMixer(sample_rate=44100)
    track = _track(audio_path="long-file.wav")
    mixer.set_tracks([track])

    source = mixer._sources[id(track)]
    assert source.frames == 44100 * 60 * 10
    assert source.output_frames == 44100 * 60 * 10
    assert source.data is None
    assert mixer._total_frames == source.output_frames


def test_file_block_reader_interpolates_only_requested_frames():
    class FakeReader:
        def __init__(self):
            self.data = np.arange(4, dtype=np.float32)[:, None]
            self.position = 0
            self.last_read_count = 0

        def seek(self, frame):
            self.position = frame

        def read(self, count, dtype, always_2d):
            self.last_read_count = count
            result = self.data[self.position : self.position + count]
            self.position += len(result)
            return result

    mixer = AudioMixer(sample_rate=4, block_size=4)
    source = _TrackSource(
        track=_track(),
        path="test.wav",
        frames=4,
        sample_rate=2,
        output_frames=8,
    )
    reader = FakeReader()

    block = mixer._read_file_block(reader, source, 0, 4)
    assert reader.last_read_count == 3
    assert block.shape == (4, 2)
    assert np.allclose(block[:, 0], [0.0, 0.5, 1.0, 1.5])
    assert np.allclose(block[:, 1], [0.0, 0.5, 1.0, 1.5])


def test_callback_copies_partial_blocks_and_tracks_position(monkeypatch):
    import modules.backend.audio_mixer as audio_mixer_module

    class CallbackStop(Exception):
        pass

    monkeypatch.setattr(
        audio_mixer_module,
        "sd",
        SimpleNamespace(CallbackStop=CallbackStop),
    )
    mixer = AudioMixer(sample_rate=4, block_size=4)
    track = _track()
    _install_memory_sources(mixer, (track, np.ones((4, 2), dtype=np.float32)))
    mixer._blocks.put(np.ones((4, 2), dtype=np.float32))
    mixer._producer_done.set()

    first = np.zeros((2, 2), dtype=np.float32)
    mixer._audio_callback(first, 2, None, None)
    assert np.allclose(first, 1.0)
    assert mixer.position_sec == 0.5

    second = np.zeros((3, 2), dtype=np.float32)
    try:
        mixer._audio_callback(second, 3, None, None)
    except CallbackStop:
        pass
    else:
        raise AssertionError("callback should stop after the final queued block")
    assert np.allclose(second[:2], 1.0)
    assert np.allclose(second[2:], 0.0)
    assert mixer.position_sec == 1.0


def test_callback_records_device_underflow_and_queue_underrun(monkeypatch):
    import modules.backend.audio_mixer as audio_mixer_module

    class CallbackStatus:
        output_underflow = True
        output_overflow = False

        def __str__(self):
            return "output underflow"

    monkeypatch.setattr(
        audio_mixer_module,
        "sd",
        SimpleNamespace(CallbackStop=RuntimeError),
    )
    mixer = AudioMixer(sample_rate=4, block_size=2)
    out = np.ones((2, 2), dtype=np.float32)
    mixer._audio_callback(out, 2, None, CallbackStatus())

    diagnostics = mixer.get_diagnostics()
    assert np.allclose(out, 0.0)
    assert diagnostics["output_underflow_count"] == 1
    assert diagnostics["output_overflow_count"] == 0
    assert diagnostics["queue_underrun_count"] == 1
    assert diagnostics["last_callback_status"] == "output underflow"


def test_stream_queue_has_a_fixed_memory_bound():
    mixer = AudioMixer(queue_blocks=3, block_size=512)
    assert mixer._blocks.maxsize == 3


def test_play_probes_default_output_and_supports_mono_devices(monkeypatch):
    import modules.backend.audio_mixer as audio_mixer_module

    CallbackStopForTest = type("CallbackStopForTest", (Exception,), {})

    created = {}

    class FakeStream:
        def __init__(self, **kwargs):
            self.channels = kwargs["channels"]
            self.callback = kwargs["callback"]
            self.active = False
            created["stream"] = self

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

        def close(self):
            self.active = False

    class FakeSoundDevice:
        CallbackStop = CallbackStopForTest

        @staticmethod
        def query_devices(kind=None):
            assert kind == "output"
            return {
                "name": "Mono Output",
                "max_output_channels": 1,
                "default_samplerate": 48000.0,
            }

        @staticmethod
        def OutputStream(**kwargs):
            return FakeStream(**kwargs)

    monkeypatch.setattr(audio_mixer_module, "sd", FakeSoundDevice)
    mixer = AudioMixer(sample_rate=4, block_size=2)
    track = _track()
    _install_memory_sources(
        mixer,
        (track, np.asarray([[1.0, -1.0], [0.5, -0.5]], dtype=np.float32)),
    )

    mixer.play(0.0)
    stream = created["stream"]
    assert stream.channels == 1
    assert mixer.get_diagnostics()["output_device_name"] == "Mono Output"
    assert mixer.get_diagnostics()["output_default_samplerate"] == 48000.0

    out = np.zeros((2, 1), dtype=np.float32)
    try:
        stream.callback(out, 2, None, None)
    except CallbackStopForTest:
        pass
    assert np.allclose(out[:, 0], 0.0)
    mixer.stop()


def test_play_reports_when_no_default_output_device_is_available(monkeypatch):
    import modules.backend.audio_mixer as audio_mixer_module

    class FakeSoundDevice:
        @staticmethod
        def query_devices(kind=None):
            raise OSError("No default output device")

    monkeypatch.setattr(audio_mixer_module, "sd", FakeSoundDevice)
    mixer = AudioMixer()

    try:
        mixer.play()
    except RuntimeError as exc:
        assert "既定の音声出力デバイスを取得できません" in str(exc)
    else:
        raise AssertionError("missing output device must be reported")
