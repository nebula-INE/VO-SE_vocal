from typing import Optional, TypeVar

from modules.data.oto_parser import (
    OtoEntry,
    OtoParser,
    VoicebankLoadCancelled,
)
import pytest


T = TypeVar("T")


def _require_entry(entry: Optional[T]) -> T:
    assert entry is not None
    return entry

@pytest.mark.smoke
class TestOtoParser:
    def test_parse_single_entry(self, tmp_path):
        ini_file = tmp_path / "oto.ini"
        ini_file.write_text("a.wav=a,50,100,0,120,30", encoding="cp932")

        parser = OtoParser()
        count = parser.load_oto_file(str(ini_file))

        assert count == 1
        entry = parser.get("a")
        assert entry is not None  # ← この行を追加
        assert entry.filename == "a.wav"
        assert entry.left_blank == 50.0
        assert entry.preutterance == 120.0
        assert entry.overlap == 30.0
        assert entry.voice_dir == str(tmp_path)

    def test_resolve_alias_vcv_priority(self):
        parser = OtoParser()
        parser._db["a い"] = OtoEntry(alias="a い", filename="a_i.wav", voice_dir="/dummy", left_blank=0, fixed_range=0, right_blank=0, preutterance=0, overlap=0)
        parser._db["- い"] = OtoEntry(alias="- い", filename="sil_i.wav", voice_dir="/dummy", left_blank=0, fixed_range=0, right_blank=0, preutterance=0, overlap=0)
        parser._db["い"] = OtoEntry(alias="い", filename="i.wav", voice_dir="/dummy", left_blank=0, fixed_range=0, right_blank=0, preutterance=0, overlap=0)

        entry = parser.resolve_alias("い", "a")
        assert entry is not None  # ← 追加
        assert entry.alias == "a い"

        entry = parser.resolve_alias("い", None)
        assert entry is not None  # ← 追加
        assert entry.alias == "- い"
        
    def test_encoding_fallback(self, tmp_path):
        """Shift-JISとUTF-8の自動判別"""
        ini_file = tmp_path / "oto.ini"
        # UTF-8で書かれた場合 (BOMなし)
        ini_file.write_text("あ.wav=あ,0,0,0,0,0", encoding="utf-8")
        parser = OtoParser()
        parser.load_oto_file(str(ini_file))
        assert parser.get("あ") is not None

        # Shift-JISで書かれた場合 (cp932)
        ini_file.write_text("あ.wav=あ,0,0,0,0,0", encoding="cp932")
        parser = OtoParser()
        parser.load_oto_file(str(ini_file))
        assert parser.get("あ") is not None

    def test_nested_and_windows_style_wav_path_resolution(self, tmp_path):
        voice_dir = tmp_path / "voice"
        nested = voice_dir / "SubVoice"
        nested.mkdir(parents=True)
        wav_file = nested / "A.wav"
        wav_file.write_bytes(b"RIFF")

        entry = OtoEntry(
            alias="あ",
            filename="subvoice\\A.wav",
            voice_dir=str(voice_dir),
            left_blank=0,
            fixed_range=0,
            right_blank=0,
            preutterance=0,
            overlap=0,
        )

        # Windows の oto.ini にある \\ 区切りと大文字小文字の違いを
        # macOS/Linux 上でも同じ実ファイルへ解決できることを確認する。
        assert entry.wav_path == str(wav_file)
def test_resolve_alias_does_not_use_unrelated_partial_match():
    parser = OtoParser()
    parser._db["しー"] = OtoEntry(
        alias="しー",
        filename="shi_long.wav",
        voice_dir="/dummy",
        left_blank=0,
        fixed_range=0,
        right_blank=0,
        preutterance=0,
        overlap=0,
    )

    assert parser.resolve_alias("し", None) is None


def test_resolve_alias_keeps_vcv_context_strict():
    parser = OtoParser()
    parser._db["a い"] = OtoEntry(
        alias="a い",
        filename="a_i.wav",
        voice_dir="/dummy",
        left_blank=0,
        fixed_range=0,
        right_blank=0,
        preutterance=0,
        overlap=0,
    )
    parser._db["i い"] = OtoEntry(
        alias="i い",
        filename="i_i.wav",
        voice_dir="/dummy",
        left_blank=0,
        fixed_range=0,
        right_blank=0,
        preutterance=0,
        overlap=0,
    )

    assert parser.resolve_alias("い", "u") is None


