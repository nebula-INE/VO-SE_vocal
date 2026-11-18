from modules.data.oto_editor import save_oto_entry
from modules.data.oto_parser import OtoEntry


def test_save_oto_entry_updates_only_selected_line_and_preserves_cp932(tmp_path):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    ini = voice_dir / "oto.ini"
    ini.write_bytes(
        "あ.wav=あ,1,2,-3,4,5\r\nい.wav=い,6,7,8,9,10\r\n".encode("cp932")
    )
    entry = OtoEntry(
        alias="い", filename="い.wav", voice_dir=str(voice_dir),
        left_blank=6, fixed_range=7, right_blank=8, preutterance=9, overlap=10,
    )
    assert save_oto_entry(
        entry, left_blank=11, fixed_range=12, right_blank=-13,
        preutterance=14, overlap=15,
    )
    decoded = ini.read_bytes().decode("cp932")
    assert "あ.wav=あ,1,2,-3,4,5" in decoded
    assert "い.wav=い,11,12,-13,14,15" in decoded


def test_save_oto_entry_does_not_rewrite_unknown_alias(tmp_path):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    (voice_dir / "oto.ini").write_text(
        "a.wav=a,0,0,0,0,0\n", encoding="utf-8"
    )
    entry = OtoEntry(
        alias="missing", filename="missing.wav", voice_dir=str(voice_dir),
        left_blank=0, fixed_range=0, right_blank=0, preutterance=0, overlap=0,
    )
    try:
        save_oto_entry(
            entry, left_blank=1, fixed_range=2, right_blank=3,
            preutterance=4, overlap=5,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("unknown alias should not modify oto.ini")


def test_save_oto_entry_does_not_add_bom_to_plain_utf8(tmp_path):
    voice_dir = tmp_path / "utf8"
    voice_dir.mkdir()
    ini = voice_dir / "oto.ini"
    ini.write_text("a.wav=あ,0,0,0,0,0" + chr(10), encoding="utf-8")
    entry = OtoEntry(
        alias="あ", filename="a.wav", voice_dir=str(voice_dir),
        left_blank=0, fixed_range=0, right_blank=0, preutterance=0, overlap=0,
    )
    save_oto_entry(
        entry, left_blank=1, fixed_range=2, right_blank=3,
        preutterance=4, overlap=5,
    )
    assert not ini.read_bytes().startswith(bytes((0xEF, 0xBB, 0xBF)))
