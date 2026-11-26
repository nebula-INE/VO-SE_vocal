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


@pytest.mark.smoke
@pytest.mark.unit
@pytest.mark.parametrize(
    "member_name",
    [
        r"..\..\evil.txt",
        r"C:\outside\evil.txt",
        r"C:evil.txt",
        r"\rooted\evil.txt",
    ],
)
def test_zip_rejects_windows_absolute_and_traversal_paths(vm, tmp_path, member_name):
    archive = tmp_path / "windows_evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(member_name, "x")

    with pytest.raises(ValueError):
        vm.install_voice_from_zip(str(archive))

    assert not (tmp_path / "evil.txt").exists()


@pytest.mark.smoke
@pytest.mark.unit
def test_zip_backslash_member_paths_are_normalized(vm, tmp_path):
    archive = tmp_path / "windows_separators.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(r"Pack\oto.ini", "a.wav=a,0,0,0,0,0")
        zf.writestr(r"Pack\a.wav", "x")

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



@pytest.mark.unit
def test_delete_imported_voice_removes_only_managed_copy(vm, tmp_path):
    src = _make_voice(str(tmp_path / "source"), "VoiceToDelete")
    name = vm.install_voice_from_folder(src)
    installed_path = vm.voices[name]

    assert vm.is_managed_voice(installed_path)
    assert vm.delete_user_voice(installed_path) == "VoiceToDelete"

    assert not os.path.exists(installed_path)
    assert os.path.exists(os.path.join(src, "oto.ini")), "インポート元は削除しない"
    assert "VoiceToDelete" not in vm.voices


@pytest.mark.unit
def test_delete_rejects_external_voice_folder(vm, tmp_path):
    external = _make_voice(str(tmp_path / "external"), "ExternalVoice")
    with pytest.raises(ValueError, match="VO-SEの音源保存先"):
        vm.delete_user_voice(external)
    assert os.path.exists(os.path.join(external, "oto.ini"))


@pytest.mark.unit
def test_delete_rejects_managed_store_root(vm, tmp_path):
    os.makedirs(vm.user_voice_dir, exist_ok=True)
    with pytest.raises(ValueError, match="VO-SEの音源保存先"):
        vm.delete_user_voice(vm.user_voice_dir)
    assert os.path.isdir(vm.user_voice_dir)


@pytest.mark.unit
def test_delete_rejects_nested_or_symlinked_voice_targets(vm, tmp_path):
    outside = _make_voice(str(tmp_path / "outside"), "OutsideVoice")
    os.makedirs(vm.user_voice_dir, exist_ok=True)
    outer = os.path.join(vm.user_voice_dir, "outer")
    nested = os.path.join(outer, "nested")
    os.makedirs(nested, exist_ok=True)
    with open(os.path.join(nested, "oto.ini"), "w", encoding="utf-8") as handle:
        handle.write("a.wav=a,0,0,0,0,0\n")

    assert not vm.is_managed_voice(nested)
    with pytest.raises(ValueError):
        vm.delete_user_voice(nested)
    assert os.path.isdir(nested)

    link = os.path.join(vm.user_voice_dir, "linked")
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("この環境ではディレクトリシンボリックリンクを作成できません")
    assert not vm.is_managed_voice(link)
    with pytest.raises(ValueError):
        vm.delete_user_voice(link)
    assert os.path.exists(os.path.join(outside, "oto.ini"))



@pytest.mark.unit
def test_legacy_in_place_voice_under_managed_root_can_be_deleted(vm):
    """Old local banks without the new ownership registry remain manageable."""
    os.makedirs(vm.user_voice_dir, exist_ok=True)
    src = _make_voice(vm.user_voice_dir, "ExistingVoice")

    assert vm.install_voice_from_folder(vm.user_voice_dir) == "ExistingVoice"
    installed_path = vm.voices["ExistingVoice"]

    assert installed_path == src
    assert not vm.is_managed_voice(installed_path)
    assert vm.is_local_voice(installed_path)
    assert vm.delete_user_voice(installed_path) == "ExistingVoice"
    assert not os.path.exists(installed_path)