def _entry(alias: str) -> OtoEntry:
    return OtoEntry(
        alias=alias,
        filename=f"{alias.replace(' ', '_')}.wav",
        voice_dir="/dummy",
        left_blank=0,
        fixed_range=0,
        right_blank=0,
        preutterance=0,
        overlap=0,
    )


def test_resolve_alias_vcv_context_falls_back_to_cv_not_other_vcv():
    parser = OtoParser()
    parser._db["a い"] = _entry("a い")
    parser._db["- い"] = _entry("- い")

    # u い が無い場合でも、a いへ勝手に文脈を変更してはいけない。
    # 安全なfallbackは語頭/CVの - い。
    entry = parser.resolve_alias("い", "u")
    assert entry is not None
    assert entry.alias == "- い"


def test_resolve_alias_vcv_context_returns_none_when_only_other_vcv_exists():
    parser = OtoParser()
    parser._db["a い"] = _entry("a い")

    # 明示された u 文脈を a 文脈へ置き換えない。
    assert parser.resolve_alias("い", "u") is None


def test_resolve_alias_supports_initial_cv_alias_forms():
    parser = OtoParser()
    parser._db["_あ"] = _entry("_あ")

    entry = parser.resolve_alias("あ", None)
    assert entry is not None
    assert entry.alias == "_あ"


def test_resolve_alias_prefers_plain_cv_when_no_initial_marker_exists():
    parser = OtoParser()
    parser._db["あ"] = _entry("あ")

    entry = parser.resolve_alias("あ", None)
    assert entry is not None
    assert entry.alias == "あ"


def test_resolve_alias_never_resolves_rest_markers():
    parser = OtoParser()
    for alias in ("-", "ー", "~", "r", "休符"):
        parser._db[alias] = _entry(alias)

    for lyric in ("-", "ー", "~", "r", "休符"):
        assert parser.resolve_alias(lyric, None) is None


def test_resolve_alias_prefers_matching_vcv_context_for_common_transitions():
    parser = OtoParser()
    for alias in ("か", "き", "し", "た", "ん", "a か", "a き", "a し", "a た", "a ん"):
        parser._db[alias] = OtoEntry(alias=alias, filename=alias + ".wav", voice_dir="/dummy", left_blank=0, fixed_range=0, right_blank=0, preutterance=0, overlap=0)

    assert _require_entry(parser.resolve_alias("か", "a")).alias == "a か"
    assert _require_entry(parser.resolve_alias("き", "a")).alias == "a き"
    assert _require_entry(parser.resolve_alias("し", "a")).alias == "a し"
    assert _require_entry(parser.resolve_alias("た", "a")).alias == "a た"
    assert _require_entry(parser.resolve_alias("ん", "a")).alias == "a ん"

def test_resolve_alias_does_not_cross_substitute_vcv_contexts():
    parser = OtoParser()
    parser._db["い"] = OtoEntry(alias="い", filename="i.wav", voice_dir="/dummy", left_blank=0, fixed_range=0, right_blank=0, preutterance=0, overlap=0)
    parser._db["a い"] = OtoEntry(alias="a い", filename="a_i.wav", voice_dir="/dummy", left_blank=0, fixed_range=0, right_blank=0, preutterance=0, overlap=0)
    assert parser.resolve_alias("い", "u") is not None
    assert _require_entry(parser.resolve_alias("い", "u")).alias == "い"

def test_resolve_alias_rest_markers_never_become_audio():
    parser = OtoParser()
    for alias in ("r", "-", "休"):
        parser._db[alias] = OtoEntry(alias=alias, filename=alias + ".wav", voice_dir="/dummy", left_blank=0, fixed_range=0, right_blank=0, preutterance=0, overlap=0)
    for lyric in ("r", "r_", "r_0", "[r]", "-", "休", "休符", "ー", "~"):
        assert parser.resolve_alias(lyric) is None

