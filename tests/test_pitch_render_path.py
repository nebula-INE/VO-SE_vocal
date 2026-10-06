from pathlib import Path
from types import SimpleNamespace
from typing import cast

import numpy as np

from modules.audio.vo_se_engine import VO_SE_Engine


def test_empty_pitch_curve_uses_note_base_frequency():
    note = SimpleNamespace(note_number=60, start_time=0.0, duration=0.5)

    engine = cast(VO_SE_Engine, SimpleNamespace(aural_ai=None))
    curve = VO_SE_Engine._get_sampled_curve(
        engine, [], note, 5, is_pitch=True
    )

    expected = 440.0 * (2.0 ** ((60.0 - 69.0) / 12.0))
    assert curve.dtype == np.float32
    assert np.allclose(curve, expected, rtol=1e-6, atol=1e-5)


def test_explicit_pitch_curve_is_still_interpreted_as_semitone_offset():
    note = SimpleNamespace(note_number=69, start_time=0.0, duration=0.5)
    events = [
        SimpleNamespace(time=0.0, value=0.0),
        SimpleNamespace(time=0.5, value=12.0),
    ]

    engine = cast(VO_SE_Engine, SimpleNamespace(aural_ai=None))
    curve = VO_SE_Engine._get_sampled_curve(
        engine, events, note, 3, is_pitch=True
    )

    assert np.allclose(
        curve,
        np.array([440.0, 440.0 * np.sqrt(2.0), 880.0], dtype=np.float32),
        rtol=1e-5,
        atol=1e-4,
    )


def test_world_render_path_does_not_smooth_explicit_pitch_curve():
    source = (
        Path(__file__).resolve().parents[1] / "src" / "vose_core.cpp"
    ).read_text(encoding="utf-8")

    assignment = source.index("tl_scratch.f0[j] = base_f0_val;")
    vibrato = source.index("// ビブラートカーブが NoteEvent", assignment)
    render_pitch_section = source[assignment:vibrato]

    assert "smooth_f0_gaussian(" not in render_pitch_section

def test_world_render_path_uses_conservative_voiced_aperiodicity_ceiling():
    source = (
        Path(__file__).resolve().parents[1] / "src" / "vose_core.cpp"
    ).read_text(encoding="utf-8")

    assert "{0.003, 0.015, 0.045, 0.08}" in source
    assert "{0.05, 0.35, 0.70}" in source
    assert "smooth_band_value(freq, bfreqs, bvals, 3)" in source


def test_world_render_path_keeps_wav_export_after_noise_control():
    source = (
        Path(__file__).resolve().parents[1] / "src" / "vose_core.cpp"
    ).read_text(encoding="utf-8")

    assert "wavwrite(" in source
    assert "full_song_buffer" in source
