"""Apply a verified update with a small OS-native helper after the app exits."""
from __future__ import annotations

import os
import posixpath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from modules.updater.update_manager import get_update_cache_dir, is_deb_installation


MAC_APP_NAME = "VO-SE_vocal_Mac.app"
MAC_BINARY_RELATIVE = Path("Contents") / "MacOS" / "VO-SE_vocal_Mac"
LINUX_BINARY_NAME = "VO-SE_vocal_Linux"
WINDOWS_ASSET_NAME = "VO-SE_vocal_Win.exe"
MAX_ARCHIVE_MEMBERS = 100_000
MAX_MACOS_UNPACKED_BYTES = 3 * 1024 * 1024 * 1024
MAX_LINUX_BINARY_BYTES = 1024 * 1024 * 1024


class UpdateInstallError(RuntimeError):
    """Raised when an update cannot safely be staged or installed."""


def _require_frozen_application() -> None:
    if not getattr(sys, "frozen", False):
        raise UpdateInstallError(
            "ソースコードから起動した開発版では自動置き換えできません。"
        )


def _probe_writable_directory(directory: Path) -> None:
    try:
        descriptor, probe_name = tempfile.mkstemp(
            prefix=".vose-update-write-test-", dir=directory
        )
        os.close(descriptor)
        Path(probe_name).unlink(missing_ok=True)
    except OSError as exc:
        raise UpdateInstallError(
            f"アプリのインストール先に書き込み権限がありません: {directory}\n"
            "管理者権限での自動変更は行いません。Releaseページから手動更新してください。"
        ) from exc


def _write_helper(name: str, content: str) -> Path:
    path = get_update_cache_dir() / name
    path.write_text(content, encoding="utf-8", newline="\n")
    if os.name != "nt":
        path.chmod(0o700)
    return path


