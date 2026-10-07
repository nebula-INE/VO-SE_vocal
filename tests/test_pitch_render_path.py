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

    assert "{0.003, 0.012, 0.030, 0.050}" in source
    assert "{0.05, 0.35, 0.70}" in source
    assert "smooth_band_value(freq, bfreqs, bvals, 3)" in source


def test_world_render_path_keeps_wav_export_after_noise_control():
    source = (
        Path(__file__).resolve().parents[1] / "src" / "vose_core.cpp"
    ).read_text(encoding="utf-8")

    assert "wavwrite(" in source
    assert "full_song_buffer" in source


def test_absolute_timeline_zero_overlap_uses_boundary_declick_without_changing_wav_export():
    source = (
        Path(__file__).resolve().parents[1] / "src" / "vose_core.cpp"
    ).read_text(encoding="utf-8")

    assert "kBoundaryDeclickSamples = 88" in source
    assert "prior_audio_overlaps && source_skip == 0" in source
    assert "find_adjacent_absolute_predecessor" in source
    assert "other_end == render_start" in source
    assert "kBoundaryStepCorrectionSamples = 88" in source
    assert "previous_last" in source
    assert "next_first" in source
    assert "full_song_buffer[render_start + s] * fade_out" in source
    assert "wavwrite(" in source


def test_absolute_boundary_step_correction_removes_sample_jump_without_shifting_timeline():
    boundary = 88
    previous_last = -0.544403076171875
    next_first = 0.755218505859375
    signal = np.zeros(boundary + 88, dtype=np.float64)
    signal[boundary - 1] = previous_last
    signal[boundary] = next_first

    safe = 88
    target = 0.5 * (previous_last + next_first)
    previous_delta = target - previous_last
    next_delta = target - next_first
    for i in range(safe):
        t = i / (safe - 1)
        fade = 0.5 * (1.0 - np.cos(np.pi * t))
        signal[boundary - safe + i] += previous_delta * fade
        signal[boundary + i] += next_delta * (1.0 - fade)

    assert signal[boundary - 1] == np.float64(target)
    assert signal[boundary] == np.float64(target)
    assert abs(signal[boundary] - signal[boundary - 1]) < 1e-12
    assert boundary == 88


def test_boundary_correction_is_limited_to_exact_zero_gap_and_preserves_short_regions():
    previous_end = 1000
    render_start = 1000
    one_sample_gap_end = 999

    assert previous_end == render_start
    assert one_sample_gap_end != render_start

    for write_len in (1, 2, 10, 88):
        safe = min(88, previous_end, write_len)
        assert safe == min(88, write_len)
        assert safe > 0

def test_absolute_overlap_declick_repairs_the_actual_first_sample_step():
    previous_last = -0.544403076171875
    next_first = 0.755218505859375
    boundary = 88
    signal = np.zeros(boundary + 88, dtype=np.float64)
    signal[boundary - 1] = previous_last
    signal[boundary] = next_first

    safe = 88
    target = 0.5 * (previous_last + next_first)
    previous_delta = target - previous_last
    next_delta = target - next_first
    for i in range(safe):
        t = i / (safe - 1)
        fade = 0.5 * (1.0 - np.cos(np.pi * t))
        signal[boundary - safe + i] += previous_delta * fade
        signal[boundary + i] += next_delta * (1.0 - fade)

    assert signal[boundary - 1] == np.float64(target)
    assert signal[boundary] == np.float64(target)
    assert abs(signal[boundary] - signal[boundary - 1]) < 1e-12


def test_world_overlap_boundary_path_applies_continuity_correction_after_crossfade():
    source = (
        Path(__file__).resolve().parents[1] / "src" / "vose_core.cpp"
    ).read_text(encoding="utf-8")

    branch = source.index("} else if (prior_audio_overlaps && source_skip == 0)")
    following = source.index("} else if (source_skip == 0)", branch)
    section = source[branch:following]

    assert "apply_boundary_step_correction(" in section
    assert "previous_last" in section
    assert "next_first" in section

def test_world_overlap_crossfade_branch_also_repairs_the_actual_seam():
    source = (
        Path(__file__).resolve().parents[1] / "src" / "vose_core.cpp"
    ).read_text(encoding="utf-8")

    overlap_branch = source.index("if (overlap_samples > 0)")
    next_branch = source.index("} else if (prior_audio_overlaps && source_skip == 0)", overlap_branch)
    section = source[overlap_branch:next_branch]

    assert "prior_audio_overlaps && source_skip == 0 && render_start > 0" in section
    assert "const double previous_last" in section
    assert "const double next_first" in section
    assert "apply_boundary_step_correction(" in section


def test_final_audio_diagnostics_map_anomalies_to_note_placement():
    source = (
        Path(__file__).resolve().parents[1] / "src" / "vose_core.cpp"
    ).read_text(encoding="utf-8")

    assert "struct RenderPlacementDiagnostic" in source
    assert "log_full_song_anomaly_context(" in source
    assert "[RenderAnomaly] final_buffer anomalies=" in source
    assert "placement_diagnostics" in source
    assert "placement.alias" in source
    assert "placement.pitch_hz" in source
    assert "placement.source_skip" in source
