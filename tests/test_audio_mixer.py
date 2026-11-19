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
    mixer._track_total_frames = max(source.output_frames for source in sources.values())
    mixer._total_frames = mixer._track_total_frames + mixer._effects.tail_frames


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

    def query_output_device(kind=None):
        assert kind == "output"
        return {
            "name": "Mono Output",
            "max_output_channels": 1,
            "default_samplerate": 48000.0,
        }

    def create_output_stream(**kwargs):
        return FakeStream(**kwargs)

    fake_sound_device = SimpleNamespace(
        CallbackStop=CallbackStopForTest,
        query_devices=query_output_device,
        OutputStream=create_output_stream,
    )
    monkeypatch.setattr(audio_mixer_module, "sd", fake_sound_device)
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



def test_master_compressor_processes_the_combined_track_bus():
    mixer = AudioMixer(sample_rate=1000, block_size=256)
    first = _track()
    second = _track()
    _install_memory_sources(
        mixer,
        (first, np.full((256, 2), 0.5, dtype=np.float32)),
        (second, np.full((256, 2), 0.5, dtype=np.float32)),
    )
    unprocessed = mixer._mix_block(0, 256)

    mixer.set_effect_chain([{
        "id": "compressor-main",
        "type": "compressor",
        "enabled": True,
        "params": {
            "threshold_db": -12.0,
            "ratio": 8.0,
            "attack_ms": 2.0,
            "release_ms": 80.0,
            "makeup_gain_db": 0.0,
        },
    }])
    processed = mixer._mix_block(0, 256)

    assert processed.shape == unprocessed.shape
    assert float(np.mean(np.abs(processed[-100:]))) < float(
        np.mean(np.abs(unprocessed[-100:]))
    )
    assert mixer.get_diagnostics()["enabled_effects"] == 1


def test_master_reverb_extends_project_duration_and_renders_its_tail():
    mixer = AudioMixer(sample_rate=8000, block_size=128)
    track = _track()
    impulse = np.zeros((128, 2), dtype=np.float32)
    impulse[0] = (0.8, 0.8)
    _install_memory_sources(mixer, (track, impulse))
    track_frames = mixer._total_frames

    mixer.set_effect_chain([{
        "id": "reverb-main",
        "type": "reverb",
        "enabled": True,
        "params": {
            "room_size": 0.55,
            "damping": 0.4,
            "wet": 0.5,
            "dry": 0.5,
            "width": 0.8,
        },
    }])
    assert mixer._total_frames > track_frames

    mixer._mix_block(0, 128)
    tail = mixer._mix_block(128, 512)
    assert float(np.max(np.abs(tail))) > 1e-5



def test_desktop_audio_mixer_default_matches_visible_buffer_setting():
    mixer = AudioMixer(sample_rate=44100)
    assert mixer.block_size == 256


def test_set_block_size_restarts_active_stream_and_preserves_position(monkeypatch):
    import modules.backend.audio_mixer as audio_mixer_module

    created_streams = []

    class FakeStream:
        def __init__(self, **kwargs):
            self.blocksize = kwargs["blocksize"]
            self.callback = kwargs["callback"]
            self.channels = kwargs["channels"]
            self.active = False
            created_streams.append(self)

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

        def close(self):
            self.active = False

    class FakeSoundDevice:
        CallbackStop = RuntimeError

        @staticmethod
        def query_devices(kind=None):
            assert kind == "output"
            return {
                "name": "Test Output",
                "max_output_channels": 2,
                "default_samplerate": 64.0,
            }

        @staticmethod
        def OutputStream(**kwargs):
            return FakeStream(**kwargs)

    monkeypatch.setattr(audio_mixer_module, "sd", FakeSoundDevice)
    mixer = AudioMixer(sample_rate=64, block_size=64)
    track = _track()
    _install_memory_sources(
        mixer,
        (track, np.ones((512, 2), dtype=np.float32)),
    )

    mixer.play(0.0)
    first_stream = created_streams[0]
    callback_output = np.zeros((32, 2), dtype=np.float32)
    first_stream.callback(callback_output, 32, None, None)
    assert mixer.position_sec == 0.5

    assert mixer.set_block_size(128) == 128
    assert len(created_streams) == 2
    assert first_stream.active is False
    assert created_streams[1].active is True
    assert created_streams[1].blocksize == 128
    assert mixer.position_sec == 0.5

    mixer.stop()


