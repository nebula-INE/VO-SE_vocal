import main
import pytest
import os

@pytest.mark.smoke
def test_runtime_requirement_check_reports_python_packages(monkeypatch):
    monkeypatch.setattr(main.platform, "system", lambda: "UnknownOS")
    monkeypatch.setattr(main, "find_spec", lambda module_name: None)

    missing = main._check_runtime_requirements()

    assert "Python package: PySide6" in missing
    assert "Python package: sounddevice" in missing
    assert "Python package: soundfile" in missing


def test_runtime_requirement_check_reports_linux_os_libraries(monkeypatch):
    monkeypatch.setattr(main.platform, "system", lambda: "Linux")
    monkeypatch.setattr(main, "find_spec", lambda module_name: object())
    monkeypatch.setattr(main, "_is_os_library_loadable", lambda library_name: False)

    missing = main._check_runtime_requirements()

    assert "OS library: libGL.so.1 (libgl1)" in missing
    assert "OS library: libxcb-cursor.so.0 (libxcb-cursor0)" in missing
    assert "OS library: libportaudio.so.2 (libportaudio2)" in missing


def test_linux_runtime_hint_uses_runtime_audio_packages():
    hint = main.OS_DEPENDENCY_INSTALL_HINTS["Linux"]
    assert "libsndfile1" in hint
    assert "libportaudio2" in hint
    assert "portaudio19-dev" not in hint


def test_runtime_requirement_check_skips_available_libraries(monkeypatch):
    monkeypatch.setattr(main.platform, "system", lambda: "Linux")
    monkeypatch.setattr(main, "find_spec", lambda module_name: object())
    monkeypatch.setattr(main, "_is_os_library_loadable", lambda library_name: True)

    assert main._check_runtime_requirements() == []


def test_runtime_requirement_check_skips_macos_bundled_os_libraries(monkeypatch):
    monkeypatch.setattr(main.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(main, "find_spec", lambda module_name: object())
    monkeypatch.setattr(main.sys, "frozen", True, raising=False)
    monkeypatch.setattr(main, "_is_os_library_loadable", lambda library_name: False)

    assert main._check_runtime_requirements() == []


def test_engine_library_path_uses_linux_shared_object(monkeypatch):
    monkeypatch.setattr(main.platform, "system", lambda: "Linux")
    monkeypatch.setattr(main.os.path, "exists", lambda path: False)

    engine_path = main.get_engine_library_path()

    # プラットフォームに依存しないパス区切り文字を使う
    expected_suffix = os.path.join("bin", "libvose_core.so")
    assert engine_path.endswith(expected_suffix)


def test_app_initializer_accepts_linux_core_names(monkeypatch, tmp_path):
    from modules.utils.initializer import AppInitializer
    import modules.utils.initializer as initializer

    bin_dir = tmp_path / "bin"
    open_jtalk_dir = bin_dir / "open_jtalk"
    models_dir = tmp_path / "models"
    open_jtalk_dir.mkdir(parents=True)
    models_dir.mkdir()
    (bin_dir / "libvose_core.so").write_text("", encoding="utf-8")
    (open_jtalk_dir / "open_jtalk").write_text("", encoding="utf-8")
    (models_dir / "onset_detector.onnx").write_text("", encoding="utf-8")

    monkeypatch.setattr(initializer.sys, "platform", "linux")
    monkeypatch.setattr(initializer.sys, "frozen", True, raising=False)
    monkeypatch.setattr(initializer.sys, "_MEIPASS", str(tmp_path), raising=False)

    ok, message = AppInitializer.check_environment()

    assert ok is True
    assert message == "All clear"
