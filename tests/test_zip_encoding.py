"""日本語名の UTAU 音源ZIPで、フォルダ名・ファイル名が文字化けしないことのテスト。

Windows のエクスプローラー等で作られた ZIP は、ファイル名を Shift-JIS(CP932) で
保存し、UTF-8 フラグを立てない。Python の zipfile はその場合 CP437 として読むため、
「あ.wav」が「éü.wav」のような別の名前になり、oto.ini の参照と一致しなくなる。
"""
import os
import zipfile

import pytest

from modules.audio.voice_manager import VoiceManager
from modules.utils.text_encoding import (
    decode_zip_member_names,
    fix_cp437_mojibake,
    repair_mojibake_tree,
    safe_extract_zip,
)
from modules.utils.zip_handler import ZipHandler


def make_legacy_zip(path, files, encoding="cp932"):
    """ファイル名を `encoding` のバイト列のまま、UTF-8 フラグ無しで書いた ZIP を作る。

    zipfile は非 ASCII 名を自動で UTF-8 フラグ付きにするため、同じバイト長の
    ASCII 仮名で書いたあと、ZIP 内の名前のバイト列を差し替える。
    """
    patches = {}
    with zipfile.ZipFile(path, "w") as zf:
        for i, (name, data) in enumerate(files.items()):
            raw = name.encode(encoding)
            placeholder = f"#{i}#".ljust(len(raw), "_")[: len(raw)]
            assert len(placeholder) == len(raw) and placeholder.isascii()
            patches[placeholder.encode()] = raw
            zf.writestr(placeholder, data)
    blob = open(path, "rb").read()
    for placeholder, raw in patches.items():
        blob = blob.replace(placeholder, raw)      # ローカルヘッダと中央ディレクトリの両方
    open(path, "wb").write(blob)


VOICE_FILES = {
    "テスト音源/oto.ini": "あ.wav=あ,0,100,0,50,20\n".encode("cp932"),
    "テスト音源/あ.wav": b"RIFF",
    "テスト音源/い.wav": b"RIFF",
}


@pytest.mark.unit
def test_legacy_zip_reproduces_the_garbled_names(tmp_path):
    archive = tmp_path / "legacy.zip"
    make_legacy_zip(archive, VOICE_FILES)
    with zipfile.ZipFile(archive) as zf:
        raw_names = [i.filename for i in zf.infolist()]
        assert not any(i.flag_bits & 0x800 for i in zf.infolist())
        assert "テスト音源/oto.ini" not in raw_names            # そのままでは化ける
        assert decode_zip_member_names(zf.infolist()) == list(VOICE_FILES)


@pytest.mark.unit
def test_voice_manager_installs_cp932_zip_with_correct_names(tmp_path):
    archive = tmp_path / "legacy.zip"
    make_legacy_zip(archive, VOICE_FILES)
    vm = VoiceManager()
    vm.internal_voice_dir = str(tmp_path / "voice_banks")

    name = vm.install_voice_from_zip(str(archive))

    assert name == "テスト音源"
    base = os.path.join(vm.internal_voice_dir, "テスト音源")
    assert sorted(os.listdir(base)) == ["oto.ini", "あ.wav", "い.wav"]
    assert "テスト音源" in vm.scan_voices()


@pytest.mark.unit
def test_utf8_flagged_zip_is_untouched(tmp_path):
    archive = tmp_path / "utf8.zip"
    with zipfile.ZipFile(archive, "w") as zf:                 # 非ASCII名は自動でUTF-8フラグ付き
        for n, d in VOICE_FILES.items():
            zf.writestr(n, d)
    with zipfile.ZipFile(archive) as zf:
        assert all(i.flag_bits & 0x800 for i in zf.infolist())
        assert decode_zip_member_names(zf.infolist()) == list(VOICE_FILES)


@pytest.mark.unit
def test_utf8_bytes_without_flag_are_also_handled(tmp_path):
    archive = tmp_path / "utf8_noflag.zip"                    # macOS/Linux の一部ツールがこう書く
    make_legacy_zip(archive, VOICE_FILES, encoding="utf-8")
    with zipfile.ZipFile(archive) as zf:
        assert decode_zip_member_names(zf.infolist()) == list(VOICE_FILES)