def test_load_voice_dir_keeps_first_entry_for_duplicate_alias(tmp_path):
    voice_dir = tmp_path / "voice"
    sub_dir = voice_dir / "pitch"
    sub_dir.mkdir(parents=True)

    (voice_dir / "root.wav").write_bytes(b"RIFF")
    (sub_dir / "sub.wav").write_bytes(b"RIFF")

    (voice_dir / "oto.ini").write_text(
        "root.wav=あ,0,0,0,0,0\n",
        encoding="utf-8",
    )
    (sub_dir / "oto.ini").write_text(
        "sub.wav=あ,0,0,0,0,0\n",
        encoding="utf-8",
    )

    parser = OtoParser()
    count = parser.load_voice_dir(str(voice_dir), use_cache=False)

    assert count == 2
    entry = parser.get("あ")
    assert entry is not None
    assert entry.filename == "root.wav"
    assert entry.wav_path == str(voice_dir / "root.wav")



def test_load_voice_dir_keeps_first_duplicate_alias_and_exposes_scoped_aliases(tmp_path):
    voice_dir = tmp_path / "voice"
    low = voice_dir / "C4"
    high = voice_dir / "D4"
    low.mkdir(parents=True)
    high.mkdir(parents=True)

    (low / "a.wav").write_bytes(b"RIFF")
    (high / "a.wav").write_bytes(b"RIFF")
    (low / "oto.ini").write_text(
        "a.wav=あ,0,20,0,50,20\n",
        encoding="utf-8",
    )
    (high / "oto.ini").write_text(
        "a.wav=あ,0,20,0,60,20\n",
        encoding="utf-8",
    )

    parser = OtoParser()
    parser.load_voice_dir(str(voice_dir), use_cache=False)

    # The unqualified alias is deterministic: first discovered oto.ini wins.
    assert parser.get("あ") is not None
    assert _require_entry(parser.get("あ")).preutterance == 50.0

    # Multi-pitch folders remain explicitly selectable.
    c4 = parser.resolve_alias("C4/あ")
    d4 = parser.resolve_alias("D4\\あ")
    assert c4 is not None
    assert d4 is not None
    assert c4.preutterance == 50.0
    assert d4.preutterance == 60.0


def test_load_voice_dir_cache_preserves_scoped_duplicate_aliases(tmp_path):
    voice_dir = tmp_path / "voice"
    c4 = voice_dir / "C4"
    d4 = voice_dir / "D4"
    c4.mkdir(parents=True)
    d4.mkdir(parents=True)

    (c4 / "a.wav").write_bytes(b"RIFF")
    (d4 / "a.wav").write_bytes(b"RIFF")
    (c4 / "oto.ini").write_text("a.wav=あ,0,20,0,50,20\n", encoding="utf-8")
    (d4 / "oto.ini").write_text("a.wav=あ,0,20,0,60,20\n", encoding="utf-8")

    parser = OtoParser()
    parser.load_voice_dir(str(voice_dir), use_cache=True)

    cached = OtoParser()
    cached.load_voice_dir(str(voice_dir), use_cache=True)

    assert _require_entry(cached.resolve_alias("C4/あ")).preutterance == 50.0
    assert _require_entry(cached.resolve_alias("D4/あ")).preutterance == 60.0
    assert _require_entry(cached.get("あ")).preutterance == 50.0

def test_resolve_alias_supports_prefix_map_pitch_selection(tmp_path):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    (voice_dir / "prefix.map").write_text(
        "C1\t\t_C4\nF4\t\t_F4\n",
        encoding="utf-8",
    )
    (voice_dir / "oto.ini").write_text(
        "a_c4.wav=あ_C4,0,0,0,50,0\n"
        "a_f4.wav=あ_F4,0,0,0,60,0\n",
        encoding="utf-8",
    )
    (voice_dir / "a_c4.wav").write_bytes(b"RIFF")
    (voice_dir / "a_f4.wav").write_bytes(b"RIFF")

    parser = OtoParser()
    parser.load_voice_dir(str(voice_dir), use_cache=False)

    # Below the first declared boundary there is no valid mapped subbank.
    assert parser.resolve_alias("あ", None, note_num=0) is None
    low = parser.resolve_alias("あ", None, note_num=59)
    mid = parser.resolve_alias("あ", None, note_num=64)
    high = parser.resolve_alias("あ", None, note_num=65)
    assert low is not None and low.alias == "あ_C4"
    assert mid is not None and mid.alias == "あ_C4"
    assert high is not None and high.alias == "あ_F4"
    assert low.wav_path == str(voice_dir / "a_c4.wav")
    assert high.wav_path == str(voice_dir / "a_f4.wav")


