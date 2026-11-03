#zip_handler.py


import zipfile
import os
import shutil

from modules.utils.text_encoding import safe_extract_zip

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
                
                # 日本語名(CP932・UTF-8フラグ無し)のZIPでも名前が化けないよう共通処理で展開する
                safe_extract_zip(zip_ref, extract_path)
                return True, bank_name
        except Exception as e:
            return False, str(e)