def test_set_block_size_rejects_unsupported_sizes_without_touching_stream():
    mixer = AudioMixer(sample_rate=44100, block_size=256)

    try:
        mixer.set_block_size(1000)
    except ValueError as exc:
        assert "Unsupported audio block size" in str(exc)
    else:
        raise AssertionError("unsupported block size must be rejected")

    assert mixer.block_size == 256


def test_resume_resets_effect_history_after_discarding_prefetched_blocks(monkeypatch):
    import modules.backend.audio_mixer as audio_mixer_module

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.channels = kwargs["channels"]
            self.active = False

        def start(self):
            self.active = True
            output = np.zeros((32, self.channels), dtype=np.float32)
            self.callback(output, 32, None, None)

        def stop(self):
            self.active = False

        def close(self):
            self.active = False

    class FakeSoundDevice:
        CallbackStop = RuntimeError

        @staticmethod
        def query_devices(kind=None):
            assert kind == "output"
            return {
                "name": "Test Output",
                "max_output_channels": 2,
                "default_samplerate": 64.0,
            }

        @staticmethod
        def OutputStream(**kwargs):
            return FakeStream(**kwargs)

    monkeypatch.setattr(audio_mixer_module, "sd", FakeSoundDevice)
    mixer = AudioMixer(sample_rate=64, block_size=64)
    track = _track()
    _install_memory_sources(
        mixer,
        (track, np.ones((512, 2), dtype=np.float32) * 0.25),
    )
    mixer.set_effect_chain([{
        "id": "resume-reverb",
        "type": "reverb",
        "enabled": True,
        "params": {"wet": 0.5, "dry": 0.5},
    }])

    original_reset = mixer._effects.reset
    reset_count = 0

    def count_reset():
        nonlocal reset_count
        reset_count += 1
        original_reset()

    monkeypatch.setattr(mixer._effects, "reset", count_reset)

    mixer.play(0.0)
    assert mixer.position_sec == 0.5
    mixer.pause()

    # The producer may have processed blocks that were never consumed by the
    # callback. Resuming must start with fresh filter/delay history.
    mixer.play()
    assert reset_count == 2
    assert mixer.position_sec == 1.0
    mixer.stop()



def test_track_compressor_is_applied_before_master_bus_and_only_to_its_track():
    mixer = AudioMixer(sample_rate=1000, block_size=256)
    compressed = _track()
    clean = _track()
    compressed.effects = [{
        "id": "track-compressor",
        "type": "compressor",
        "enabled": True,
        "params": {
            "threshold_db": -12.0,
            "ratio": 8.0,
            "attack_ms": 2.0,
            "release_ms": 80.0,
            "makeup_gain_db": 0.0,
        },
    }]
    _install_memory_sources(
        mixer,
        (compressed, np.full((256, 2), 0.5, dtype=np.float32)),
        (clean, np.full((256, 2), 0.1, dtype=np.float32)),
    )
    mixer._configure_track_effects([compressed, clean])
    mixed = mixer._mix_block(0, 256)

    reference = AudioMixer(sample_rate=1000, block_size=256)
    reference_a = _track()
    reference_b = _track()
    _install_memory_sources(
        reference,
        (reference_a, np.full((256, 2), 0.5, dtype=np.float32)),
        (reference_b, np.full((256, 2), 0.1, dtype=np.float32)),
    )
    reference._configure_track_effects([reference_a, reference_b])
    plain = reference._mix_block(0, 256)

    assert mixed.shape == plain.shape
    assert float(np.mean(np.abs(mixed[-100:]))) < float(np.mean(np.abs(plain[-100:])))


def test_track_reverb_extends_duration_and_renders_its_own_tail():
    mixer = AudioMixer(sample_rate=8000, block_size=128)
    track = _track()
    impulse = np.zeros((128, 2), dtype=np.float32)
    impulse[0] = (0.8, 0.8)
    track.effects = [{
        "id": "track-room",
        "type": "reverb",
        "enabled": True,
        "params": {
            "room_size": 0.55,
            "damping": 0.4,
            "wet": 0.5,
            "dry": 0.5,
            "width": 0.8,
        },
    }]
    _install_memory_sources(mixer, (track, impulse))
    mixer._configure_track_effects([track])
    mixer._track_total_frames = mixer._calculate_track_total_frames(mixer._sources)
    mixer._total_frames = mixer._track_total_frames + mixer._effects.tail_frames

    assert mixer._track_total_frames > len(impulse)
    mixer._mix_block(0, 128)
    tail = mixer._mix_block(128, 512)
    assert float(np.max(np.abs(tail))) > 1e-5


