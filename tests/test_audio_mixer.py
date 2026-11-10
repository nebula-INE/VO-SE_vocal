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


def _memory_source(mixer, track, data, sample_rate=None):
    stereo = mixer._to_stereo(np.asarray(data, dtype=np.float32))
    mixer._tracks = [track]
    mixer._sources = {
        id(track): {
            "path": "",
            "sample_rate": int(sample_rate or mixer.sample_rate),
            "frames": len(stereo),
            "array": stereo,
        }
    }


def _collect_blocks(mixer):
    mixer._producer_loop()
    # The synchronous producer has finished; inspect queued blocks without
    # consuming them so callback tests can exercise the real delivery path.
    return list(mixer._queue.queue)


def _render_callback(mixer, frames, monkeypatch):
    import modules.backend.audio_mixer as audio_mixer_module

    class CallbackStop(Exception):
        pass

    monkeypatch.setattr(
        audio_mixer_module,
        "sd",
        SimpleNamespace(CallbackStop=CallbackStop),
    )
    output = np.zeros((frames, 2), dtype=np.float32)
    try:
        mixer._audio_callback(output, frames, None, None)
    except CallbackStop:
        return output, True
    return output, False


def test_mute_and_solo_activity_rules():
    normal = _track()
    muted = _track(is_muted=True)
    solo = _track(is_solo=True)

    assert AudioMixer._track_is_active(normal, solo_exists=False)
    assert not AudioMixer._track_is_active(muted, solo_exists=False)
    assert AudioMixer._track_is_active(solo, solo_exists=True)
    assert not AudioMixer._track_is_active(normal, solo_exists=True)


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


def test_producer_streams_mixed_blocks_without_full_file_buffers(monkeypatch):
    mixer = AudioMixer(sample_rate=4, block_size=2)
    audible = _track(volume=0.5)
    muted = _track(is_muted=True)
    _memory_source(mixer, audible, np.ones((4, 2), dtype=np.float32))
    muted_data = np.full((4, 2), 0.75, dtype=np.float32)
    mixer._sources[id(muted)] = {
        "path": "",
        "sample_rate": 4,
        "frames": len(muted_data),
        "array": muted_data,
    }
    mixer._tracks = [audible, muted]

    blocks = _collect_blocks(mixer)

    assert len(blocks) == 2
    assert all(data.shape == (2, 2) for _, data, _ in blocks)
    assert all(valid == 2 for _, _, valid in blocks)
    assert np.allclose(blocks[0][1], 0.5 * np.sqrt(0.5))
    assert np.allclose(blocks[1][1], 0.5 * np.sqrt(0.5))
    assert mixer._producer_done.is_set()


def test_callback_stops_only_after_last_valid_frame(monkeypatch):
    mixer = AudioMixer(sample_rate=4, block_size=4)
    track = _track()
    _memory_source(
        mixer,
        track,
        np.asarray([[0.25, 0.5], [0.5, 0.75], [0.75, 1.0]], dtype=np.float32),
    )
    _collect_blocks(mixer)

    output, stopped = _render_callback(mixer, 4, monkeypatch)

    assert stopped
    expected = np.asarray(
        [[0.25, 0.5], [0.5, 0.75], [0.75, 1.0]],
        dtype=np.float32,
    ) * np.sqrt(0.5)
    assert np.allclose(output[:3], expected)
    assert np.allclose(output[3], 0.0)
    assert mixer.position_sec == 0.75


def test_all_muted_tracks_keep_timeline_running_until_audio_ends(monkeypatch):
    mixer = AudioMixer(sample_rate=4, block_size=2)
    muted = _track(is_muted=True)
    _memory_source(mixer, muted, np.ones((4, 2), dtype=np.float32))
    _collect_blocks(mixer)

    first, stopped_first = _render_callback(mixer, 2, monkeypatch)
    assert not stopped_first
    assert np.allclose(first, 0.0)
    assert mixer.position_sec == 0.5

    second, stopped_second = _render_callback(mixer, 2, monkeypatch)
    assert stopped_second
    assert np.allclose(second, 0.0)
    assert mixer.position_sec == 1.0


def test_callback_solo_excludes_non_solo_tracks(monkeypatch):
    mixer = AudioMixer(sample_rate=4, block_size=4)
    normal = _track(volume=1.0)
    solo = _track(volume=0.25, is_solo=True)
    normal_data = np.ones((4, 2), dtype=np.float32)
    solo_data = np.ones((4, 2), dtype=np.float32)
    mixer._tracks = [normal, solo]
    mixer._sources = {
        id(normal): {
            "path": "",
            "sample_rate": 4,
            "frames": 4,
            "array": normal_data,
        },
        id(solo): {
            "path": "",
            "sample_rate": 4,
            "frames": 4,
            "array": solo_data,
        },
    }
    _collect_blocks(mixer)

    output, _stopped = _render_callback(mixer, 4, monkeypatch)
    assert np.allclose(output, 0.25 * np.sqrt(0.5))