@pytest.mark.unit
def test_copy_registers_only_app_managed_voicebank(vm, tmp_path):
    src = _make_voice(str(tmp_path / "source"), "MarkedVoice")
    name = vm.install_voice_from_folder(src)
    installed_path = vm.voices[name]

    import json

    registry_path = os.path.join(vm.user_voice_dir, vm.MANAGED_VOICE_REGISTRY)
    with open(registry_path, "r", encoding="utf-8") as registry_file:
        registry = json.load(registry_file)

    assert os.path.basename(installed_path) in registry["managed_voicebanks"]
    assert not os.path.exists(os.path.join(installed_path, ".vose-managed-voicebank"))
    assert vm.is_managed_voice(installed_path)


@pytest.mark.unit
def test_managed_voice_registry_tracks_repaired_top_level_name(vm, tmp_path, monkeypatch):
    from modules.utils import text_encoding

    src = _make_voice(str(tmp_path / "source"), "BrokenVoice")

    def rename_managed_bank(root, cancel_event=None, max_entries=200_000):
        if os.path.normcase(os.path.abspath(root)) != os.path.normcase(os.path.abspath(vm.user_voice_dir)):
            return []
        old_path = os.path.join(root, "BrokenVoice")
        new_path = os.path.join(root, "FixedVoice")
        if not os.path.isdir(old_path):
            return []
        os.rename(old_path, new_path)
        return [(old_path, new_path)]

    monkeypatch.setattr(text_encoding, "repair_mojibake_tree", rename_managed_bank)

    installed_name = vm.install_voice_from_folder(src)
    assert installed_name == "FixedVoice"
    installed_path = vm.voices[installed_name]

    import json

    registry_path = os.path.join(vm.user_voice_dir, vm.MANAGED_VOICE_REGISTRY)
    with open(registry_path, "r", encoding="utf-8") as registry_file:
        registry = json.load(registry_file)

    assert registry["managed_voicebanks"] == ["FixedVoice"]
    assert vm.is_managed_voice(installed_path)
    assert vm.delete_user_voice(installed_path) == "FixedVoice"
    assert os.path.exists(os.path.join(src, "oto.ini"))



@pytest.mark.unit
def test_scan_voices_supports_cancellation(vm, tmp_path):
    import threading

    _make_voice(str(tmp_path / "source"), "VoiceC")
    vm.internal_voice_dir = str(tmp_path / "source")
    cancel_event = threading.Event()
    cancel_event.set()

    voices = vm.scan_voices(cancel_event=cancel_event)
    assert voices == {"VO-SE Official (Embedded)": "__INTERNAL__"}


@pytest.mark.unit
def test_scan_voices_reports_progress_without_glob_materialization(vm, tmp_path, monkeypatch):
    import glob

    root = tmp_path / "source"
    root.mkdir(parents=True, exist_ok=True)
    for index in range(130):
        bank = root / f"bank-{index:03d}"
        bank.mkdir()
        if index == 129:
            (bank / "oto.ini").write_text(
                "a.wav=あ,0,0,0,0,0\n",
                encoding="utf-8",
            )
    vm.internal_voice_dir = str(root)
    monkeypatch.setattr(glob, "glob", lambda *_args, **_kwargs: pytest.fail("glob must not be used"))
    progress = []

    voices = vm.scan_voices(progress_callback=progress.append)
    assert "bank-129" in voices
    assert progress



@pytest.mark.unit
def test_zip_install_rejects_excessive_uncompressed_size(vm, tmp_path, monkeypatch):
    from modules.audio import voice_manager as voice_manager_module

    archive = tmp_path / "oversized.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("LargeVoice/oto.ini", "a.wav=あ,0,0,0,0,0\n")
        zf.writestr("LargeVoice/a.wav", b"x" * 128)

    monkeypatch.setattr(
        voice_manager_module,
        "MAX_VOICE_ZIP_UNCOMPRESSED_BYTES",
        64,
    )
    with pytest.raises(ValueError, match="展開後の合計サイズ"):
        vm.install_voice_from_zip(str(archive))
    assert not os.path.exists(vm.user_voice_dir)
