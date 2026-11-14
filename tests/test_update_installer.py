from __future__ import annotations

import io
import stat
import tarfile
import zipfile

import pytest

from modules.updater import update_installer


def test_linux_portable_archive_extracts_only_expected_binary(tmp_path):
    archive_path = tmp_path / "linux.tar.gz"
    payload = b"VO-SE executable"
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo(update_installer.LINUX_BINARY_NAME)
        info.size = len(payload)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(payload))

    destination = tmp_path / "staged-binary"
    result = update_installer._extract_linux_portable(archive_path, destination)

    assert result == destination
    assert destination.read_bytes() == payload
    assert destination.stat().st_mode & 0o777 == 0o755


def test_linux_portable_archive_rejects_extra_or_traversal_members(tmp_path):
    archive_path = tmp_path / "unsafe.tar.gz"
    payload = b"binary"
    with tarfile.open(archive_path, "w:gz") as archive:
        info = tarfile.TarInfo("../outside")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    with pytest.raises(update_installer.UpdateInstallError, match="想定と異なります"):
        update_installer._extract_linux_portable(
            archive_path, tmp_path / "staged-binary"
        )


def test_macos_update_zip_requires_expected_app_bundle(tmp_path):
    archive_path = tmp_path / "mac-update.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            f"{update_installer.MAC_APP_NAME}/Contents/MacOS/VO-SE_vocal_Mac",
            b"app binary",
        )

    update_installer._validate_macos_update_zip(archive_path)


def test_macos_update_zip_rejects_path_traversal(tmp_path):
    archive_path = tmp_path / "unsafe-mac.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            f"{update_installer.MAC_APP_NAME}/../../outside",
            b"not allowed",
        )
        archive.writestr(
            f"{update_installer.MAC_APP_NAME}/Contents/MacOS/VO-SE_vocal_Mac",
            b"app binary",
        )

    with pytest.raises(update_installer.UpdateInstallError, match="不正なパス"):
        update_installer._validate_macos_update_zip(archive_path)


def test_macos_update_zip_rejects_symlink_outside_app_bundle(tmp_path):
    archive_path = tmp_path / "unsafe-link.zip"
    link = zipfile.ZipInfo(f"{update_installer.MAC_APP_NAME}/Contents/escape")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(link, "../../outside")
        archive.writestr(
            f"{update_installer.MAC_APP_NAME}/Contents/MacOS/VO-SE_vocal_Mac",
            b"app binary",
        )

    with pytest.raises(update_installer.UpdateInstallError, match="アプリ外"):
        update_installer._validate_macos_update_zip(archive_path)


def test_macos_update_zip_rejects_wrong_bundle_name(tmp_path):
    archive_path = tmp_path / "wrong-app.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "Other.app/Contents/MacOS/VO-SE_vocal_Mac",
            b"app binary",
        )

    with pytest.raises(update_installer.UpdateInstallError, match="構成が不正"):
        update_installer._validate_macos_update_zip(archive_path)
