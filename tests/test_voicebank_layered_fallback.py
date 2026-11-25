from __future__ import annotations

import logging

from modules.data.oto_parser import OtoParser, _KANA_TO_ROMAJI


HIRAGANA_CV = (
    "あいうえお"
    "かきくけこさしすせそたちつてとなにぬねの"
    "はひふへほまみむめもやゆよらりるれろわをん"
    "がぎぐげござじずぜぞだぢづでどばびぶべぼ"
    "ぱぴぷぺぽぁぃぅぇぉゃゅょゔっ"
)


def _write_layer(directory, aliases, *, overrides=None):
    directory.mkdir(parents=True, exist_ok=True)
    overrides = overrides or {}
    lines = []
    for index, alias in enumerate(aliases):
        filename = overrides.get(alias, f"sample_{index}.wav")
        wav_path = directory / filename
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        wav_path.write_bytes(b"RIFF")
        lines.append(f"{filename}={alias},0,0,0,50,0\n")
    (directory / "oto.ini").write_text("".join(lines), encoding="utf-8")


def test_selected_layer_wins_and_sibling_layer_is_used_as_wav_relative_fallback(tmp_path):
    extracted_root = tmp_path / "package"
    extracted_root.mkdir()
    (extracted_root / "character.txt").write_text("name=Example", encoding="utf-8")
    bank_root = extracted_root / "Bank"

    # The selected directory sorts after its sibling alphabetically. Priority
    # must still follow explicit selection, not the OS/filesystem traversal order.
    selected = bank_root / "Z_Selected"
    sibling = bank_root / "A_Sibling"
    selected_aliases = [alias for alias in HIRAGANA_CV if alias != "い"]
    _write_layer(selected, selected_aliases, overrides={"あ": "selected-a.wav"})
    _write_layer(
        sibling,
        HIRAGANA_CV,
        overrides={"あ": "sibling-a.wav", "い": "samples/i.wav"},
    )

    parser = OtoParser()
    count = parser.load_voice_dir(
        str(selected),
        use_cache=False,
        write_cache=False,
    )

    assert count >= len(selected_aliases) + len(HIRAGANA_CV)
    assert parser.loaded_layers[0] == str(selected)
    assert parser.find_voice_root(str(selected)) == str(extracted_root)
    selected_entry = parser.get("あ")
    assert selected_entry is not None
    assert selected_entry.wav_path == str(selected / "selected-a.wav")

    fallback = parser.resolve_alias("い")
    assert fallback is not None
    assert fallback.voice_dir == str(sibling)
    assert fallback.wav_path == str(sibling / "samples" / "i.wav")


def test_extra_or_low_coverage_layer_is_fallback_only_and_notifies(tmp_path):
    root = tmp_path / "release"
    root.mkdir()
    (root / "readme.txt").write_text("voicebank", encoding="utf-8")
    selected_extra = root / "selected_Append"
    main = root / "Main_CV"

    _write_layer(selected_extra, ["あ"])
    _write_layer(main, HIRAGANA_CV)

    parser = OtoParser()
    parser.load_voice_dir(
        str(selected_extra),
        use_cache=False,
        write_cache=False,
    )

    assert parser.loaded_layers[0] == str(main)
    entry = parser.resolve_alias("あ")
    assert entry is not None
    assert entry.voice_dir == str(main)
    assert parser.layer_notifications
    assert "併用しました" in parser.layer_notifications[0]


def test_missing_root_markers_fall_back_to_selected_direct_child_of_voices(tmp_path):
    voices_dir = tmp_path / "voices"
    selected = voices_dir / "BankA" / "CV"
    unrelated = voices_dir / "BankB"
    _write_layer(selected, ["あ"])
    _write_layer(unrelated, ["い"])

    parser = OtoParser()
    parser.load_voice_dir(
        str(selected),
        use_cache=False,
        write_cache=False,
    )

    assert parser.find_voice_root(str(selected)) == str(voices_dir / "BankA")
    assert parser.get("あ") is not None
    assert parser.get("い") is None
    assert all(str(voices_dir / "BankB") not in layer for layer in parser.loaded_layers)


def test_nfkc_alias_lookup_preserves_original_oto_folder_for_wav(tmp_path):
    root = tmp_path / "bank"
    root.mkdir()
    (root / "character.txt").write_text("name=Example", encoding="utf-8")
    _write_layer(root / "selected", ["ａ"])

    parser = OtoParser()
    parser.load_voice_dir(
        str(root / "selected"),
        use_cache=False,
        write_cache=False,
    )

    entry = parser.resolve_alias("a")
    assert entry is not None
    assert entry.alias == "ａ"
    assert entry.wav_path == str(root / "selected" / "sample_0.wav")


