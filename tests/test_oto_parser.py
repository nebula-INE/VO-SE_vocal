from modules.data.oto_parser import OtoParser, OtoEntry
import pytest

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


def test_resolve_alias_does_not_substitute_unrelated_phoneme():
    parser = OtoParser()
    parser._db["- か"] = OtoEntry(
        alias="- か",
        filename="ka.wav",
        voice_dir="/dummy",
        left_blank=0,
        fixed_range=0,
        right_blank=0,
        preutterance=0,
        overlap=0,
    )

    assert parser.resolve_alias("し", None) is None