def _start_detached(command: list[str]) -> None:
    options: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        options["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    else:
        options["start_new_session"] = True
    try:
        subprocess.Popen(command, **options)  # noqa: S603
    except OSError as exc:
        raise UpdateInstallError(f"更新適用ヘルパーを起動できません: {exc}") from exc


def _validate_macos_update_zip(archive_path: Path) -> None:
    """Reject archive traversal and symlinks escaping the expected app bundle."""
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UpdateInstallError("macOS更新ZIPを開けません。") from exc

    found_main_binary = False
    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_MEMBERS:
            raise UpdateInstallError("macOS更新ZIPのファイル数が上限を超えています。")
        total_unpacked = sum(info.file_size for info in infos)
        if total_unpacked > MAX_MACOS_UNPACKED_BYTES:
            raise UpdateInstallError("macOS更新ZIPの展開サイズが上限を超えています。")
        for info in infos:
            name = info.filename
            if not name:
                continue
            member = PurePosixPath(name)
            if member.is_absolute() or ".." in member.parts:
                raise UpdateInstallError("macOS更新ZIPに不正なパスが含まれています。")
            # Apple ditto may place archived resource-fork metadata in
            # __MACOSX/. Keep validating every path, but do not treat these
            # metadata entries as additional application payload.
            if member.parts[0] == "__MACOSX":
                continue
            if member.parts[0] != MAC_APP_NAME:
                raise UpdateInstallError("macOS更新ZIPのアプリ構成が不正です。")

            if name.rstrip("/") == f"{MAC_APP_NAME}/{MAC_BINARY_RELATIVE.as_posix()}":
                found_main_binary = True

            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                try:
                    link_target = archive.read(info).decode("utf-8")
                except (UnicodeDecodeError, OSError) as exc:
                    raise UpdateInstallError("macOS更新ZIPのシンボリックリンクが不正です。") from exc
                if not link_target or link_target.startswith("/"):
                    raise UpdateInstallError("macOS更新ZIPに外部参照のリンクがあります。")
                resolved = PurePosixPath(
                    posixpath.normpath(
                        str(member.parent / PurePosixPath(link_target))
                    )
                )
                if not resolved.parts or resolved.parts[0] != MAC_APP_NAME or ".." in resolved.parts:
                    raise UpdateInstallError("macOS更新ZIPのリンクがアプリ外を参照しています。")

    if not found_main_binary:
        raise UpdateInstallError("macOS更新ZIPにアプリ本体がありません。")


def _find_macos_app_bundle(executable: Path) -> Path:
    for parent in executable.parents:
        if parent.suffix == ".app":
            if parent.name == MAC_APP_NAME and parent.is_dir():
                return parent
            raise UpdateInstallError(
                f"VO-SE Proのアプリバンドル名が想定と異なります: {parent.name}"
            )
    raise UpdateInstallError(
        "現在の実行ファイルがmacOSアプリバンドル内にありません。"
    )


def _extract_linux_portable(archive_path: Path, destination: Path) -> Path:
    try:
        archive = tarfile.open(archive_path, mode="r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise UpdateInstallError("Linux更新アーカイブを開けません。") from exc

    with archive:
        members = archive.getmembers()
        expected = [member for member in members if member.name == LINUX_BINARY_NAME]
        if (
            len(members) != 1
            or len(expected) != 1
            or not expected[0].isreg()
            or expected[0].size <= 0
            or expected[0].size > MAX_LINUX_BINARY_BYTES
        ):
            raise UpdateInstallError(
                "Linux更新アーカイブの内容が想定と異なります。適用を中止しました。"
            )
        source = archive.extractfile(expected[0])
        if source is None:
            raise UpdateInstallError("Linux更新アーカイブから実行ファイルを取得できません。")
        with source, destination.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
    destination.chmod(0o755)
    if destination.stat().st_size != expected[0].size:
        destination.unlink(missing_ok=True)
        raise UpdateInstallError("展開したLinux実行ファイルのサイズが一致しません。")
    return destination


def _schedule_windows_update(downloaded: Path) -> str:
    if downloaded.name != WINDOWS_ASSET_NAME:
        raise UpdateInstallError("Windows向け更新ファイル名が一致しません。")

    target = Path(os.path.abspath(sys.executable))
    if not target.is_file() or target.suffix.lower() != ".exe":
        raise UpdateInstallError("現在のWindows実行ファイルを特定できません。")
    _probe_writable_directory(target.parent)

    helper = _write_helper(
        "apply-update-windows.ps1",
        r"""param(
    [int]$ParentProcessId,
    [string]$StagedFile,
    [string]$TargetFile
)
$ErrorActionPreference = "Stop"
$logDir = Join-Path $env:LOCALAPPDATA "VO-SE Pro\updates"
$logFile = Join-Path $logDir "apply-update.log"
try {
    $deadline = (Get-Date).AddMinutes(3)
    while ((Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue) -and (Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
    }
    if (Get-Process -Id $ParentProcessId -ErrorAction SilentlyContinue) {
        throw "VO-SE did not exit in time."
    }
    $newFile = "$TargetFile.vose-new"
    $backupFile = "$TargetFile.vose-backup"
    if (Test-Path -LiteralPath $backupFile) {
        throw "A previous update backup exists; refusing to overwrite it."
    }
    Copy-Item -LiteralPath $StagedFile -Destination $newFile -Force
    Move-Item -LiteralPath $TargetFile -Destination $backupFile
    try {
        Move-Item -LiteralPath $newFile -Destination $TargetFile
        $process = Start-Process -FilePath $TargetFile -PassThru
        Start-Sleep -Seconds 5
        if (-not (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) {
            throw "Updated VO-SE did not remain running."
        }
        Remove-Item -LiteralPath $backupFile -Force
    }
    catch {
        if (Test-Path -LiteralPath $TargetFile) {
            Remove-Item -LiteralPath $TargetFile -Force
        }
        if (Test-Path -LiteralPath $backupFile) {
            Move-Item -LiteralPath $backupFile -Destination $TargetFile
        }
        throw
    }
}
catch {
    New-Item -ItemType Directory -Force -Path $logDir | Out-Null
    $_ | Out-File -FilePath $logFile -Encoding utf8
    exit 1
}
""",
    )
    _start_detached(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(helper),
            str(os.getpid()),
            str(downloaded),
            str(target),
        ]
    )
    return "更新ファイルを確認しました。VO-SE Proを終了し、更新後のアプリを起動します。"


def _schedule_macos_update(downloaded: Path) -> str:
    if not downloaded.name.endswith("_update.zip"):
        raise UpdateInstallError("macOS向け更新ZIPではありません。")

    executable = Path(os.path.abspath(sys.executable))
    app_bundle = _find_macos_app_bundle(executable)
    _probe_writable_directory(app_bundle.parent)
    _validate_macos_update_zip(downloaded)

    # Extract beside the installed app so the final directory rename stays on
    # the same filesystem and remains atomic.
    stage_dir = app_bundle.parent / f".vose-update-stage-{os.getpid()}"
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)
    ditto = "/usr/bin/ditto"
    if not Path(ditto).is_file():
        raise UpdateInstallError("macOS標準のdittoコマンドが見つかりません。")
    try:
        subprocess.run(
            [ditto, "-x", "-k", str(downloaded), str(stage_dir)],
            check=True,
            timeout=300,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise UpdateInstallError(f"macOS更新ファイルを展開できません: {exc}") from exc

    staged_app = stage_dir / MAC_APP_NAME
    staged_binary = staged_app / MAC_BINARY_RELATIVE
    if not staged_binary.is_file():
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise UpdateInstallError("展開したmacOSアプリの実行ファイルがありません。")
    try:
        subprocess.run(
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(staged_app)],
            check=True,
            timeout=60,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise UpdateInstallError(
            f"macOS更新アプリのコード署名を検証できません: {exc}"
        ) from exc

    helper = _write_helper(
        "apply-update-macos.sh",
        r"""#!/bin/sh
set -eu
parent_pid="$1"
target_app="$2"
staged_app="$3"
backup_app="$4"
while kill -0 "$parent_pid" 2>/dev/null; do
    sleep 0.5
done
if [ -e "$backup_app" ]; then
    echo "Previous update backup already exists; refusing to overwrite it." >&2
    exit 1
fi
mv "$target_app" "$backup_app"
if ! mv "$staged_app" "$target_app"; then
    mv "$backup_app" "$target_app"
    exit 1
fi
if ! /usr/bin/open -a "$target_app"; then
    rm -rf "$target_app"
    mv "$backup_app" "$target_app"
    /usr/bin/open -a "$target_app" || true
    exit 1
fi
sleep 10
binary="$target_app/Contents/MacOS/VO-SE_vocal_Mac"
if /bin/ps -axo command= | /usr/bin/grep -F "$binary" | /usr/bin/grep -v grep >/dev/null 2>&1; then
    rm -rf "$backup_app"
else
    rm -rf "$target_app"
    mv "$backup_app" "$target_app"
    /usr/bin/open -a "$target_app" || true
    rm -rf "$(dirname "$staged_app")"
    exit 1
fi
rm -rf "$(dirname "$staged_app")"
""",
    )
    backup_app = app_bundle.with_name(f"{app_bundle.name}.vose-backup-{os.getpid()}")
    _start_detached(
        [
            "/bin/sh",
            str(helper),
            str(os.getpid()),
            str(app_bundle),
            str(staged_app),
            str(backup_app),
        ]
    )
    return "更新を適用するためVO-SE Proを終了し、新しいアプリを起動します。"


def _schedule_linux_deb_update(downloaded: Path) -> str:
    if downloaded.name != "vo-se-vocal_amd64.deb":
        raise UpdateInstallError("Debian/Ubuntu向けパッケージ名が一致しません。")
    target = Path(os.path.abspath(sys.executable))
    if target.resolve() != Path("/opt/vo-se-vocal/VO-SE_vocal_Linux"):
        raise UpdateInstallError("インストール済みDEBの実行パスを確認できません。")

    pkexec = shutil.which("pkexec")
    apt_get = "/usr/bin/apt-get"
    if not pkexec or not Path(apt_get).is_file():
        raise UpdateInstallError(
            "pkexecまたはapt-getがありません。ダウンロード済みDEBをパッケージマネージャーから手動で更新してください。"
        )

    helper = _write_helper(
        "apply-update-deb.sh",
        r"""#!/bin/sh
set -u
parent_pid="$1"
deb_file="$2"
target_app="$3"
while kill -0 "$parent_pid" 2>/dev/null; do
    sleep 0.5
done
if pkexec /usr/bin/env DEBIAN_FRONTEND=noninteractive /usr/bin/apt-get install --reinstall -y "$deb_file"; then
    nohup "$target_app" >/dev/null 2>&1 &
else
    status=$?
    # Cancellation or package-manager failure must leave the old app usable.
    nohup "$target_app" >/dev/null 2>&1 &
    exit "$status"
fi
""",
    )
    _start_detached(
        ["/bin/sh", str(helper), str(os.getpid()), str(downloaded), str(target)]
    )
    return (
        "Debianパッケージを更新します。アプリ終了後、OSの認証画面で"
        "管理者パスワードを確認します。認証を許可すると更新後に再起動します。"
    )


def _schedule_linux_portable_update(downloaded: Path) -> str:
    if downloaded.name != "VO-SE_vocal_Linux_x86_64.tar.gz":
        raise UpdateInstallError("Linux向け更新アーカイブ名が一致しません。")

    target = Path(os.path.abspath(sys.executable))
    if target.is_symlink():
        raise UpdateInstallError(
            "シンボリックリンク経由の起動では安全に置き換えできません。"
            "Releaseページから手動更新してください。"
        )
    if not target.is_file():
        raise UpdateInstallError("現在のLinux実行ファイルを特定できません。")
    _probe_writable_directory(target.parent)

    staged = get_update_cache_dir() / f"VO-SE_vocal_Linux.update-{os.getpid()}"
    _extract_linux_portable(downloaded, staged)
    helper = _write_helper(
        "apply-update-linux.sh",
        r"""#!/bin/sh
set -eu
parent_pid="$1"
staged_file="$2"
target_file="$3"
while kill -0 "$parent_pid" 2>/dev/null; do
    sleep 0.5
done
new_file="$target_file.vose-new"
backup_file="$target_file.vose-backup-$$"
cp "$staged_file" "$new_file"
chmod 755 "$new_file"
mv "$target_file" "$backup_file"
if ! mv "$new_file" "$target_file"; then
    mv "$backup_file" "$target_file"
    exit 1
fi
nohup "$target_file" >/dev/null 2>&1 &
new_pid=$!
sleep 5
if kill -0 "$new_pid" 2>/dev/null; then
    rm -f "$backup_file" "$staged_file"
else
    rm -f "$target_file"
    mv "$backup_file" "$target_file"
fi
""",
    )
    _start_detached(
        ["/bin/sh", str(helper), str(os.getpid()), str(staged), str(target)]
    )
    return "更新を適用するためVO-SE Proを終了し、新しい実行ファイルを起動します。"


def prepare_and_schedule_update(downloaded_path: str | os.PathLike[str]) -> str:
    """Validate the staged package and start its external apply helper."""
    _require_frozen_application()
    downloaded = Path(downloaded_path)
    if not downloaded.is_file():
        raise UpdateInstallError("ダウンロード済み更新ファイルが見つかりません。")

    if sys.platform == "win32":
        return _schedule_windows_update(downloaded)
    if sys.platform == "darwin":
        return _schedule_macos_update(downloaded)
    if sys.platform.startswith("linux"):
        if is_deb_installation():
            return _schedule_linux_deb_update(downloaded)
        return _schedule_linux_portable_update(downloaded)
    raise UpdateInstallError("このOSの自動更新はまだサポートされていません。")