def test_metadata_loading_does_not_decode_the_entire_audio_file(monkeypatch):
    import modules.backend.audio_mixer as audio_mixer_module

    class Info:
        samplerate = 48000
        frames = 48000 * 60 * 20

    class NoFullReadSoundFile:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("files should only be opened by the producer")

    monkeypatch.setattr(audio_mixer_module, "sf", SimpleNamespace(
        info=lambda _path: Info(),
        SoundFile=NoFullReadSoundFile,
    ))
    track = _track(audio_path="long.wav")
    mixer = AudioMixer(sample_rate=44100, block_size=1024)

    mixer.set_tracks([track])

    source = mixer._sources[id(track)]
    assert source["frames"] == 48000 * 60 * 20
    assert source["array"] is None


def test_callback_underflow_is_counted_and_does_not_block(monkeypatch):
    mixer = AudioMixer(sample_rate=4, block_size=2)
    mixer._producer_ready.set()
    mixer._producer_done.clear()
    output = np.ones((2, 2), dtype=np.float32)

    mixer._audio_callback(output, 2, None, None)

    assert np.allclose(output, 0.0)
    assert mixer.output_underflow_count == 1
    assert mixer.position_sec == 0.5



def test_file_sources_are_read_in_small_blocks_on_the_producer(monkeypatch):
    import modules.backend.audio_mixer as audio_mixer_module

    source_data = (np.arange(40, dtype=np.float32) * 0.01).reshape(20, 2)
    read_sizes = []

    class FakeSoundFile:
        def __init__(self, _path, mode="r"):
            assert mode == "r"
            self.position = 0

        def seek(self, frame):
            self.position = frame

        def read(self, frames, dtype, always_2d):
            assert dtype == "float32"
            assert always_2d
            read_sizes.append(frames)
            result = source_data[self.position:self.position + frames]
            self.position += len(result)
            return result.copy()

        def close(self):
            pass

    class Info:
        samplerate = 4
        frames = len(source_data)

    monkeypatch.setattr(
        audio_mixer_module,
        "sf",
        SimpleNamespace(
            info=lambda _path: Info(),
            SoundFile=FakeSoundFile,
        ),
    )
    mixer = AudioMixer(sample_rate=4, block_size=4, prefetch_blocks=8)
    track = _track(audio_path="long-audio.wav")

    mixer.set_tracks([track])
    assert read_sizes == []
    assert mixer._sources[id(track)]["array"] is None

    blocks = _collect_blocks(mixer)

    assert len(blocks) == 5
    assert read_sizes
    assert max(read_sizes) <= mixer.block_size + 2
    assert mixer._queue.maxsize == 8
    assert all(block[1].shape == (4, 2) for block in blocks)
    streamed = np.concatenate([b[1][:b[2]] for b in blocks])
    assert np.allclose(streamed, source_data * np.sqrt(0.5))



def test_play_uses_background_file_reader_and_queued_callback(monkeypatch):
    import threading
    import modules.backend.audio_mixer as audio_mixer_module

    source_data = np.asarray(
        [[0.1, 0.2], [0.2, 0.3], [0.3, 0.4], [0.4, 0.5]],
        dtype=np.float32,
    )
    read_threads = []

    class Info:
        samplerate = 4
        frames = len(source_data)

    class FakeSoundFile:
        def __init__(self, _path, mode="r"):
            assert mode == "r"
            self.position = 0

        def seek(self, frame):
            self.position = frame

        def read(self, frames, dtype, always_2d):
            read_threads.append(threading.current_thread().name)
            assert dtype == "float32"
            assert always_2d
            result = source_data[self.position:self.position + frames]
            self.position += len(result)
            return result.copy()

        def close(self):
            pass

    class CallbackStop(Exception):
        pass

    class FakeOutputStream:
        def __init__(self, **kwargs):
            self.callback = kwargs["callback"]
            self.active = False
            self.first_output = None
            self.latency = 0.0

        def start(self):
            self.active = True
            self.first_output = np.zeros((2, 2), dtype=np.float32)
            try:
                self.callback(self.first_output, 2, None, None)
            except CallbackStop:
                self.active = False

        def stop(self):
            self.active = False

        def close(self):
            self.active = False

    monkeypatch.setattr(
        audio_mixer_module,
        "sf",
        SimpleNamespace(
            info=lambda _path: Info(),
            SoundFile=FakeSoundFile,
        ),
    )
    monkeypatch.setattr(
        audio_mixer_module,
        "sd",
        SimpleNamespace(
            CallbackStop=CallbackStop,
            OutputStream=FakeOutputStream,
        ),
    )
    mixer = AudioMixer(sample_rate=4, block_size=2)
    mixer.set_tracks([_track(audio_path="short.wav")])

    mixer.play()

    assert read_threads
    assert set(read_threads) == {"VO-SE-AudioProducer"}
    assert np.allclose(
        mixer._stream.first_output,
        source_data[:2] * np.sqrt(0.5),
    )
    mixer.stop()
