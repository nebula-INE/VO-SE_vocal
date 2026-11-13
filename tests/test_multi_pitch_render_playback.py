# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
from __future__ import annotations

import ctypes
import math
import os
import wave
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from modules.data.data_models import NoteEvent


pytestmark = pytest.mark.integration


def _write_test_wav(path: Path, frequency_hz: float, duration_sec: float = 0.7) -> None:
    """Create a small real PCM WAV that WORLD can analyze reliably."""
    sample_rate = 44100
    count = int(sample_rate * duration_sec)
    fade = max(1, int(sample_rate * 0.02))
    samples = np.sin(
        2.0 * math.pi * frequency_hz * np.arange(count, dtype=np.float64) / sample_rate
    )
    envelope = np.ones(count, dtype=np.float64)
    envelope[:fade] *= np.linspace(0.0, 1.0, fade)
    envelope[-fade:] *= np.linspace(1.0, 0.0, fade)
    pcm = np.clip(samples * envelope * 0.65, -1.0, 1.0)
    pcm16 = (pcm * 32767.0).astype("<i2")

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm16.tobytes())


@pytest.fixture
def multi_pitch_voicebank(tmp_path: Path) -> Path:
    voice_dir = tmp_path / "multi_pitch_voice"
    voice_dir.mkdir()

    # The two files intentionally contain different source pitches so the
    # test cannot pass if the render path silently reuses one pitch layer.
    _write_test_wav(voice_dir / "a_C4.wav", 261.625565)
    _write_test_wav(voice_dir / "a_F4.wav", 349.228231)

    (voice_dir / "prefix.map").write_text(
        "C1\t\t_C4\n"
        "F4\t\t_F4\n",
        encoding="utf-8",
    )
    (voice_dir / "oto.ini").write_text(
        "a_C4.wav=あ_C4,0,20,0,60,20\n"
        "a_F4.wav=あ_F4,0,20,0,60,20\n",
        encoding="utf-8",
    )
    return voice_dir


