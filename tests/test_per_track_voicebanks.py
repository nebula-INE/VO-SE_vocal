from pathlib import Path
from typing import Any, cast
from types import SimpleNamespace

from modules.audio.vo_se_engine import VO_SE_Engine
from modules.gui.main_window import _VocalPlaybackPreparationThread


def test_playback_preparation_routes_each_track_to_its_voicebank_and_restores_engine(tmp_path):
    voice_a = tmp_path / "voice-a"
    voice_b = tmp_path / "voice-b"
    global_voice = tmp_path / "global-voice"
    for voice in (voice_a, voice_b, global_voice):
        voice.mkdir()
        (voice / "oto.ini").write_text("a.wav=a,0,0,0,0,0\\n", encoding="utf-8")

    engine = cast(Any, object.__new__(VO_SE_Engine))
    engine.voice_lib_path = str(global_voice)
    engine._default_voice_lib_path = str(global_voice)
    engine._vose_desktop_export_ready = True
    engine.active_paths = []
    engine.refresh_paths = []

    def refresh_voice_library_v2():
        engine.refresh_paths.append(engine.voice_lib_path)

    def export_to_wav_v2(notes, parameters, file_path, **kwargs):
        engine.active_paths.append(engine.voice_lib_path)
        Path(file_path).write_bytes(b"test-wav")
        return file_path

    engine.refresh_voice_library_v2 = refresh_voice_library_v2
    engine.export_to_wav_v2 = export_to_wav_v2

    tracks = [SimpleNamespace(name="A"), SimpleNamespace(name="B")]
    jobs = []
    for index, (track, voice) in enumerate(zip(tracks, (voice_a, voice_b))):
        jobs.append({
            "index": index,
            "track": track,
            "notes": [SimpleNamespace(value=index)],
            "parameters": {"Pitch": [], "Gender": [], "Tension": [], "Breath": []},
            "voice_id": str(voice),
            "voice_path": str(voice),
            "tempo": 120.0,
            "previous_signature": "",
            "previous_path": "",
        })

    worker = _VocalPlaybackPreparationThread(engine, jobs)
    completed = []
    failed = []
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.run()

    assert failed == []
    assert len(completed) == 1
    results = completed[0]
    assert len(results) == 2
    assert all(Path(item[1]).is_file() for item in results)
    assert results[0][1] != results[1][1]
    assert engine.active_paths == [str(voice_a), str(voice_b)]
    assert engine.voice_lib_path == str(global_voice)
    assert engine.refresh_paths == [
        str(voice_a), str(global_voice), str(voice_b), str(global_voice)
    ]