def test_resolve_alias_prefix_map_preserves_explicit_suffix(tmp_path):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    (voice_dir / "prefix.map").write_text("C1\t\t_C4\nF4\t\t_F4\n", encoding="utf-8")
    (voice_dir / "oto.ini").write_text(
        "a_f4.wav=あ_F4,0,0,0,60,0\n",
        encoding="utf-8",
    )
    (voice_dir / "a_f4.wav").write_bytes(b"RIFF")

    parser = OtoParser()
    parser.load_voice_dir(str(voice_dir), use_cache=False)
    entry = parser.resolve_alias("あ_F4", None, note_num=60)
    assert entry is not None
    assert entry.alias == "あ_F4"


def test_resolve_alias_prefix_map_preserves_explicit_vcv_alias(tmp_path):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    (voice_dir / "prefix.map").write_text("C1\t\t_C4\nF4\t\t_F4\n", encoding="utf-8")
    (voice_dir / "oto.ini").write_text(
        "a_i.wav=a い,0,0,0,50,0\n"
        "a_i_f4.wav=a い_F4,0,0,0,60,0\n",
        encoding="utf-8",
    )
    (voice_dir / "a_i.wav").write_bytes(b"RIFF")
    (voice_dir / "a_i_f4.wav").write_bytes(b"RIFF")

    parser = OtoParser()
    parser.load_voice_dir(str(voice_dir), use_cache=False)

    entry = parser.resolve_alias("a い", None, note_num=65)
    assert entry is not None
    assert entry.alias == "a い"


def test_resolve_alias_prefix_map_applies_to_complete_vcv_alias(tmp_path):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    (voice_dir / "prefix.map").write_text("C1\tC4/\t\n", encoding="utf-8")
    (voice_dir / "oto.ini").write_text(
        "a_i.wav=C4/a い,0,0,0,50,0\n",
        encoding="utf-8",
    )
    (voice_dir / "a_i.wav").write_bytes(b"RIFF")

    parser = OtoParser()
    parser.load_voice_dir(str(voice_dir), use_cache=False)

    entry = parser.resolve_alias("い", "a", note_num=60)
    assert entry is not None
    assert entry.alias == "C4/a い"


def test_prefix_map_note_name_and_boundary_helpers():
    from modules.data.prefix_map import note_name_to_midi, parse_prefix_map, select_prefix_map

    assert note_name_to_midi("C4") == 60
    assert note_name_to_midi("F#4") == 66
    assert note_name_to_midi("Bb3") == 58
    assert note_name_to_midi("C-1") == 0

    entries = parse_prefix_map("C1\t\t_C4\nF4\t\t_F4\n")
    assert _require_entry(select_prefix_map(entries, 59)).suffix == "_C4"
    assert _require_entry(select_prefix_map(entries, 60)).suffix == "_C4"
    assert _require_entry(select_prefix_map(entries, 64)).suffix == "_C4"
    assert _require_entry(select_prefix_map(entries, 65)).suffix == "_F4"
    assert _require_entry(select_prefix_map(entries, 127)).suffix == "_F4"


def test_prefix_map_supports_explicit_prefix_and_suffix():
    from modules.data.prefix_map import map_alias, parse_prefix_map

    entries = parse_prefix_map("C4\tC4/\t_C4\n")
    assert map_alias("a い", 60, entries) == "C4/a い_C4"
    assert map_alias("C4/a い", 60, entries) == "C4/a い_C4"



