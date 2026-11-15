import threading
import time
from pathlib import Path
from typing import Any, Callable

from modules.audio import vo_se_engine_patch


def test_apply_patch_serializes_atomic_v2_render_calls(monkeypatch, tmp_path):
    state = {"active": 0, "max_active": 0}
    state_lock = threading.Lock()

    def fake_render(self, notes, parameters, file_path, *args, **kwargs):
        with state_lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        try:
            time.sleep(0.03)
            Path(file_path).write_bytes(b"test-wav")
            return file_path
        finally:
            with state_lock:
                state["active"] -= 1

    monkeypatch.setattr(vo_se_engine_patch, "_render_to_wav_v2", fake_render)

    class FakeEngine:
        _render_lock: Any
        export_to_wav_v2: Callable[..., str]

    vo_se_engine_patch.apply_patch(FakeEngine)
    engines = [FakeEngine(), FakeEngine()]
    for engine in engines:
        engine._render_lock = threading.RLock()

    destination = tmp_path / "render.wav"
    results = []
    result_lock = threading.Lock()

    def render(index):
        engine = engines[index % len(engines)]
        value = engine.export_to_wav_v2([], {}, str(destination))
        with result_lock:
            results.append(value)

    workers = [
        threading.Thread(target=render, args=(index,))
        for index in range(8)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=2.0)

    assert all(not worker.is_alive() for worker in workers)
    assert results == [str(destination)] * 8
    assert state["max_active"] == 1
    assert destination.read_bytes() == b"test-wav"



def test_load_ust_project_replaces_voice_root_under_native_render_lock(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    voice_dir = tmp_path / "voicebank"
    voice_dir.mkdir()

    class FakeParser:
        def load(self, path):
            return SimpleNamespace(voice_dir=str(voice_dir), tempo=120.0)

    class TrackingLock:
        active = False

        def __enter__(self):
            self.active = True
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self.active = False

    native_lock = TrackingLock()

    class FakeEngine:
        def __init__(self):
            object.__setattr__(self, "voice_lib_path", str(tmp_path / "old"))
            object.__setattr__(self, "_ust_voice_dir", "")

        def __setattr__(self, name, value):
            if name in {"voice_lib_path", "_ust_voice_dir"}:
                assert native_lock.active
            object.__setattr__(self, name, value)

    refresh_calls = []

    monkeypatch.setattr(vo_se_engine_patch, "_NATIVE_ENGINE_LOCK", native_lock)
    monkeypatch.setattr(vo_se_engine_patch, "UstParser", FakeParser)
    monkeypatch.setattr(
        vo_se_engine_patch.UstConverter,
        "to_note_dicts",
        staticmethod(lambda _project: []),
    )
    monkeypatch.setattr(
        vo_se_engine_patch,
        "_refresh_voice_library_v2_unlocked",
        lambda engine: refresh_calls.append(engine.voice_lib_path),
    )

    engine = FakeEngine()
    result = vo_se_engine_patch._load_ust_project(engine, str(tmp_path / "test.ust"))

    assert result == []
    assert engine.voice_lib_path == str(voice_dir)
    assert engine._ust_voice_dir == str(voice_dir)
    assert refresh_calls == [str(voice_dir)]