def test_track_effect_chain_is_validated_and_returns_detached_copy():
    mixer = AudioMixer()
    track = _track()
    mixer._tracks = [track]
    chain = [{
        "id": "track-eq",
        "type": "eq",
        "enabled": True,
        "params": {"frequency_hz": 1200.0, "gain_db": 3.0, "q": 1.0},
    }]
    mixer.set_track_effect_chain(track, chain)
    returned = mixer.get_track_effect_chain(track)
    returned[0]["params"]["gain_db"] = 18.0

    assert mixer.get_track_effect_chain(track)[0]["params"]["gain_db"] == 3.0


def test_audio_output_configuration_updates_device_rate_and_buffer(monkeypatch):
    import modules.backend.audio_mixer as audio_mixer_module

    queried = []

    class FakeSoundDevice:
        @staticmethod
        def query_devices(device=None, kind=None):
            queried.append((device, kind))
            if device is None:
                return {"name": "Default", "max_output_channels": 2, "default_samplerate": 44100.0}
            return {"name": "Selected Interface", "max_output_channels": 2, "default_samplerate": 96000.0}

    monkeypatch.setattr(audio_mixer_module, "sd", FakeSoundDevice)
    mixer = AudioMixer(sample_rate=44100, block_size=256)

    diagnostics = mixer.configure_output(
        output_device=3,
        sample_rate=96000,
        block_size=512,
    )

    assert mixer.output_device == 3
    assert mixer.sample_rate == 96000
    assert mixer.block_size == 512
    assert diagnostics["output_device_name"] == "Selected Interface"
    assert diagnostics["sample_rate"] == 96000
    assert diagnostics["block_size"] == 512
    assert queried[-1] == (3, "output")


def test_audio_output_configuration_rolls_back_when_device_is_invalid(monkeypatch):
    import modules.backend.audio_mixer as audio_mixer_module

    class FakeSoundDevice:
        @staticmethod
        def query_devices(device=None, kind=None):
            if device == 7:
                raise OSError("device disappeared")
            return {"name": "Default", "max_output_channels": 2, "default_samplerate": 44100.0}

    monkeypatch.setattr(audio_mixer_module, "sd", FakeSoundDevice)
    mixer = AudioMixer(sample_rate=44100, block_size=256)

    try:
        mixer.configure_output(output_device=7, sample_rate=48000, block_size=512)
    except RuntimeError as exc:
        assert "音声出力デバイス" in str(exc)
    else:
        raise AssertionError("invalid device must be rejected")

    assert mixer.output_device is None
    assert mixer.sample_rate == 44100
    assert mixer.block_size == 256


def test_list_output_devices_filters_input_only_devices(monkeypatch):
    import modules.backend.audio_mixer as audio_mixer_module

    class FakeSoundDevice:
        @staticmethod
        def query_devices():
            return [
                {"name": "Duplex", "max_output_channels": 2, "hostapi": 0, "default_samplerate": 48000},
                {"name": "Input only", "max_output_channels": 0, "hostapi": 0, "default_samplerate": 44100},
            ]

        @staticmethod
        def query_hostapis():
            return [{"name": "WASAPI"}]

    monkeypatch.setattr(audio_mixer_module, "sd", FakeSoundDevice)
    devices = AudioMixer.list_output_devices()

    assert len(devices) == 1
    assert devices[0]["index"] == 0
    assert devices[0]["host_api"] == "WASAPI"



def test_configure_output_preserves_playback_position_across_safe_restart(
    monkeypatch, tmp_path
):
    import soundfile as sf
    import modules.backend.audio_mixer as audio_mixer_module

    created = []

    class FakeStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.channels = kwargs["channels"]
            self.blocksize = kwargs["blocksize"]
            self.latency = 0.025
            self.active = False
            created.append(self)

        def start(self):
            self.active = True

        def stop(self):
            self.active = False

        def close(self):
            self.active = False

    class FakeSoundDevice:
        CallbackStop = RuntimeError

        @staticmethod
        def query_devices(device=None, kind=None):
            return {
                "name": f"Output {device if device is not None else 'Default'}",
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
            }

        @staticmethod
        def OutputStream(**kwargs):
            return FakeStream(**kwargs)

    monkeypatch.setattr(audio_mixer_module, "sd", FakeSoundDevice)
    audio_path = tmp_path / "source.wav"
    sf.write(
        audio_path,
        np.full((512, 2), 0.25, dtype=np.float32),
        64,
        subtype="FLOAT",
    )
    track = _track(audio_path=str(audio_path))
    mixer = AudioMixer(sample_rate=64, block_size=64)
    mixer.set_tracks([track])
    mixer.play(0.0)
    output = np.zeros((32, 2), dtype=np.float32)
    created[0].callback(output, 32, None, None)
    previous_position = mixer.position_sec

    mixer.configure_output(output_device=2, sample_rate=48000, block_size=128)

    assert len(created) == 2
    assert created[0].active is False
    assert created[1].active is True
    assert created[1].blocksize == 128
    assert abs(mixer.position_sec - previous_position) < 1e-6
    assert mixer.get_diagnostics()["output_latency_ms"] == 25.0
    mixer.stop()



