# tests/test_linux_engine_build.py
import ctypes
import sys
from pathlib import Path

import pytest

from modules.ffi import CNoteEvent, validate_note_event_layout

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.smoke
def test_linux_build_script_exists_and_contains_vose_core():
    """Linux エンジンビルドスクリプトが存在し、正しいターゲットを含むことを確認する"""
    script_path = REPO_ROOT / "scripts" / "build_linux_engine.sh"
    assert script_path.exists(), f"build_linux_engine.sh not found at {script_path}"

    content = script_path.read_text(encoding="utf-8")
    # CMake 呼び出しと vose_core ターゲット、出力パスが含まれているか
    assert "cmake -S" in content
    assert "--target vose_core" in content
    assert "bin/libvose_core.so" in content
    # 最終的にロードテストを行う Python コードも含まれている
    assert "ctypes.CDLL(engine_path)" in content


@pytest.mark.smoke
def test_linux_build_script_is_executable():
    """ビルドスクリプトに実行権限があることを確認（CI 環境向け）"""
    script_path = REPO_ROOT / "scripts" / "build_linux_engine.sh"
    assert script_path.exists()
    # 実際のパーミッションチェック（Unix のみ）
    if not script_path.is_symlink():
        import os

        assert os.access(script_path, os.X_OK), "build_linux_engine.sh is not executable"


@pytest.mark.smoke
def test_cmake_lists_for_vose_core_if_present():
    """
    もしルートの CMakeLists.txt が存在すれば、vose_core が定義されているかを確認する。
    ただし、存在しなければスキップ（現在のプロジェクトでは存在しない可能性が高い）。
    """
    cmake_file = REPO_ROOT / "CMakeLists.txt"
    if not cmake_file.exists():
        pytest.skip("CMakeLists.txt not found at repository root")

    # バイナリファイルとして扱われるケースがあるので、テキストとして読み込めるか確認
    try:
        content = cmake_file.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        pytest.skip("CMakeLists.txt appears to be binary, skipping content test")

    # vose_core ターゲットの定義があるか（オプション）
    if "add_library(vose_core SHARED" in content:
        assert "OUTPUT_NAME \"vose_core\"" in content
        assert "LIBRARY_OUTPUT_DIRECTORY" in content


def test_linux_build_script_verifies_ctypes_load():
    """ビルドスクリプト内で ctypes によるロードテストが行われていることを確認する"""
    script_path = REPO_ROOT / "scripts" / "build_linux_engine.sh"
    if not script_path.exists():
        pytest.skip("build_linux_engine.sh not found")
    content = script_path.read_text(encoding="utf-8")
    assert "ctypes.CDLL(engine_path)" in content


@pytest.mark.skipif(sys.platform != 'linux', reason="Linux固有のABIテストのため")
def test_python_note_event_matches_cpp_linux_engine_abi():
    """CNoteEvent のレイアウトが C++ の NoteEvent と一致することを確認する"""
    validate_note_event_layout()

    # 各フィールドのオフセットが正しいか簡易チェック（より詳細な検証は validate_note_event_layout で行う）
    assert CNoteEvent.pitch_curve.offset == ctypes.sizeof(ctypes.c_void_p)
    assert CNoteEvent.vibrato_depth_curve.offset > CNoteEvent.breath_curve.offset
    assert CNoteEvent.vibrato_curve_length.offset > CNoteEvent.vibrato_rate_curve.offset
    assert CNoteEvent.portamento_offsets.offset > CNoteEvent.vibrato_curve_length.offset
    assert CNoteEvent.portamento_length.offset > CNoteEvent.portamento_offsets.offset
    assert CNoteEvent.intensity.offset > CNoteEvent.portamento_length.offset
    assert CNoteEvent.modulation.offset > CNoteEvent.intensity.offset
