from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_native_cancelable_render_api_is_exposed():
    header = (ROOT / "include" / "vose_core.h").read_text(encoding="utf-8")
    ffi = (ROOT / "modules" / "ffi" / "vose_api.py").read_text(encoding="utf-8")

    assert "execute_render_cancelable" in header
    assert "VoseProgressCallback" in header
    assert "VoseCancelCheckCallback" in header
    assert '"execute_render_cancelable"' in ffi


def test_desktop_progress_stays_monotonic_across_native_boundary():
    patch = (ROOT / "modules" / "audio" / "vo_se_engine_patch.py").read_text(encoding="utf-8")

    # Python-side NoteEvent preparation ends at 20%, then native 2..100%
    # is mapped into 20..100%, avoiding the old 90% -> 2% regression.
    assert "8 + int(((i + 1) / max(note_count, 1)) * 12.0)" in patch
    assert "20 + int(" in patch
    assert "native_pct" in patch
    assert "if pct < last_progress:" in patch
    assert "execute_render_cancelable" in patch


def test_desktop_render_checks_output_after_native_completion():
    patch = (ROOT / "modules" / "audio" / "vo_se_engine_patch.py").read_text(encoding="utf-8")

    assert 'if not os.path.exists(file_path):' in patch
    assert 'raise RuntimeError("レンダリング結果の WAV が生成されませんでした。")' in patch
    assert "report_progress(100)" in patch


def test_desktop_v2_render_retries_direct_oto_alias_before_silence():
    source = Path("modules/audio/vo_se_engine_patch.py").read_text(encoding="utf-8")
    assert "direct_entry = oto_parser.resolve_alias(" in source
    assert 'getattr(oto_parser, "_db", {}).get(' in source
    assert "resolved_voice_count = 0" in source
    assert "音源WAVを1件も解決できませんでした" in source
    assert "os.path.isfile(wav_path)" in source
