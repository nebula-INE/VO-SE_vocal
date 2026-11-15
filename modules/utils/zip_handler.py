# zip_handler.py

import os
import shutil
import tempfile
import zipfile

from modules.utils.text_encoding import safe_extract_zip


def _remove_path(target: str) -> None:
    """Remove a file, symlink, or directory without following a symlink target."""
    if os.path.islink(target) or not os.path.isdir(target):
        if os.path.lexists(target):
            os.unlink(target)
    elif os.path.lexists(target):
        shutil.rmtree(target)


class ZipHandler:
    @staticmethod
    def extract_voice_bank(zip_path, target_root="voice_banks"):
        """ZIPを安全な一時領域へ展開し、検証後に音源フォルダを置き換える。"""
        staging_path = None
        backup_path = None
        moved_old = False

        try:
            raw_name = os.path.basename(str(zip_path).replace("\\", "/"))
            bank_name = os.path.splitext(raw_name)[0]
            reserved = {"CON", "PRN", "AUX", "NUL"}
            reserved.update(f"COM{i}" for i in range(1, 10))
            reserved.update(f"LPT{i}" for i in range(1, 10))
            if (
                not bank_name
                or len(bank_name) > 100
                or bank_name in {".", ".."}
                or any(char in bank_name for char in '/\\<>:"|?*')
                or bank_name.endswith(".")
                or bank_name.split(".", 1)[0].upper() in reserved
                or any(ord(char) < 32 for char in bank_name)
            ):
                raise ValueError("ZIPファイル名から安全な音源名を作成できません。")

            os.makedirs(target_root, exist_ok=True)
            root = os.path.realpath(target_root)
            extract_path = os.path.join(root, bank_name)
            if os.path.commonpath([root, os.path.abspath(extract_path)]) != root:
                raise ValueError("音源の展開先が対象フォルダ外です。")

            with zipfile.ZipFile(zip_path, "r") as zip_ref:
                staging_path = tempfile.mkdtemp(
                    prefix=f".{bank_name}.staging-", dir=root
                )
                extracted = safe_extract_zip(zip_ref, staging_path)
                if not extracted:
                    raise ValueError("ZIPに展開可能なファイルがありません。")

            if os.path.lexists(extract_path):
                backup_placeholder = tempfile.mkdtemp(
                    prefix=f".{bank_name}.backup-", dir=root
                )
                os.rmdir(backup_placeholder)
                backup_path = backup_placeholder
                os.replace(extract_path, backup_path)
                moved_old = True

            try:
                os.replace(staging_path, extract_path)
                staging_path = None
            except Exception:
                if moved_old and backup_path and os.path.lexists(backup_path):
                    os.replace(backup_path, extract_path)
                    moved_old = False
                raise

            if moved_old and backup_path and os.path.lexists(backup_path):
                try:
                    _remove_path(backup_path)
                    moved_old = False
                except OSError:
                    # The new bank is already installed. A stale backup is safer
                    # than rolling back a valid replacement because cleanup failed.
                    pass

            return True, bank_name

        except Exception as error:
            if moved_old and backup_path and os.path.lexists(backup_path):
                try:
                    if not os.path.lexists(extract_path):
                        os.replace(backup_path, extract_path)
                except OSError:
                    pass
            return False, str(error)
        finally:
            if staging_path and os.path.lexists(staging_path):
                try:
                    _remove_path(staging_path)
                except OSError:
                    pass
