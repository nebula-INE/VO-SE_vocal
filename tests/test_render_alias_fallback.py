from types import SimpleNamespace

import numpy as np
import pytest

from modules.audio.vo_se_engine_patch import (
    _lookup_oto_map_fallback,
    _phoneme_sequence_aliases,
    _render_to_wav_v2,
)
from modules.data.data_models import NoteEvent


def test_oto_map_fallback_accepts_string_phonemes():
    assert _lookup_oto_map_fallback(
        {"a": "/voice/a.wav"},
        "not-an-alias",
        "a",
    ) == "/voice/a.wav"


def test_oto_map_fallback_accepts_single_item_phoneme_list():
    assert _lookup_oto_map_fallback(
        {"a": "/voice/a.wav"},
        "not-an-alias",
        ["a"],
    ) == "/voice/a.wav"


def test_oto_map_fallback_does_not_hash_or_guess_multi_phoneme_lists():
    # A multi-phone sequence is not a single sample alias. In particular,
    # do not silently choose "a.wav" just because "a" is its first phone.
    assert _lookup_oto_map_fallback(
        {"a": "/voice/a.wav", "i": "/voice/i.wav"},
        "not-an-alias",
        ["a", "i"],
    ) == ""


def test_oto_map_fallback_prefers_the_exact_lyric_alias():
    assert _lookup_oto_map_fallback(
        {"あ": "/voice/あ.wav", "a": "/voice/a.wav"},
        "あ",
        ["a"],
    ) == "/voice/あ.wav"


def test_oto_map_fallback_ignores_non_string_phoneme_items():
    assert _lookup_oto_map_fallback(
        {"a": "/voice/a.wav"},
        "not-an-alias",
        [None, ["a"]],
    ) == ""


def test_render_with_unresolved_multi_phoneme_note_reports_voice_error_not_type_error(
    tmp_path,
):
    sample_path = tmp_path / "a.wav"
    sample_path.write_bytes(b"placeholder")

    note = NoteEvent(
        note_number=60,
        start_time=0.0,
        duration=0.5,
        lyric="見つからない歌詞",
        phonemes=["a", "i"],
    )

    class FakeAnalyzer:
        @staticmethod
        def align_vocal_timing(notes, _oto_parser, *, tempo_bpm):
            assert tempo_bpm == 120.0
            return notes, []

    def sampled_curve(_events, _note, resolution, is_pitch=False):
        return np.zeros(resolution, dtype=np.float32)

    engine = SimpleNamespace(
        lib=object(),
        text_analyzer=FakeAnalyzer(),
        oto_parser=None,
        cvvc_resolver=None,
        vcv_resolver=None,
        oto_map={"a": str(sample_path), "i": str(sample_path)},
        _temp_refs=[],
        _get_sampled_curve=sampled_curve,
    )
    parameters = {
        "Pitch": [],
        "Gender": [],
        "Tension": [],
        "Breath": [],
    }

    with pytest.raises(RuntimeError, match="音源WAVを1件も解決") as error:
        _render_to_wav_v2(
            engine,
            [note],
            parameters,
            str(tmp_path / "render.wav"),
            tempo_bpm=120.0,
        )

    assert "見つからない歌詞" in str(error.value)
    assert "phonemes=['a', 'i']" in str(error.value)
    assert "unhashable type" not in str(error.value)



def test_render_keeps_resolved_notes_and_skips_unresolved_multi_phoneme_fallback(
    tmp_path, monkeypatch
):
    from pathlib import Path

    from modules.audio import vo_se_engine_patch

    resolved_sample = tmp_path / "あ.wav"
    resolved_sample.write_bytes(b"sample")
    output_path = tmp_path / "partial-render.wav"
    captured_paths = []

    notes = [
        NoteEvent(
            note_number=60,
            start_time=0.0,
            duration=0.5,
            lyric="あ",
            phonemes=["a"],
        ),
        NoteEvent(
            note_number=62,
            start_time=0.5,
            duration=0.5,
            lyric="存在しない歌詞",
            phonemes=["a", "i"],
        ),
    ]

    class FakeAnalyzer:
        @staticmethod
        def align_vocal_timing(note_events, _oto_parser, *, tempo_bpm):
            assert tempo_bpm == 120.0
            return note_events, []

    class FakeNativeLibrary:
        @staticmethod
        def execute_render(c_notes, note_count, encoded_path, _mode_flag):
            for index in range(int(note_count)):
                captured_paths.append(c_notes[index].wav_path)
            Path(encoded_path.decode("utf-8")).write_bytes(b"fake-rendered-wav")

    def sampled_curve(_events, _note, resolution, is_pitch=False):
        return np.zeros(resolution, dtype=np.float32)

    monkeypatch.setattr(
        vo_se_engine_patch,
        "_validate_rendered_wav",
        lambda *_args: None,
    )
    engine = SimpleNamespace(
        lib=FakeNativeLibrary(),
        text_analyzer=FakeAnalyzer(),
        oto_parser=None,
        cvvc_resolver=None,
        vcv_resolver=None,
        oto_map={"あ": str(resolved_sample), "a": str(resolved_sample)},
        _temp_refs=[],
        _get_sampled_curve=sampled_curve,
    )
    parameters = {
        "Pitch": [],
        "Gender": [],
        "Tension": [],
        "Breath": [],
    }

    result = _render_to_wav_v2(
        engine,
        notes,
        parameters,
        str(output_path),
        tempo_bpm=120.0,
    )

    assert result == str(output_path.resolve())
    assert output_path.is_file()
    assert captured_paths == [str(resolved_sample).encode("utf-8"), None]



