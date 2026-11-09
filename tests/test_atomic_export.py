from pathlib import Path

import pytest

from modules.audio.atomic_export import render_wav_atomically


def test_atomic_export_replaces_target_only_after_success(tmp_path):
    target = tmp_path / "voice.wav"
    target.write_bytes(b"previous-good-export")

    def render(temporary_path):
        Path(temporary_path).write_bytes(b"new-good-export")
        return temporary_path

    result = render_wav_atomically(target, render)

    assert result == str(target)
    assert target.read_bytes() == b"new-good-export"
    assert list(tmp_path.glob(".voice.*.tmp.wav")) == []


def test_atomic_export_preserves_existing_target_on_render_failure(tmp_path):
    target = tmp_path / "voice.wav"
    target.write_bytes(b"previous-good-export")

    def fail(temporary_path):
        Path(temporary_path).write_bytes(b"partial-broken-export")
        raise RuntimeError("native render failed")

    with pytest.raises(RuntimeError, match="native render failed"):
        render_wav_atomically(target, fail)

    assert target.read_bytes() == b"previous-good-export"
    assert list(tmp_path.glob(".voice.*.tmp.wav")) == []


def test_atomic_export_rejects_missing_temp_output_and_preserves_target(tmp_path):
    target = tmp_path / "voice.wav"
    target.write_bytes(b"previous-good-export")

    with pytest.raises(RuntimeError, match="一時WAVが生成されません"):
        render_wav_atomically(target, lambda temporary_path: temporary_path)

    assert target.read_bytes() == b"previous-good-export"
    assert list(tmp_path.glob(".voice.*.tmp.wav")) == []