def test_audio_output_configuration_rejects_unsupported_device_sample_rate(monkeypatch):
    import modules.backend.audio_mixer as audio_mixer_module

    class FakeSoundDevice:
        @staticmethod
        def query_devices(device=None, kind=None):
            return {
                "name": "48kHz only",
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
            }

        @staticmethod
        def check_output_settings(**kwargs):
            if kwargs["samplerate"] == 96000:
                raise OSError("unsupported sample rate")

    monkeypatch.setattr(audio_mixer_module, "sd", FakeSoundDevice)
    mixer = AudioMixer(sample_rate=44100, block_size=256)

    try:
        mixer.configure_output(output_device=1, sample_rate=96000, block_size=512)
    except RuntimeError as exc:
        assert "対応していません" in str(exc)
    else:
        raise AssertionError("unsupported device sample rate must be rejected")

    assert mixer.sample_rate == 44100
    assert mixer.block_size == 256
    assert mixer.output_device is None


def test_restore_tracks_preserving_sources_rebinds_cloned_track_and_effect_chain(tmp_path):
    import soundfile as sf

    source_path = tmp_path / "history-source.wav"
    audio = np.full((64, 2), 0.2, dtype=np.float32)
    sf.write(source_path, audio, 8000, subtype="FLOAT")

    original = _track(audio_path=str(source_path))
    original.effects = [{
        "id": "history-compressor",
        "type": "compressor",
        "enabled": True,
        "params": {
            "threshold_db": -20.0,
            "ratio": 2.5,
            "attack_ms": 5.0,
            "release_ms": 100.0,
            "makeup_gain_db": 0.0,
        },
    }]
    mixer = AudioMixer(sample_rate=8000, block_size=64)
    mixer.set_tracks([original])
    source_before = mixer._sources[id(original)]

    restored = _track(audio_path=str(source_path), volume=0.6)
    restored.effects = [{
        "id": "history-eq",
        "type": "eq",
        "enabled": True,
        "params": {"frequency_hz": 1800.0, "gain_db": 2.0, "q": 1.0},
    }]
    mixer.restore_tracks_preserving_sources([restored])

    assert mixer._tracks == [restored]
    assert id(restored) in mixer._sources
    assert mixer._sources[id(restored)].track is restored
    assert mixer._sources[id(restored)].path == source_before.path
    assert mixer.get_track_effect_chain(restored) == restored.effects
    assert mixer._track_total_frames == source_before.output_frames


def test_saved_output_device_resolves_by_name_and_host_api_after_index_changes():
    devices = [
        {"index": 0, "name": "USB Headset", "host_api": "WASAPI"},
        {"index": 4, "name": "Studio Interface", "host_api": "ASIO"},
    ]

    resolved = AudioMixer.resolve_output_device(
        devices,
        preferred_index=1,
        preferred_name="Studio Interface",
        preferred_host_api="ASIO",
    )

    assert resolved == 4


def test_saved_output_device_does_not_silently_select_a_different_device():
    devices = [
        {"index": 1, "name": "USB Headset", "host_api": "WASAPI"},
        {"index": 2, "name": "Laptop Speakers", "host_api": "CoreAudio"},
    ]

    resolved = AudioMixer.resolve_output_device(
        devices,
        preferred_index=1,
        preferred_name="Studio Interface",
        preferred_host_api="ASIO",
    )

    assert resolved is None


def test_saved_output_device_uses_existing_index_for_legacy_preferences():
    devices = [
        {"index": 0, "name": "Default", "host_api": "WASAPI"},
        {"index": 3, "name": "USB Headset", "host_api": "WASAPI"},
    ]

    assert AudioMixer.resolve_output_device(devices, 3) == 3
    assert AudioMixer.resolve_output_device(devices, 9) is None


def test_os_default_output_preference_remains_os_default_even_with_saved_device_label():
    devices = [
        {"index": 0, "name": "Current Default", "host_api": "CoreAudio"},
    ]

    assert AudioMixer.resolve_output_device(
        devices,
        preferred_index=None,
        preferred_name="Previously Default",
        preferred_host_api="WASAPI",
    ) is None