def test_phoneme_sequence_aliases_reject_special_moraic_nasal_before_lowercasing():
    assert _phoneme_sequence_aliases(["N", "a"]) == []
    assert _phoneme_sequence_aliases(["n", "a"]) == ["na", "n a"]
    assert _phoneme_sequence_aliases(["cl", "a"]) == []


def test_oto_map_fallback_keeps_exact_lyric_ahead_of_phone_alias():
    assert _lookup_oto_map_fallback(
        {"じ": "/voice/exact-lyric.wav", "ji": "/voice/phone-alias.wav"},
        "じ",
        ["j", "i"],
    ) == "/voice/exact-lyric.wav"
    assert _lookup_oto_map_fallback(
        {"ji": "/voice/phone-alias.wav"},
        None,
        ["j", "i"],
    ) == "/voice/phone-alias.wav"


def test_render_resolves_reported_five_note_sequence_from_cv_aliases(tmp_path, monkeypatch):
    from pathlib import Path

    from modules.audio.vcv_resolver import VcvResolver
    from modules.data.oto_parser import OtoParser
    from modules.audio import vo_se_engine_patch

    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    samples = {
        "ji_C4.wav": "ji_C4",
        "i_sa_C4.wav": "i sa_C4",
        "a_i_C4.wav": "a い_C4",
        "i_no_C4.wav": "i no_C4",
        "o_ka_C4.wav": "o か_C4",
    }
    for filename in samples:
        (voice_dir / filename).write_bytes(b"RIFF")
    # Real tab-separated UTAU format; this must parse as one prefix.map row.
    (voice_dir / "prefix.map").write_text(
        "C1\t\t_C4\n",
        encoding="utf-8",
    )
    (voice_dir / "oto.ini").write_text(
        "".join(
            f"{filename}={alias},0,0,0,50,0\n"
            for filename, alias in samples.items()
        ),
        # Standard UTAU voicebank metadata is commonly Shift-JIS/CP932.
        # OtoParser intentionally checks CP932 before UTF-8 for compatibility.
        encoding="cp932",
    )

    parser = OtoParser()
    parser._load_prefix_map(str(voice_dir))
    assert len(parser._prefix_map) == 1
    assert parser.load_oto_file(str(voice_dir / "oto.ini"), voice_root=str(voice_dir)) == 5
    assert parser.get("a い_C4") is not None

    notes = [
        NoteEvent(note_number=70, start_time=0.0, duration=0.5, lyric="じ", phonemes=["j", "i"]),
        NoteEvent(note_number=65, start_time=0.5, duration=0.5, lyric="さ", phonemes=["s", "a"]),
        NoteEvent(note_number=65, start_time=1.0, duration=0.5, lyric="a い", phonemes=["i"]),
        NoteEvent(note_number=65, start_time=1.5, duration=0.5, lyric="の", phonemes=["n", "o"]),
        NoteEvent(note_number=65, start_time=2.0, duration=0.5, lyric="か", phonemes=["k", "a"]),
    ]

    class FakeAnalyzer:
        @staticmethod
        def align_vocal_timing(note_events, _oto_parser, *, tempo_bpm):
            assert tempo_bpm == 120.0
            return note_events, []

    captured_paths = []

    class FakeNativeLibrary:
        @staticmethod
        def execute_render(c_notes, note_count, encoded_path, _mode_flag):
            captured_paths.extend(
                c_notes[index].wav_path.decode("utf-8") if c_notes[index].wav_path else ""
                for index in range(int(note_count))
            )
            Path(encoded_path.decode("utf-8")).write_bytes(b"fake-rendered-wav")

    def sampled_curve(_events, _note, resolution, is_pitch=False):
        return np.zeros(resolution, dtype=np.float32)

    monkeypatch.setattr(
        vo_se_engine_patch,
        "_validate_rendered_wav",
        lambda *_args: None,
    )
    engine = SimpleNamespace(
        lib=FakeNativeLibrary(),
        text_analyzer=FakeAnalyzer(),
        oto_parser=parser,
        cvvc_resolver=None,
        vcv_resolver=VcvResolver(parser, use_g2p=False),
        oto_map={},
        voice_lib_path=str(voice_dir),
        _temp_refs=[],
        _get_sampled_curve=sampled_curve,
    )
    parameters = {"Pitch": [], "Gender": [], "Tension": [], "Breath": []}

    result = _render_to_wav_v2(
        engine,
        notes,
        parameters,
        str(tmp_path / "five-note-render.wav"),
        tempo_bpm=120.0,
    )

    assert result == str((tmp_path / "five-note-render.wav").resolve())
    assert captured_paths == [
        str(voice_dir / "ji_C4.wav"),
        str(voice_dir / "i_sa_C4.wav"),
        str(voice_dir / "a_i_C4.wav"),
        str(voice_dir / "i_no_C4.wav"),
        str(voice_dir / "o_ka_C4.wav"),
    ]