def test_unresolved_alias_warning_lists_tried_keys_and_all_layers(tmp_path, caplog):
    root = tmp_path / "bank"
    root.mkdir()
    (root / "character.txt").write_text("name=Example", encoding="utf-8")
    selected = root / "selected"
    sibling = root / "sibling"
    _write_layer(selected, ["あ"])
    _write_layer(sibling, ["い"])

    parser = OtoParser()
    parser.load_voice_dir(
        str(selected),
        use_cache=False,
        write_cache=False,
    )

    with caplog.at_level(logging.WARNING, logger="modules.data.oto_parser"):
        assert parser.resolve_alias("存在しない") is None

    assert "tried_keys" in caplog.text
    assert "存在しない" in caplog.text
    assert str(selected) in caplog.text
    assert str(sibling) in caplog.text


def test_successful_alias_debug_log_identifies_the_source_layer(tmp_path, caplog):
    root = tmp_path / "bank"
    root.mkdir()
    (root / "character.txt").write_text("name=Example", encoding="utf-8")
    selected = root / "selected"
    sibling = root / "sibling"
    _write_layer(selected, ["あ"])
    _write_layer(sibling, ["い"])

    parser = OtoParser()
    parser.load_voice_dir(
        str(selected),
        use_cache=False,
        write_cache=False,
    )

    with caplog.at_level(logging.DEBUG, logger="modules.data.oto_parser"):
        entry = parser.resolve_alias("あ")

    assert entry is not None
    assert "音源エイリアス解決" in caplog.text
    assert str(selected) in caplog.text
    assert str(entry.wav_path) in caplog.text


def test_extra_folder_uses_only_exact_name_matched_main_bank_sibling(tmp_path):
    voices = tmp_path / "voices"
    main = voices / "重音テト"
    extra = voices / "重音テトエクストラ"
    unrelated = voices / "別音源"

    main_aliases = [
        romaji for kana, romaji in _KANA_TO_ROMAJI.items() if kana != "じ"
    ]
    _write_layer(main, main_aliases)
    _write_layer(extra, ["じ"])
    _write_layer(unrelated, ["さ"])

    parser = OtoParser()
    parser.load_voice_dir(
        str(extra),
        use_cache=False,
        write_cache=False,
    )

    assert parser.loaded_layers[0] == str(main)
    assert parser.loaded_layers[1] == str(extra)
    assert all(str(unrelated) not in layer for layer in parser.loaded_layers)

    main_entry = parser.resolve_alias("さ")
    extra_entry = parser.resolve_alias("じ")
    assert main_entry is not None
    assert extra_entry is not None
    assert main_entry.voice_dir == str(main)
    assert extra_entry.voice_dir == str(extra)
    assert parser.layer_notifications
    assert "併用しました" in parser.layer_notifications[0]


def test_oto_wav_basename_fallback_is_unique_and_stays_inside_bank_root(tmp_path):
    root = tmp_path / "bank"
    selected = root / "oto"
    audio = root / "audio"
    selected.mkdir(parents=True)
    audio.mkdir(parents=True)
    (root / "character.txt").write_text("name=Example", encoding="utf-8")
    (selected / "oto.ini").write_text(
        "/old/voice/sample.wav=あ,0,0,0,50,0\n",
        encoding="utf-8",
    )
    actual_wav = audio / "sample.wav"
    actual_wav.write_bytes(b"RIFF")

    parser = OtoParser()
    parser.load_voice_dir(
        str(selected),
        use_cache=False,
        write_cache=False,
    )

    entry = parser.get("あ")
    assert entry is not None
    assert entry.wav_path == str(actual_wav)


def test_selected_folder_prefix_map_is_used_when_extracted_root_has_none(tmp_path):
    root = tmp_path / "package"
    root.mkdir()
    (root / "character.txt").write_text("name=Example", encoding="utf-8")
    selected = root / "Bank" / "Selected"
    _write_layer(selected, ["あ"])
    (selected / "prefix.map").write_text(
        "C1\t\t_C4\nF4\t\t_F4\n",
        encoding="utf-8",
    )

    parser = OtoParser()
    parser.load_voice_dir(
        str(selected),
        use_cache=False,
        write_cache=False,
    )

    assert len(parser._prefix_map) == 2
