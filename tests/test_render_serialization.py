import threading
import time
from pathlib import Path

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
        pass

    vo_se_engine_patch.apply_patch(FakeEngine)
    engine = FakeEngine()
    engine._render_lock = threading.RLock()

    destination = tmp_path / "render.wav"
    results = []
    result_lock = threading.Lock()

    def render():
        value = engine.export_to_wav_v2([], {}, str(destination))
        with result_lock:
            results.append(value)

    workers = [threading.Thread(target=render) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=2.0)

    assert all(not worker.is_alive() for worker in workers)
    assert results == [str(destination)] * 8
    assert state["max_active"] == 1
    assert destination.read_bytes() == b"test-wav"
