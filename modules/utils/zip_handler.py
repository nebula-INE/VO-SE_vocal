#zip_handler.py


import os
import shutil
import tempfile
import uuid
import zipfile

from modules.utils.text_encoding import safe_extract_zip


class ZipHandler:
    @staticmethod
    def extract_voice_bank(zip_path, target_root="voice_banks"):
        """ZIPを安全な一時ディレクトリに展開し、検証後に音源を原子的に差し替える。"""
        try:
            os.makedirs(target_root, exist_ok=True)
            target_root = os.path.realpath(target_root)
            bank_name = os.path.splitext(os.path.basename(zip_path))[0]
            if (
                not bank_name
                or bank_name in {".", ".."}
                or os.sep in bank_name
                or (os.altsep and os.altsep in bank_name)
                or "\x00" in bank_name
            ):
                raise ValueError("不正な音源名です。")

            extract_path = os.path.join(target_root, bank_name)
            if os.path.commonpath([target_root, os.path.abspath(extract_path)]) != target_root:
                raise ValueError("音源の展開先が対象フォルダ外です。")

            staging_path = tempfile.mkdtemp(
                prefix=f".{bank_name}.staging-", dir=target_root
            )
            backup_path = os.path.join(
                target_root, f".{bank_name}.backup-{uuid.uuid4().hex}"
            )
            backup_created = False
            try:
                with zipfile.ZipFile(zip_path, "r") as zip_ref:
                    safe_extract_zip(zip_ref, staging_path, collect_paths=False)

                if os.path.lexists(extract_path):
                    if os.path.islink(extract_path) or not os.path.isdir(extract_path):
                        raise ValueError("既存の音源保存先が安全なディレクトリではありません。")
                    os.replace(extract_path, backup_path)
                    backup_created = True

                try:
                    os.replace(staging_path, extract_path)
                    staging_path = None
                except Exception:
                    if backup_created:
                        os.replace(backup_path, extract_path)
                        backup_created = False
                    raise

                if backup_created:
                    shutil.rmtree(backup_path, ignore_errors=True)
                    backup_created = False
                return True, bank_name
            finally:
                if staging_path and os.path.lexists(staging_path):
                    shutil.rmtree(staging_path, ignore_errors=True)
                if backup_created and os.path.lexists(backup_path):
                    if not os.path.lexists(extract_path):
                        os.replace(backup_path, extract_path)
                    else:
                        shutil.rmtree(backup_path, ignore_errors=True)
        except Exception as exc:
            return False, str(exc)
