from __future__ import annotations

import base64
import json
from pathlib import Path

from modules.data.oto_parser import OtoParser


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads(
    (ROOT / "specs" / "voicebank_resolution_parity.json").read_text(encoding="utf-8")
)


def _oto_lines(entries: list[dict[str, str]]) -> list[str]:
    return [
        f"{entry['filename']}={entry['alias']},0,0,0,20,5"
        for entry in entries
    ]


def _materialize_voicebanks(tmp_path: Path) -> tuple[Path, Path, Path]:
    voices = tmp_path / "voices"
    main_name = FIXTURE["voicebanks"]["main"]
    extra_name = main_name + "_Extra"
    unrelated_name = FIXTURE["voicebanks"]["unrelated"]
    main = voices / main_name
    extra = voices / extra_name
    unrelated = voices / unrelated_name

    for directory in (main, extra, unrelated):
        directory.mkdir(parents=True)
        (directory / "character.txt").write_text("name=parity fixture\n", encoding="utf-8")

    main_lines = [
        f"cv_{index}.wav={alias},0,0,0,20,5"
        for index, alias in enumerate(FIXTURE["main_cv_aliases"])
    ]
    main_lines.extend(_oto_lines(FIXTURE["main_entries"]))
    (main / "oto.ini").write_text("\n".join(main_lines) + "\n", encoding="utf-8")
    (main / "prefix.map").write_text(FIXTURE["prefix_map"]["main"], encoding="utf-8")

    (extra / "oto.ini").write_text(
        "\n".join(_oto_lines(FIXTURE["extra_entries"])) + "\n",
        encoding="utf-8",
    )
    (extra / "prefix.map").write_text(FIXTURE["prefix_map"]["extra"], encoding="utf-8")
    (unrelated / "oto.ini").write_text(
        "\n".join(_oto_lines(FIXTURE["unrelated_entries"])) + "\n",
        encoding="utf-8",
    )

    encoding_dir = main / "encoding"
    encoding_dir.mkdir()
    cp932_oto = base64.b64decode(FIXTURE["encoding_case"]["oto_base64_cp932"])
    (encoding_dir / "oto.ini").write_bytes(cp932_oto)

    for relative_path in FIXTURE["wav_payloads"]:
        bank_key, *parts = relative_path.split("/")
        bank_root = {
            "main": main,
            "extra": extra,
            "unrelated": unrelated,
        }[bank_key]
        wav_path = bank_root.joinpath(*parts)
        wav_path.parent.mkdir(parents=True, exist_ok=True)
        wav_path.write_bytes(b"RIFF parity fixture")

    return main, extra, unrelated


def test_shared_voicebank_resolution_contract_when_extra_is_selected(tmp_path: Path) -> None:
    main, extra, unrelated = _materialize_voicebanks(tmp_path)

    parser = OtoParser()
    parser.load_voice_dir(
        str(extra),
        use_cache=False,
        reset=True,
        write_cache=False,
    )

    expected_layers = [
        str(main),
        str(extra),
    ]
    assert list(parser.loaded_layers[:2]) == expected_layers
    assert all(str(unrelated) not in layer for layer in parser.loaded_layers)

    # Main-layer duplicate aliases win even though the selected bank is Extra.
    shared = parser.resolve_alias(FIXTURE["expected"]["main_layer_alias_wins"])
    assert shared is not None
    assert shared.voice_dir == str(main)
    assert shared.filename == "audio/main-shared.wav"

    # An alias that exists only in Extra can use a unique WAV basename in main.
    extra_only = parser.resolve_alias(FIXTURE["expected"]["extra_only_alias"])
    assert extra_only is not None
    assert extra_only.voice_dir == str(extra)
    assert extra_only.wav_path == str(main / "audio" / "extra-ji.wav")

    # The main bank's prefix.map decides which pitch alias the note resolves to.
    prefix_entry = parser.resolve_alias("あ", note_num=65)
    assert prefix_entry is not None
    assert prefix_entry.alias == FIXTURE["expected"]["prefix_map_alias"]
    assert prefix_entry.voice_dir == str(main)

    # The CP932 oto.ini nested inside the main bank remains discoverable.
    cp932_entry = parser.resolve_alias(FIXTURE["expected"]["encoding_alias"])
    assert cp932_entry is not None
    assert cp932_entry.filename == FIXTURE["encoding_case"]["filename"]
    assert cp932_entry.wav_path == str(main / FIXTURE["encoding_case"]["wav_path_under_main"])

    # A sibling that is not the exact name-linked base/Extra pair is excluded.
    assert parser.resolve_alias(FIXTURE["expected"]["unrelated_alias"]) is None

    # Duplicate basename candidates across the permitted pair are ambiguous.
    ambiguous = parser.resolve_alias(FIXTURE["expected"]["ambiguous_alias"])
    assert ambiguous is not None
    assert not Path(ambiguous.wav_path).is_file()


def test_shared_voicebank_resolution_contract_when_main_is_selected(tmp_path: Path) -> None:
    main, extra, unrelated = _materialize_voicebanks(tmp_path)

    parser = OtoParser()
    parser.load_voice_dir(
        str(main),
        use_cache=False,
        reset=True,
        write_cache=False,
    )

    assert parser.loaded_layers[0] == str(main)
    assert str(extra) in parser.loaded_layers
    assert parser.loaded_layers.index(str(extra)) > parser.loaded_layers.index(str(main))
    assert all(str(unrelated) not in layer for layer in parser.loaded_layers)

    extra_only = parser.resolve_alias(FIXTURE["expected"]["extra_only_alias"])
    assert extra_only is not None
    assert extra_only.voice_dir == str(extra)
    assert extra_only.wav_path == str(main / "audio" / "extra-ji.wav")


def test_shared_resolution_fixture_declares_safe_cross_platform_rules() -> None:
    contract = FIXTURE["contract"]
    assert contract["companion_matching"] == "exact_normalized_base_name_only"
    assert contract["unrelated_sibling_voicebanks"] == "never_include"
    assert contract["duplicate_alias"] == "first_wins_after_layer_ordering"
    assert contract["ambiguous_basename"] == "do_not_guess"
    assert contract["prefix_map"] == "first_non_supplement_root_then_selected_root"
    assert contract["multi_pitch"] == (
        "closest_pitch_within_the_highest_priority_matching_layer"
    )
