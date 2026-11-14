"""Authenticated download of the platform-specific VO-SE Release asset."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import sys
import tempfile
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Any

from PySide6.QtCore import QThread, Signal

logger = logging.getLogger(__name__)

GITHUB_REPOSITORY = "nebula-INE/VO-SE_vocal"
LATEST_RELEASE_API = (
    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/tags/latest"
)
DOWNLOAD_URL_PREFIX = (
    f"https://github.com/{GITHUB_REPOSITORY}/releases/download/"
)
REQUEST_TIMEOUT_SECONDS = 30
DOWNLOAD_TIMEOUT_SECONDS = 60
CHUNK_SIZE = 1024 * 1024
MAX_ASSET_SIZE = 1024 * 1024 * 1024


class UpdateError(RuntimeError):
    """Raised when an update asset cannot be selected or verified."""


class UpdateCancelledError(UpdateError):
    """Raised when the user cancels an update download."""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    download_url: str
    size: int
    sha256: str
    version: str


def _normalized_machine(machine: str) -> str:
    value = machine.strip().lower()
    if value in {"amd64", "x64", "x86-64"}:
        return "x86_64"
    if value in {"aarch64", "arm64"}:
        return "arm64"
    return value


def is_deb_installation(executable: str | os.PathLike[str] | None = None) -> bool:
    """Return True only for the exact path installed by our Debian package."""
    if sys.platform != "linux" and executable is None:
        return False
    path = Path(executable or sys.executable)
    try:
        return path.resolve() == Path("/opt/vo-se-vocal/VO-SE_vocal_Linux")
    except OSError:
        return False


def platform_asset_name(
    system: str | None = None,
    machine: str | None = None,
    executable: str | os.PathLike[str] | None = None,
) -> str:
    """Select only a release artifact built for the current OS and architecture."""
    system_name = system or platform.system()
    arch = _normalized_machine(machine or platform.machine())

    if system_name == "Windows":
        if arch == "x86_64":
            return "VO-SE_vocal_Win.exe"
        raise UpdateError(f"Windows {arch} 用の更新ファイルはありません。")

    if system_name == "Darwin":
        if arch == "arm64":
            return "VO-SE_vocal_Mac_AppleSilicon_update.zip"
        if arch == "x86_64":
            return "VO-SE_vocal_Mac_Intel_update.zip"
        raise UpdateError(f"macOS {arch} 用の更新ファイルはありません。")

    if system_name == "Linux":
        if arch != "x86_64":
            raise UpdateError(f"Linux {arch} 用の更新ファイルはありません。")
        if is_deb_installation(executable):
            return "vo-se-vocal_amd64.deb"
        return "VO-SE_vocal_Linux_x86_64.tar.gz"

    raise UpdateError(f"{system_name} は自動アップデートに対応していません。")


def _parse_sha256_digest(value: Any) -> str:
    if not isinstance(value, str):
        raise UpdateError("ReleaseにSHA-256 digestがありません。安全のため更新を中止しました。")
    match = re.fullmatch(r"sha256:([0-9a-fA-F]{64})", value)
    if not match:
        raise UpdateError("ReleaseのSHA-256 digest形式が不正です。")
    return match.group(1).lower()


def select_release_asset(
    release: dict[str, Any],
    system: str | None = None,
    machine: str | None = None,
    executable: str | os.PathLike[str] | None = None,
) -> ReleaseAsset:
    expected_name = platform_asset_name(system, machine, executable)
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("GitHub Releaseに配布ファイル一覧がありません。")

    selected = next(
        (
            item
            for item in assets
            if isinstance(item, dict) and item.get("name") == expected_name
        ),
        None,
    )
    if selected is None:
        raise UpdateError(f"この環境向けの配布ファイル {expected_name} がありません。")

    url = selected.get("browser_download_url")
    if not isinstance(url, str) or not url.startswith(DOWNLOAD_URL_PREFIX):
        raise UpdateError("更新ファイルのダウンロード先が不正です。")

    size = selected.get("size")
    if not isinstance(size, int) or size <= 0 or size > MAX_ASSET_SIZE:
        raise UpdateError("更新ファイルのサイズ情報が不正です。")

    version = release.get("name") or release.get("tag_name") or "latest"
    if not isinstance(version, str):
        version = "latest"

    return ReleaseAsset(
        name=expected_name,
        download_url=url,
        size=size,
        sha256=_parse_sha256_digest(selected.get("digest")),
        version=version,
    )


def _read_latest_release() -> dict[str, Any]:
    request = urllib.request.Request(
        LATEST_RELEASE_API,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "VO-SE-Pro-Updater",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(
        request, timeout=REQUEST_TIMEOUT_SECONDS
    ) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise UpdateError("GitHub Release APIの応答形式が不正です。")
    return payload


def get_update_cache_dir() -> Path:
    """Return a per-user writable directory outside the installed application."""
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        directory = root / "VO-SE Pro" / "updates"
    elif sys.platform == "darwin":
        directory = Path.home() / "Library/Application Support/VO-SE Pro/updates"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        directory = root / "vo-se-pro" / "updates"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def download_release_asset(
    asset: ReleaseAsset,
    destination_dir: str | os.PathLike[str] | None = None,
    progress_callback: Callable[[int, str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Stream, size-check, and SHA-256-check a Release asset before publishing it."""
    output_dir = Path(destination_dir) if destination_dir else get_update_cache_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / asset.name
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{asset.name}.",
        suffix=".part",
        dir=output_dir,
    )
    temporary_path = Path(temporary_name)
    digest = hashlib.sha256()
    received = 0

    request = urllib.request.Request(
        asset.download_url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "VO-SE-Pro-Updater",
        },
    )

    try:
        with os.fdopen(descriptor, "wb") as output:
            with urllib.request.urlopen(
                request, timeout=DOWNLOAD_TIMEOUT_SECONDS
            ) as response:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise UpdateCancelledError("ダウンロードはキャンセルされました。")
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > asset.size:
                        raise UpdateError("ダウンロードサイズがReleaseの記録を超えました。")
                    digest.update(chunk)
                    output.write(chunk)
                    if progress_callback:
                        percent = min(99, int(received * 100 / asset.size))
                        progress_callback(
                            percent,
                            f"{asset.name} をダウンロード中 "
                            f"({received // (1024 * 1024)} / "
                            f"{asset.size // (1024 * 1024)} MB)",
                        )
            output.flush()
            os.fsync(output.fileno())

        if received != asset.size:
            raise UpdateError(
                f"ダウンロードサイズが一致しません（expected={asset.size}, actual={received}）。"
            )
        actual_digest = digest.hexdigest()
        if actual_digest != asset.sha256:
            raise UpdateError("SHA-256検証に失敗しました。ファイルは適用しません。")

        os.replace(temporary_path, destination)
        if progress_callback:
            progress_callback(100, "ダウンロードとSHA-256検証が完了しました。")
        return destination
    except Exception:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not remove incomplete update download: %s", temporary_path)
        raise


class UpdateDownloadWorker(QThread):
    """Downloads the correct Release asset without blocking the GUI thread."""

    progress = Signal(int, str)
    completed = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    def run(self) -> None:
        try:
            release = _read_latest_release()
            asset = select_release_asset(release)
            path = download_release_asset(
                asset,
                progress_callback=self.progress.emit,
                cancel_event=self._cancel_event,
            )
            self.completed.emit(str(path))
        except UpdateCancelledError:
            self.cancelled.emit()
        except Exception as exc:
            logger.warning("VO-SE update download failed", exc_info=True)
            self.failed.emit(str(exc))
