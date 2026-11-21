import pytest

from modules.audio import vo_se_engine_patch
from modules.audio.vo_se_engine import VO_SE_Engine


def test_public_export_to_wav_forwards_all_v2_options(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from modules.gui.main_window_patch import _patch_engine

    calls: dict[str, object] = {}

    def fake_export_v2(notes, params, path, **kwargs):
        calls["notes"] = notes
        calls["params"] = params
        calls["path"] = path
        calls["kwargs"] = kwargs
        return str(path)

    engine = VO_SE_Engine.__new__(VO_SE_Engine)
    setattr(engine, "export_to_wav_v2", fake_export_v2)
    monkeypatch.setattr(
        vo_se_engine_patch,
        "ensure_desktop_export_ready",
        lambda target: None,
    )

    _patch_engine(SimpleNamespace(vo_se_engine=engine))

    def report_progress(percent: int) -> None:
        return None

    def is_cancelled() -> bool:
        return False

    notes = [object()]
    params = {"Pitch": []}
    destination = tmp_path / "render.wav"

    # Resolve the patched public API dynamically because its compatibility
    # wrapper intentionally accepts renderer-specific keyword extensions.
    export_to_wav = getattr(engine, "export_to_wav")
    result = export_to_wav(
        notes,
        params,
        str(destination),
        mode_flag=1,
        progress_callback=report_progress,
        cancel_check=is_cancelled,
        tempo_bpm=137.5,
    )

    assert result == str(destination)
    assert calls["notes"] is notes
    assert calls["params"] is params
    assert calls["path"] == str(destination)
    assert calls["kwargs"] == {
        "mode_flag": 1,
        "progress_callback": report_progress,
        "cancel_check": is_cancelled,
        "tempo_bpm": 137.5,
    }


def test_ensure_desktop_export_ready_patches_and_refreshes_once(monkeypatch):
    calls = {"patch": 0, "refresh": 0}

    def fake_refresh(self):
        calls["refresh"] += 1

    def fake_export(self, notes, parameters, file_path, **kwargs):
        return file_path

    def fake_apply_patch(engine_class):
        calls["patch"] += 1
        monkeypatch.setattr(
            engine_class, "refresh_voice_library_v2", fake_refresh, raising=False
        )
        monkeypatch.setattr(
            engine_class, "export_to_wav_v2", fake_export, raising=False
        )

    monkeypatch.setattr(vo_se_engine_patch, "apply_patch", fake_apply_patch)
    engine = VO_SE_Engine.__new__(VO_SE_Engine)

    vo_se_engine_patch.ensure_desktop_export_ready(engine)
    vo_se_engine_patch.ensure_desktop_export_ready(engine)

    assert calls == {"patch": 1, "refresh": 1}
    assert engine._vose_desktop_export_ready is True
    assert callable(engine.export_to_wav_v2)


def test_ensure_desktop_export_ready_rejects_non_vose_engine():
    with pytest.raises(TypeError, match="VO_SE_Engine が必要"):
        vo_se_engine_patch.ensure_desktop_export_ready(object())


def test_ensure_desktop_export_ready_rejects_inconsistent_ready_state(monkeypatch):
    engine = VO_SE_Engine.__new__(VO_SE_Engine)
    engine._vose_desktop_export_ready = True
    monkeypatch.setattr(VO_SE_Engine, "export_to_wav_v2", None)

    with pytest.raises(RuntimeError, match="初期化状態が不正"):
        vo_se_engine_patch.ensure_desktop_export_ready(engine)