@pytest.mark.skipif(
    os.environ.get("VOSE_RUN_NATIVE_RENDER_TEST") != "1",
    reason="native render integration tests are opt-in; CI enables them after building vose_core",
)
def test_multi_pitch_render_generates_wav_and_playback_uses_it(
    multi_pitch_voicebank: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from modules.audio import vo_se_engine as engine_module
    from modules.audio.vo_se_engine import VO_SE_Engine
    from modules.audio.vo_se_engine_patch import apply_patch

    engine = VO_SE_Engine(voice_lib_dir=str(multi_pitch_voicebank))
    apply_patch(type(engine))
    engine.refresh_voice_library_v2()

    # First verify the exact resolver decision independently of rendering.
    c4 = engine.oto_parser.resolve_alias("あ", note_num=60)
    f4 = engine.oto_parser.resolve_alias("あ", note_num=65)
    assert c4 is not None
    assert f4 is not None
    assert c4.alias == "あ_C4"
    assert f4.alias == "あ_F4"
    assert c4.wav_path == str(multi_pitch_voicebank / "a_C4.wav")
    assert f4.wav_path == str(multi_pitch_voicebank / "a_F4.wav")

    notes = [
        NoteEvent(note_number=60, start_time=0.0, duration=0.6, lyric="あ"),
        NoteEvent(note_number=65, start_time=0.7, duration=0.6, lyric="あ"),
    ]
    params = {
        "Pitch": [],
        "Gender": [],
        "Tension": [],
        "Breath": [],
    }
    output = tmp_path / "multi_pitch_render.wav"

    # Spy on the actual native call while still executing the real renderer.
    # VO_SE_Engine.lib is Optional[ctypes.CDLL], and ctypes exposes native
    # symbols dynamically. Treat this test-only interception surface as Any
    # after explicitly checking that the native library was loaded.
    native_lib = engine.lib
    assert native_lib is not None, "native VO-SE Core library is required for this integration test"
    native_api = cast(Any, native_lib)
    captured_paths: list[str] = []
    # Newer native cores expose execute_render_cancelable(), which the
    # renderer prefers. Intercept that entry point when available; otherwise
    # retain coverage for older cores that only expose execute_render().
    native_entry_name = (
        "execute_render_cancelable"
        if callable(getattr(native_api, "execute_render_cancelable", None))
        else "execute_render"
    )
    native_entry = getattr(native_api, native_entry_name)

    def execute_render_spy(c_notes, note_count, output_path, mode_flag, *callbacks):
        for i in range(int(note_count)):
            ptr = c_notes[i].wav_path
            captured_paths.append(ctypes.string_at(ptr).decode("utf-8") if ptr else "")
        return native_entry(c_notes, note_count, output_path, mode_flag, *callbacks)

    setattr(native_api, native_entry_name, execute_render_spy)

    rendered = engine.export_to_wav_v2(notes, params, str(output), tempo_bpm=120.0)

    assert rendered == str(output.resolve())
    assert output.is_file()
    assert output.stat().st_size > 44
    assert captured_paths == [
        str(multi_pitch_voicebank / "a_C4.wav"),
        str(multi_pitch_voicebank / "a_F4.wav"),
    ]

    # Read the generated WAV through the same soundfile path used by playback.
    import soundfile as sf

    data, sample_rate = sf.read(str(output), always_2d=False)
    assert sample_rate == 44100
    assert data.size > 0
    assert np.isfinite(data).all()
    assert float(np.max(np.abs(data))) > 1e-4

    # Playback regression: use the real generated WAV, but replace the physical
    # sound device with a spy so CI never needs an audio device.
    playback_calls: list[tuple[np.ndarray, int]] = []

    class FakeSoundDevice:
        @staticmethod
        def play(audio_data, fs):
            playback_calls.append((np.asarray(audio_data), int(fs)))

    monkeypatch.setattr(engine_module, "sd", FakeSoundDevice)
    engine.play(str(output))

    assert len(playback_calls) == 1
    played_data, played_rate = playback_calls[0]
    assert played_rate == sample_rate
    assert played_data.shape == data.shape
    assert np.allclose(played_data, data)



@pytest.mark.skipif(
    os.environ.get("VOSE_RUN_NATIVE_RENDER_TEST") != "1",
    reason="native render integration tests are opt-in; CI enables them after building vose_core",
)
def test_ust_import_timeline_to_multi_pitch_wav_and_playback(
    multi_pitch_voicebank: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise real UST parsing, timeline application, native rendering and WAV playback."""
    from types import MethodType, SimpleNamespace

    from modules.audio import vo_se_engine as engine_module
    from modules.audio.vo_se_engine import VO_SE_Engine
    from modules.audio.vo_se_engine_patch import apply_patch
    from modules.gui.mixins.project_io_mixin import (
        ProjectIOMixin,
        _ExternalProjectLoadWorker,
    )

    ust_path = tmp_path / "multi_pitch_import.ust"
    ust_path.write_bytes(
        (
            "[#VERSION]\n"
            "UST Version 1.2\n"
            "[#SETTING]\n"
            "Tempo=120.000\n"
            "[#0000]\n"
            "Length=480\n"
            "Lyric=あ\n"
            "NoteNum=60\n"
            "[#0001]\n"
            "Length=360\n"
            "Lyric=R\n"
            "NoteNum=60\n"
            "[#0002]\n"
            "Length=480\n"
            "Lyric=あ\n"
            "NoteNum=65\n"
            "[#TRACKEND]\n"
        ).encode("cp932")
    )

    # Use the production QRunnable parsing entry point, without requiring a
    # visible Qt window in CI.
    loader = _ExternalProjectLoadWorker(str(ust_path))
    parsed = []
    failures = []
    loader.signals.completed.connect(
        lambda path, payload: parsed.append((path, payload))
    )
    loader.signals.failed.connect(
        lambda path, message: failures.append((path, message))
    )
    loader.run()
    assert loader.done.is_set()
    assert failures == []
    assert len(parsed) == 1

    class FakeTimeline:
        def __init__(self):
            self.notes_list = []
            self.tempo = 120.0

        def set_notes(self, notes):
            self.notes_list = list(notes)

        def update(self):
            pass

    class FakeStatusBar:
        def showMessage(self, *_args):
            pass

    window = SimpleNamespace(
        timeline_widget=FakeTimeline(),
        graph_editor_widget=None,
        tracks=[SimpleNamespace(notes=[])],
        current_track_idx=0,
        history=None,
        vo_se_engine=None,
        current_playback_time=0.0,
        current_voice_id="NONE",
        current_voice="未選択",
        current_project_path="",
        _project_load_worker=loader,
    )
    window.statusBar = lambda: FakeStatusBar()
    for method_name in (
        "_capture_project_edit_state",
        "_record_project_state_edit",
        "_apply_project_tempo",
        "load_ust_file",
    ):
        setattr(
            window,
            method_name,
            MethodType(getattr(ProjectIOMixin, method_name), window),
        )
    window._on_external_project_load_failed = lambda path, message: (
        pytest.fail(f"UST import failed for {path}: {message}")
    )
    window._on_external_project_loaded = MethodType(
        ProjectIOMixin._on_external_project_loaded, window
    )
    window._on_external_project_loaded(*parsed[0])

    notes = window.timeline_widget.notes_list
    assert len(notes) == 2
    assert [note.lyric for note in notes] == ["あ", "あ"]
    assert [note.note_number for note in notes] == [60, 65]
    assert abs(notes[0].start_time - 0.0) < 1e-9
    assert abs(notes[0].duration - 0.5) < 1e-9
    # The rest section must not become a piano-roll node, but its 360 ticks
    # remain in the second note's absolute position.
    assert abs(notes[1].start_time - 0.875) < 1e-9
    assert abs(notes[1].duration - 0.5) < 1e-9
    assert window.tracks[0].notes == notes
    assert window.timeline_widget.tempo == 120.0

    # Now send the exact NoteEvents produced by the UST import path through the
    # actual C++ core. The two notes must pick separate prefix.map pitch layers.
    engine = VO_SE_Engine(voice_lib_dir=str(multi_pitch_voicebank))
    apply_patch(type(engine))
    engine.refresh_voice_library_v2()

    native_lib = engine.lib
    assert native_lib is not None, "native VO-SE Core library is required for this integration test"
    native_api = cast(Any, native_lib)
    captured_paths: list[str] = []
    native_entry_name = (
        "execute_render_cancelable"
        if callable(getattr(native_api, "execute_render_cancelable", None))
        else "execute_render"
    )
    native_entry = getattr(native_api, native_entry_name)

    def execute_render_spy(c_notes, note_count, output_path, mode_flag, *callbacks):
        for index in range(int(note_count)):
            pointer = c_notes[index].wav_path
            captured_paths.append(
                ctypes.string_at(pointer).decode("utf-8") if pointer else ""
            )
        return native_entry(c_notes, note_count, output_path, mode_flag, *callbacks)

    setattr(native_api, native_entry_name, execute_render_spy)

    params = {
        "Pitch": [],
        "Gender": [],
        "Tension": [],
        "Breath": [],
    }
    output = tmp_path / "ust_import_native_render.wav"
    rendered = engine.export_to_wav_v2(
        notes,
        params,
        str(output),
        tempo_bpm=window.timeline_widget.tempo,
    )

    assert rendered == str(output.resolve())
    assert output.is_file()
    assert output.stat().st_size > 44
    assert captured_paths == [
        str(multi_pitch_voicebank / "a_C4.wav"),
        str(multi_pitch_voicebank / "a_F4.wav"),
    ]

    import soundfile as sf

    audio, sample_rate = sf.read(str(output), always_2d=False)
    info = sf.info(str(output))
    assert sample_rate == 44100
    assert abs(float(info.duration) - 1.375) <= 0.03
    assert audio.size > 0
    assert np.isfinite(audio).all()
    assert float(np.max(np.abs(audio))) > 1e-4

    # Playback reads the generated WAV; only the physical output device is
    # replaced, so CI validates the actual data passed to the playback API.
    playback_calls: list[tuple[np.ndarray, int]] = []

    class FakeSoundDevice:
        @staticmethod
        def play(audio_data, fs):
            playback_calls.append((np.asarray(audio_data), int(fs)))

    monkeypatch.setattr(engine_module, "sd", FakeSoundDevice)
    engine.play(str(output))

    assert len(playback_calls) == 1
    played_data, played_rate = playback_calls[0]
    assert played_rate == sample_rate
    assert played_data.shape == audio.shape
    assert np.allclose(played_data, audio)
