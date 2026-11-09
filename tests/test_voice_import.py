"""UTAU音源の取り込み（フォルダ / ZIP）のテスト。Qt には依存しない。"""
import os
import zipfile

import pytest

from modules.audio.voice_manager import VoiceManager


def _make_voice(root, name, nested=False):
    d = os.path.join(root, name, name) if nested else os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "oto.ini"), "w", encoding="cp932") as f:
        f.write("a.wav=あ,0,100,0,50,20\n")
    with open(os.path.join(d, "a.wav"), "wb") as f:
        f.write(b"RIFF")
    return os.path.join(root, name)


@pytest.fixture
def vm(tmp_path):
    m = VoiceManager()
    m.internal_voice_dir = str(tmp_path / "voice_banks")   # リポジトリを汚さない
    return m


@pytest.mark.unit
def test_install_from_folder_copies_and_registers(vm, tmp_path):
    src = _make_voice(str(tmp_path / "src"), "VoiceA")
    name = vm.install_voice_from_folder(src)
    assert name == "VoiceA"
    assert os.path.exists(os.path.join(vm.internal_voice_dir, "VoiceA", "oto.ini"))
    assert vm.voices["VoiceA"] == os.path.join(vm.internal_voice_dir, "VoiceA")
    assert os.path.exists(os.path.join(src, "oto.ini")), "元のフォルダは消さない"


@pytest.mark.unit
def test_install_from_parent_folder_finds_nested_oto(vm, tmp_path):
    parent = _make_voice(str(tmp_path / "src"), "VoiceB", nested=True)
    name = vm.install_voice_from_folder(parent)
    assert name == "VoiceB"
    # 二重フォルダ（VoiceB/VoiceB）にならず、中身だけがコピーされる
    assert os.path.exists(os.path.join(vm.internal_voice_dir, "VoiceB", "oto.ini"))
    assert not os.path.exists(os.path.join(vm.internal_voice_dir, "VoiceB", "VoiceB"))


@pytest.mark.unit
def test_install_same_name_gets_numbered_suffix(vm, tmp_path):
    src = _make_voice(str(tmp_path / "src"), "VoiceA")
    assert vm.install_voice_from_folder(src) == "VoiceA"
    assert vm.install_voice_from_folder(src) == "VoiceA_1"
    assert vm.install_voice_from_folder(src) == "VoiceA_2"


@pytest.mark.unit
def test_folder_without_oto_ini_is_rejected_and_leaves_nothing(vm, tmp_path):
    empty = tmp_path / "empty" / "nothing"
    empty.mkdir(parents=True)
    with pytest.raises(ValueError, match="oto.ini"):
        vm.install_voice_from_folder(str(empty.parent))
    assert not os.path.exists(vm.internal_voice_dir) or os.listdir(vm.internal_voice_dir) == []


@pytest.mark.unit
def test_missing_folder_raises(vm, tmp_path):
    with pytest.raises(FileNotFoundError):
        vm.install_voice_from_folder(str(tmp_path / "does_not_exist"))


@pytest.mark.unit
def test_folder_already_in_voice_dir_is_not_copied_again(vm, tmp_path):
    src = _make_voice(str(tmp_path / "src"), "VoiceA")
    vm.install_voice_from_folder(src)
    before = sorted(os.listdir(vm.internal_voice_dir))
    again = vm.install_voice_from_folder(os.path.join(vm.internal_voice_dir, "VoiceA"))
    assert again == "VoiceA"
    assert sorted(os.listdir(vm.internal_voice_dir)) == before


@pytest.mark.unit
def test_failed_copy_does_not_leave_partial_folder(vm, tmp_path, monkeypatch):
    import shutil
    src = _make_voice(str(tmp_path / "src"), "VoiceA")

    def boom(s, d, *a, **k):
        os.makedirs(d)                      # 途中まで作ってから失敗する状況
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copytree", boom)
    with pytest.raises(OSError):
        vm.install_voice_from_folder(src)
    assert not os.path.exists(os.path.join(vm.internal_voice_dir, "VoiceA"))


@pytest.mark.unit
def test_install_from_zip_still_works(vm, tmp_path):
    archive = tmp_path / "voice.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Pack/oto.ini", "a.wav=a,0,0,0,0,0")
        zf.writestr("Pack/a.wav", "x")
    assert vm.install_voice_from_zip(str(archive)) == "Pack"
    assert os.path.exists(os.path.join(vm.internal_voice_dir, "Pack", "oto.ini"))


@pytest.mark.unit
def test_zip_path_traversal_is_rejected(vm, tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../evil.txt", "x")
    with pytest.raises(ValueError):
        vm.install_voice_from_zip(str(archive))
    assert not (tmp_path / "evil.txt").exists()


@pytest.mark.unit
@pytest.mark.parametrize(
    "member_name",
    [
        r"..\\..\\evil.txt",
        r"C:\\outside\\evil.txt",
        r"C:evil.txt",
        r"\\rooted\\evil.txt",
    ],
)
def test_zip_rejects_windows_absolute_and_traversal_paths(vm, tmp_path, member_name):
    archive = tmp_path / "windows_evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member_name, "x")

    with pytest.raises(ValueError):
        vm.install_voice_from_zip(str(archive))

    assert not (tmp_path / "evil.txt").exists()


@pytest.mark.unit
def test_zip_backslash_member_paths_are_normalized(vm, tmp_path):
    archive = tmp_path / "windows_separators.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(r"Pack\\oto.ini", "a.wav=a,0,0,0,0,0")
        zf.writestr(r"Pack\\a.wav", "x")

    assert vm.install_voice_from_zip(str(archive)) == "Pack"
    assert os.path.exists(os.path.join(vm.internal_voice_dir, "Pack", "oto.ini"))


@pytest.mark.unit
def test_find_oto_dirs_orders_shallow_first_and_limits_depth(tmp_path):
    _make_voice(str(tmp_path), "top")
    deep = tmp_path / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    (deep / "oto.ini").write_text("x")
    found = VoiceManager.find_oto_dirs(str(tmp_path))
    assert found[0] == str(tmp_path / "top")
    assert str(deep) not in found, "max_depth を超えた階層は探さない"


@pytest.mark.unit
def test_scanned_voices_include_imported_one(vm, tmp_path):
    src = _make_voice(str(tmp_path / "src"), "VoiceA")
    vm.install_voice_from_folder(src)
    voices = vm.scan_voices()
    assert "VoiceA" in voices
    assert voices["VO-SE Official (Embedded)"] == "__INTERNAL__"