@pytest.mark.unit
def test_ascii_names_are_never_modified(tmp_path):
    archive = tmp_path / "ascii.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Voice_01/oto.ini", "x")
        zf.writestr("Voice_01/ka.wav", "x")
    with zipfile.ZipFile(archive) as zf:
        assert decode_zip_member_names(zf.infolist()) == ["Voice_01/oto.ini", "Voice_01/ka.wav"]


@pytest.mark.unit
def test_zip_handler_also_decodes_names(tmp_path):
    archive = tmp_path / "legacy.zip"
    make_legacy_zip(archive, VOICE_FILES)
    ok, bank = ZipHandler.extract_voice_bank(str(archive), str(tmp_path / "voices"))
    assert ok is True
    assert (tmp_path / "voices" / "legacy" / "テスト音源" / "あ.wav").exists()


@pytest.mark.unit
def test_backslash_separators_are_normalised(tmp_path):
    archive = tmp_path / "bs.zip"
    make_legacy_zip(archive, {"音源\\oto.ini": b"x", "音源\\あ.wav": b"x"})
    with zipfile.ZipFile(archive) as zf:
        safe_extract_zip(zf, str(tmp_path / "out"))
    assert (tmp_path / "out" / "音源" / "oto.ini").exists()


@pytest.mark.unit
def test_safe_extract_rejects_total_uncompressed_size_before_writing(tmp_path):
    archive = tmp_path / "oversized_total.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Voice/oto.ini", "x" * 4)
        zf.writestr("Voice/a.wav", b"x" * 4)

    output = tmp_path / "out"
    with zipfile.ZipFile(archive) as zf, pytest.raises(ValueError, match="合計サイズ"):
        safe_extract_zip(zf, str(output), max_total_size=7)

    assert not (output / "Voice" / "oto.ini").exists()
    assert not (output / "Voice" / "a.wav").exists()


def test_safe_extract_rejects_single_oversized_member(tmp_path):
    archive = tmp_path / "oversized_member.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Voice/a.wav", b"x" * 8)

    output = tmp_path / "out"
    with zipfile.ZipFile(archive) as zf, pytest.raises(ValueError, match="ファイルサイズ"):
        safe_extract_zip(zf, str(output), max_member_size=7)

    assert not (output / "Voice" / "a.wav").exists()


def test_safe_extract_rejects_too_many_members(tmp_path):
    archive = tmp_path / "too_many_members.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("a.txt", "a")
        zf.writestr("b.txt", "b")

    with zipfile.ZipFile(archive) as zf, pytest.raises(ValueError, match="ファイル数"):
        safe_extract_zip(zf, str(tmp_path / "out"), max_members=1)


def test_path_traversal_still_rejected_with_legacy_names(tmp_path):
    archive = tmp_path / "evil.zip"
    make_legacy_zip(archive, {"../../悪.txt": b"x"})
    with zipfile.ZipFile(archive) as zf, pytest.raises(ValueError):
        safe_extract_zip(zf, str(tmp_path / "out"))
    assert not (tmp_path / "悪.txt").exists()


@pytest.mark.unit
def test_fix_cp437_mojibake_only_touches_real_mojibake():
    garbled = "テスト音源".encode("cp932").decode("cp437")
    assert garbled != "テスト音源"
    assert fix_cp437_mojibake(garbled) == "テスト音源"
    assert fix_cp437_mojibake(garbled, strict=True) == "テスト音源"
    for fine in ("テスト音源", "Voice_01", "Café", "", "音"):
        assert fix_cp437_mojibake(fine) == fine
        assert fix_cp437_mojibake(fine, strict=True) == fine


@pytest.mark.unit
def test_strict_mode_does_not_misread_latin_names():
    # CP932 として読むと偶然「嗜議ode」のように読めてしまう欧文の名前
    assert fix_cp437_mojibake("Ünïcode", strict=True) == "Ünïcode"
    assert fix_cp437_mojibake("Ünïcode") != "Ünïcode", "非 strict は読み直す（ZIP全体の確認と併用する前提）"


