from types import SimpleNamespace

import numpy as np
import pytest

from modules.audio.vo_se_engine_patch import (
    _lookup_oto_map_fallback,
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
