"""Regression tests for serialized desktop native rendering."""

import threading
import time

from modules.audio import vo_se_engine_patch


def test_apply_patch_serializes_v2_render_calls(monkeypatch):
    state = {"active": 0, "max_active": 0}
    state_lock = threading.Lock()

    def fake_export(self, *args, **kwargs):
        with state_lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        try:
            time.sleep(0.03)
            return "rendered"
        finally:
            with state_lock:
                state["active"] -= 1

    monkeypatch.setattr(vo_se_engine_patch, "_export_to_wav_v2", fake_export)

    class FakeEngine:
        pass

    vo_se_engine_patch.apply_patch(FakeEngine)

    engine = FakeEngine()
    # Mirrors VO_SE_Engine.__init__; the production engine creates this lock
    # before any UI worker or manual-render action can use it.
    engine._render_lock = threading.RLock()

    results = []
    result_lock = threading.Lock()

    def render():
        value = engine.export_to_wav_v2()
        with result_lock:
            results.append(value)

    workers = [threading.Thread(target=render) for _ in range(8)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=2.0)

    assert all(not worker.is_alive() for worker in workers)
    assert results == ["rendered"] * 8
    assert state["max_active"] == 1