@pytest.mark.unit
def test_zip_with_latin_accent_names_is_not_misread(tmp_path):
    """欧文の正しい名前のZIP(CP437)は、日本語として読み直さない。"""
    archive = tmp_path / "latin.zip"
    make_legacy_zip(archive, {"Voice/Ünï.txt": b"x", "Voice/é.wav": b"x"}, encoding="cp437")
    with zipfile.ZipFile(archive) as zf:
        assert decode_zip_member_names(zf.infolist()) == ["Voice/Ünï.txt", "Voice/é.wav"]


@pytest.mark.unit
def test_repair_fixes_whole_bank_including_ascii_mixed_names(tmp_path):
    """VCV 音源の「a か.wav」のように ASCII が混ざる名前も、同じ音源内なら一緒に直す。"""
    root = tmp_path / "voice_banks"
    bank = root / "テスト音源".encode("cp932").decode("cp437")
    bank.mkdir(parents=True)
    (bank / "oto.ini").write_text("x")
    (bank / "あ.wav".encode("cp932").decode("cp437")).write_bytes(b"RIFF")
    (bank / "a か.wav".encode("cp932").decode("cp437")).write_bytes(b"RIFF")
    (bank / "テスト_v2.wav".encode("cp932").decode("cp437")).write_bytes(b"RIFF")

    repair_mojibake_tree(str(root))

    assert sorted(os.listdir(root / "テスト音源")) == sorted(["oto.ini", "あ.wav", "a か.wav", "テスト_v2.wav"])


@pytest.mark.unit
def test_repair_leaves_latin_named_bank_alone(tmp_path):
    root = tmp_path / "voice_banks"
    bank = root / "Ünïcode"
    bank.mkdir(parents=True)
    (bank / "oto.ini").write_text("x")
    (bank / "é.wav").write_bytes(b"RIFF")
    assert repair_mojibake_tree(str(root)) == []
    assert (root / "Ünïcode" / "é.wav").exists()


@pytest.mark.unit
def test_repair_only_touches_the_garbled_bank_next_to_a_latin_one(tmp_path):
    root = tmp_path / "voice_banks"
    (root / "Ünïcode").mkdir(parents=True)
    bad = root / "テスト音源".encode("cp932").decode("cp437")
    bad.mkdir()
    repair_mojibake_tree(str(root))
    assert sorted(os.listdir(root)) == sorted(["Ünïcode", "テスト音源"])


@pytest.mark.unit
def test_repair_existing_garbled_folders(tmp_path):
    """すでに化けた名前で取り込まれてしまった音源を、起動時の再スキャンで直す。"""
    root = tmp_path / "voice_banks"
    bad_dir = root / "テスト音源".encode("cp932").decode("cp437")
    bad_dir.mkdir(parents=True)
    (bad_dir / "oto.ini").write_text("x")
    (bad_dir / "あ.wav".encode("cp932").decode("cp437")).write_bytes(b"RIFF")

    renamed = repair_mojibake_tree(str(root))

    assert len(renamed) == 2
    assert sorted(os.listdir(root / "テスト音源")) == ["oto.ini", "あ.wav"]


@pytest.mark.unit
def test_repair_does_not_overwrite_existing_names(tmp_path):
    root = tmp_path / "voice_banks"
    (root / "テスト音源").mkdir(parents=True)
    bad = root / "テスト音源".encode("cp932").decode("cp437")
    bad.mkdir()
    repair_mojibake_tree(str(root))
    assert bad.exists(), "同名があるときは上書きせずそのまま残す"


@pytest.mark.unit
def test_scan_voices_repairs_garbled_folder_names(tmp_path):
    vm = VoiceManager()
    vm.internal_voice_dir = str(tmp_path / "voice_banks")
    bad = os.path.join(vm.internal_voice_dir, "テスト音源".encode("cp932").decode("cp437"))
    os.makedirs(bad)
    with open(os.path.join(bad, "oto.ini"), "w") as f:
        f.write("x")
    voices = vm.scan_voices()
    assert "テスト音源" in voices
