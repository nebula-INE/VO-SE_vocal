"""Crash-recovery snapshots for VO-SE project data.

This module is deliberately independent from the synthesizer, playback mixer and
WAV export pipeline. It stores the same JSON project payload used by .vose files.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


class AutosaveError(ValueError):
    """Raised when an autosave cannot be written or safely restored."""


def default_autosave_path() -> Path:
    """Return a per-user writable path outside the installation directory."""
    override = os.environ.get("VOSE_AUTOSAVE_PATH", "").strip()
    if override:
        return Path(override).expanduser()

    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        data_dir = root / "VO-SE Project" / "vocal"
    elif sys.platform == "darwin":
        data_dir = Path.home() / "Library" / "Application Support" / "VO-SE Project" / "vocal"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        data_dir = root / "VO-SE Project" / "vocal"
    return data_dir / "project-recovery.json"


class ProjectAutosaveStore:
    """Atomically persist and validate one recoverable project snapshot."""

    FORMAT_ID = "vose-project-autosave"
    FORMAT_VERSION = 1

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else default_autosave_path()

    @staticmethod
    def fingerprint(project_data: Mapping[str, Any]) -> str:
        """Create a stable digest for deciding whether the project changed."""
        try:
            encoded = json.dumps(
                project_data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise AutosaveError(f"プロジェクトを自動保存用JSONに変換できません: {exc}") from exc
        return hashlib.sha256(encoded).hexdigest()

    def write(
        self,
        project_data: Mapping[str, Any],
        *,
        source_path: str = "",
        was_saved: bool = False,
    ) -> dict[str, Any]:
        """Write a complete recovery envelope without exposing partial files."""
        if not isinstance(project_data, Mapping):
            raise AutosaveError("自動保存データの形式が不正です。")
        source = str(source_path or "")
        envelope = {
            "format": self.FORMAT_ID,
            "version": self.FORMAT_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "source_path": os.path.abspath(os.path.expanduser(source)) if source else "",
            "was_saved": bool(was_saved),
            "project": dict(project_data),
        }
        try:
            payload = json.dumps(
                envelope,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise AutosaveError(f"自動保存データの生成に失敗しました: {exc}") from exc

        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".vose-autosave-",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        except OSError as exc:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise AutosaveError(f"自動保存ファイルを書き込めません: {exc}") from exc
        finally:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
        return envelope

    def read(self) -> dict[str, Any] | None:
        """Return a validated snapshot, or None when no recovery exists."""
        if not self.path.is_file():
            return None
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                envelope = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AutosaveError(f"自動保存ファイルを読み込めません: {exc}") from exc

        if not isinstance(envelope, dict):
            raise AutosaveError("自動保存ファイルのルート形式が不正です。")
        if envelope.get("format") != self.FORMAT_ID:
            raise AutosaveError("自動保存ファイルの形式が対応していません。")
        if envelope.get("version") != self.FORMAT_VERSION:
            raise AutosaveError("自動保存ファイルのバージョンが対応していません。")

        project = envelope.get("project")
        source_path = envelope.get("source_path", "")
        saved_at = envelope.get("saved_at")
        was_saved = envelope.get("was_saved")
        if not isinstance(project, dict):
            raise AutosaveError("自動保存されたプロジェクト本体が不正です。")
        if not isinstance(source_path, str) or not isinstance(saved_at, str):
            raise AutosaveError("自動保存ファイルのメタデータが不正です。")
        if not isinstance(was_saved, bool):
            raise AutosaveError("自動保存ファイルの保存状態が不正です。")
        try:
            datetime.fromisoformat(saved_at)
        except ValueError as exc:
            raise AutosaveError("自動保存時刻が不正です。") from exc
        return envelope

    def clear(self) -> None:
        """Remove a consumed or explicitly discarded recovery snapshot."""
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise AutosaveError(f"自動保存ファイルを削除できません: {exc}") from exc
