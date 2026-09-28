#zip_handler.py


import zipfile
import os
import shutil

class ZipHandler:
    @staticmethod
    def extract_voice_bank(zip_path, target_root="voice_banks"):
        """ZIPを解凍してボイスバンクへ登録。解凍後のフォルダ名を返す"""
        if not os.path.exists(target_root):
            os.makedirs(target_root)

        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # ZIP名からフォルダ名を決定
                bank_name = os.path.splitext(os.path.basename(zip_path))[0]
                extract_path = os.path.join(target_root, bank_name)
                
                # 既に同名フォルダがある場合は一度削除（更新）
                if os.path.exists(extract_path):
                    shutil.rmtree(extract_path)
                
                root = os.path.realpath(extract_path)
                for member in zip_ref.infolist():
                    member_name = member.filename.replace("\\\\", "/")
                    if os.path.isabs(member_name) or member_name.startswith("/"):
                        raise ValueError(f"不正なZIPパスです: {member.filename}")
                    destination = os.path.realpath(os.path.join(extract_path, member_name))
                    if os.path.commonpath([root, destination]) != root:
                        raise ValueError(f"ZIP展開先が対象フォルダ外です: {member.filename}")
                    if member.is_dir():
                        os.makedirs(destination, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(destination), exist_ok=True)
                    with zip_ref.open(member) as source, open(destination, "wb") as target:
                        shutil.copyfileobj(source, target)
                return True, bank_name
        except Exception as e:
            return False, str(e)