def test_resolve_alias_uses_exact_default_alias_below_first_prefix_boundary(tmp_path):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    (voice_dir / "prefix.map").write_text("C5\t\t_C5\n", encoding="utf-8")
    (voice_dir / "oto.ini").write_text(
        "ji.wav=じ,0,0,0,50,0\n"
        "sa.wav=さ,0,0,0,50,0\n"
        "i_sa.wav=i さ,0,0,0,50,0\n"
        "a_i.wav=a い,0,0,0,50,0\n",
        encoding="utf-8",
    )
    for name in ("ji.wav", "sa.wav", "i_sa.wav", "a_i.wav"):
        (voice_dir / name).write_bytes(b"RIFF")

    parser = OtoParser()
    parser.load_voice_dir(str(voice_dir), use_cache=False)

    # F4/A#4 are below the first C5 pitch-map range, so the unmarked/default
    # layer must be used rather than rejecting all exact aliases.
    assert _require_entry(parser.resolve_alias("じ", None, note_num=65)).alias == "じ"
    assert _require_entry(parser.resolve_alias("さ", "i", note_num=65)).alias == "i さ"
    assert _require_entry(parser.resolve_alias("a い", None, note_num=70)).alias == "a い"


def test_resolve_alias_does_not_borrow_mapped_pitch_layer_below_first_boundary(tmp_path):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    (voice_dir / "prefix.map").write_text("C5\t\t_C5\n", encoding="utf-8")
    (voice_dir / "oto.ini").write_text(
        "a_c5.wav=あ_C5,0,0,0,50,0\n",
        encoding="utf-8",
    )
    (voice_dir / "a_c5.wav").write_bytes(b"RIFF")

    parser = OtoParser()
    parser.load_voice_dir(str(voice_dir), use_cache=False)

    assert parser.resolve_alias("あ", None, note_num=65) is None
    selected = parser.resolve_alias("あ", None, note_num=72)
    assert selected is not None
    assert selected.alias == "あ_C5"



def test_resolve_alias_selects_closest_pitch_suffix_without_prefix_map(tmp_path):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    (voice_dir / "oto.ini").write_text(
        "a_c4.wav=あ_C4,0,0,0,50,0\n"
        "a_f4.wav=あ_F4,0,0,0,60,0\n",
        encoding="utf-8",
    )
    for name in ("a_c4.wav", "a_f4.wav"):
        (voice_dir / name).write_bytes(b"RIFF")

    parser = OtoParser()
    parser.load_voice_dir(str(voice_dir), use_cache=False)

    selected = parser.resolve_alias("あ", None, note_num=65)
    assert selected is not None
    assert selected.alias == "あ_F4"


def test_resolve_alias_does_not_guess_vcv_context_without_previous_vowel():
    parser = OtoParser()
    parser._db["a い"] = _entry("a い")
    parser._db["u い"] = _entry("u い")

    # A first note or a note after a rest has no previous-vowel context.
    assert parser.resolve_alias("い", None, note_num=60) is None


def test_load_voice_dir_finds_prefix_map_case_insensitively(tmp_path):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    (voice_dir / "PREFIX.MAP").write_text(
        "C1\t_C4\nF4\t_F4\n",
        encoding="utf-8",
    )
    (voice_dir / "oto.ini").write_text(
        "a_c4.wav=あ_C4,0,0,0,50,0\n"
        "a_f4.wav=あ_F4,0,0,0,60,0\n",
        encoding="utf-8",
    )
    for name in ("a_c4.wav", "a_f4.wav"):
        (voice_dir / name).write_bytes(b"RIFF")

    parser = OtoParser()
    parser.load_voice_dir(str(voice_dir), use_cache=False)

    assert len(parser._prefix_map) == 2
    selected = parser.resolve_alias("あ", None, note_num=65)
    assert selected is not None
    assert selected.alias == "あ_F4"



def test_resolve_alias_bridges_hiragana_and_romaji_spellings():
    parser = OtoParser()
    parser._db["ji"] = _entry("ji")
    parser._db["sa"] = _entry("sa")
    parser._db["no"] = _entry("no")
    parser._db["a ji"] = _entry("a ji")

    ji = parser.resolve_alias("じ")
    sa = parser.resolve_alias("さ")
    no = parser.resolve_alias("の")
    contextual = parser.resolve_alias("じ", "a")

    assert ji is not None and ji.alias == "ji"
    assert sa is not None and sa.alias == "sa"
    assert no is not None and no.alias == "no"
    assert contextual is not None and contextual.alias == "a ji"


def test_read_safe_prefers_utf8_when_bytes_are_also_valid_cp932(tmp_path):
    path = tmp_path / "utf8-also-decodes-as-cp932.oto"
    expected = "ち.wav=ち,0,0,0,50,0\n"
    path.write_bytes(expected.encode("utf-8"))

    assert OtoParser._read_safe(str(path)) == expected



def test_load_oto_file_streams_without_materializing_full_text(tmp_path, monkeypatch):
    ini_file = tmp_path / "oto.ini"
    ini_file.write_text(
        "a.wav=あ,0,0,0,20,5\nb.wav=い,0,0,0,20,5\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        OtoParser,
        "_read_safe",
        staticmethod(lambda _path: pytest.fail("whole-file reader must not be used")),
    )

    parser = OtoParser()
    assert parser.load_oto_file(str(ini_file)) == 2
    assert parser.get("あ") is not None
    assert parser.get("い") is not None


def test_load_oto_file_accepts_large_records_without_fixed_size_ceilings(tmp_path, monkeypatch):
    from modules.data import oto_parser as parser_module

    ini_file = tmp_path / "large-record.oto"
    long_alias = "あ" * 70_000
    ini_file.write_text(
        f"large.wav={long_alias},0,0,0,0,0\n",
        encoding="utf-8",
    )
    # Compatibility names from older releases must not impose a hidden default cap.
    monkeypatch.setattr(parser_module, "MAX_OTO_FILE_BYTES", 4, raising=False)
    monkeypatch.setattr(parser_module, "MAX_OTO_LINE_CHARS", 4, raising=False)

    parser = OtoParser()
    assert parser.load_oto_file(str(ini_file)) == 1
    assert parser.get(long_alias) is not None


def test_load_voice_dir_has_no_default_oto_entry_ceiling(tmp_path, monkeypatch):
    from modules.data import oto_parser as parser_module

    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    (voice_dir / "character.txt").write_text("name=Unlimited\\n", encoding="utf-8")
    (voice_dir / "oto.ini").write_text(
        "a.wav=あ,0,0,0,0,0\nb.wav=い,0,0,0,0,0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(parser_module, "MAX_VOICEBANK_OTO_ENTRIES", 1, raising=False)

    parser = OtoParser()
    count = parser.load_voice_dir(
        str(voice_dir),
        use_cache=False,
        reset=True,
        write_cache=False,
    )
    assert count == 2
    assert parser.get("あ") is not None
    assert parser.get("い") is not None


def test_find_voice_root_searches_deep_parent_chain_by_default(tmp_path):
    bank_root = tmp_path / "nested-bank"
    selected = bank_root / "a" / "b" / "c" / "d" / "selected"
    selected.mkdir(parents=True)
    (bank_root / "character.txt").write_text("name=DeepBank", encoding="utf-8")

    assert OtoParser.find_voice_root(str(selected)) == str(bank_root)


def test_load_oto_file_honors_cancellation(tmp_path):
    import threading

    ini_file = tmp_path / "oto.ini"
    ini_file.write_text("a.wav=あ,0,0,0,0,0\n", encoding="utf-8")
    cancel_event = threading.Event()
    cancel_event.set()

    with pytest.raises(VoicebankLoadCancelled):
        OtoParser().load_oto_file(str(ini_file), cancel_event=cancel_event)



def test_layer_coverage_uses_streaming_reader(tmp_path, monkeypatch):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    ini_file = voice_dir / "oto.ini"
    ini_file.write_text(
        "a.wav=あ,0,0,0,0,0\nk.wav=か,0,0,0,0,0\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        OtoParser,
        "_read_safe",
        staticmethod(lambda _path: pytest.fail("coverage must stream oto.ini")),
    )

    covered, fraction = OtoParser._layer_aliases_and_coverage(str(ini_file))
    assert covered == {"あ", "か"}
    assert fraction > 0
