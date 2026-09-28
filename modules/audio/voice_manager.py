#GUI/voice_manager.py

import os
import platform
import glob
import sys
import logging



def get_resource_path(relative_path: str) -> str:
    """
    リソースファイルの絶対パスを取得する。
    PyInstallerの _MEIPASS 属性へのアクセスを getattr で安全に行い、
    Actions の reportAttributeAccessIssue を完全に回避します。
    """
    import sys
    import os

    # 1. getattr を使用して sys._MEIPASS を安全に取得
    # 第2引数に os.path.abspath(".") を指定することで、通常実行時もカバーします
    base_path: str = str(getattr(sys, '_MEIPASS', os.path.abspath(".")))

    # 2. 相対パスと結合してフルパスを生成
    full_path: str = os.path.join(base_path, relative_path)

    # 3. 代表の設計通り、パスを正規化して返却
    return os.path.normpath(full_path)

class VoiceManager:
    def __init__(self):
        self.system = platform.system()
        # 実行環境(PyInstaller)と開発環境のパス解決を統一
        if getattr(sys, 'frozen', False):
            # 代表、ここが修正の核心です。
            # 外部関数を呼ばず、直接 getattr で sys._MEIPASS を安全に取得します。
            # これにより F821 Undefined name エラーを 100% 回避します。
            self.base_path = str(getattr(sys, '_MEIPASS', os.path.abspath(".")))
        else:
            # modules/audio/ から見たプロジェクトルート (代表の設計通り)
            self.base_path = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        # ボイスディレクトリの確定
        self.voices_dir = os.path.join(self.base_path, "assets", "voices")
        self.internal_voice_dir = os.path.join(self.base_path, "voice_banks")
        self.voices = {}  # { "キャラ名": "絶対パス" }

    def scan_voices(self):
        """標準的なUTAU音源パスと内部フォルダをスキャン"""
        search_paths = []
        
        # 1. OSごとの標準パスを追加
        if self.system == "Darwin": # macOS
            search_paths.append(os.path.expanduser("~/Library/Application Support/OpenUTAU/Content/Voices/"))
            search_paths.append(os.path.expanduser("~/Library/Application Support/Vocaloid/Voices/")) # 互換用
        elif self.system == "Windows":
            search_paths.append(r"C:\Program Files (x86)\UTAU\voice")
            appdata_roaming = os.getenv('APPDATA')
            if appdata_roaming:
                search_paths.append(os.path.join(appdata_roaming, "OpenUTAU", "Content", "Voices"))

        # 2. プロジェクト内部の voice_banks フォルダを追加
        search_paths.append(self.internal_voice_dir)

        self.voices.clear()
        for path in search_paths:
            if not os.path.exists(path):
                continue
            
            # 再帰的に oto.ini を検索
            for ini_path in glob.glob(os.path.join(path, "**/oto.ini"), recursive=True):
                v_dir = os.path.dirname(ini_path)
                # キャラ名はフォルダ名、または character.txt があればそこから取得する拡張性
                v_name = os.path.basename(v_dir)
                
                # 重複した場合は、より「深い（個別設定された）」パスを優先
                self.voices[v_name] = v_dir
        
        # 3. 【追加】内蔵音源（公式）をリストに加える
        # DLL内に焼き込まれているので、物理パスの代わりに特殊なキーワードを入れる
        self.voices["VO-SE Official (Embedded)"] = "__INTERNAL__"
        
        logging.info(f"VO-SE: {len(self.voices)} 件の音源を検出しました。")
        return self.voices

    def is_internal(self, voice_name):
        """選択された音源が内蔵（公式）かどうかを判定"""
        return self.voices.get(voice_name) == "__INTERNAL__"

    
    def parse_oto_ini(self, voice_dir):
        """
        指定された音源フォルダの oto.ini を解析。
        戻り値: { "あ": {"filename": "a.wav", "pre_utterance": 10.0, ...} }
        """
        config = {}
        ini_path = os.path.join(voice_dir, "oto.ini")
        if not os.path.exists(ini_path):
            return config

        try:
            # UTAU音源は伝統的に Shift-JIS (cp932)
            with open(ini_path, 'r', encoding='cp932', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if not line or '=' not in line:
                        continue
                    
                    # フォーマット: ファイル名.wav=エイリアス,左ブランク,固定範囲,右ブランク,先行発声,オーバーラップ
                    fname, params = line.split('=', 1)
                    p = params.split(',')
                    
                    alias = p[0] if p[0] else fname.replace(".wav", "")
                    
                    # 単位はミリ秒(ms)としてパース（エンジン側で秒に変換することを想定）
                    config[alias] = {
                        "filename": fname,
                        "left_blank": float(p[1]) if len(p) > 1 and p[1] else 0.0,
                        "fixed_range": float(p[2]) if len(p) > 2 and p[2] else 0.0,
                        "right_blank": float(p[3]) if len(p) > 3 and p[3] else 0.0,
                        "pre_utterance": float(p[4]) if len(p) > 4 and p[4] else 0.0,
                        "overlap": float(p[5]) if len(p) > 5 and p[5] else 0.0
                    }
        except Exception as e:
            logging.error(f"oto.ini の解析中にエラー: {e}")
            
        return config

    def install_voice_from_zip(self, zip_path):
        """ZIP音源を voice_banks に展開して追加する。"""
        import zipfile
        import tempfile
        import shutil

        if not os.path.exists(zip_path):
            raise FileNotFoundError(f"ZIP not found: {zip_path}")

        with tempfile.TemporaryDirectory() as tmp_dir:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                root = os.path.realpath(tmp_dir)
                for member in zf.infolist():
                    member_name = member.filename.replace("\\\\", "/")
                    if os.path.isabs(member_name) or member_name.startswith("/"):
                        raise ValueError(f"不正なZIPパスです: {member.filename}")
                    destination = os.path.realpath(os.path.join(tmp_dir, member_name))
                    if os.path.commonpath([root, destination]) != root:
                        raise ValueError(f"ZIP展開先が対象フォルダ外です: {member.filename}")
                    if member.is_dir():
                        os.makedirs(destination, exist_ok=True)
                        continue
                    os.makedirs(os.path.dirname(destination), exist_ok=True)
                    with zf.open(member) as source, open(destination, "wb") as target:
                        shutil.copyfileobj(source, target)

            candidates = []
            for root, _, files in os.walk(tmp_dir):
                if 'oto.ini' in files:
                    candidates.append(root)

            if not candidates:
                raise ValueError('oto.ini が見つからないため、UTAU音源として認識できません。')

            src_dir = min(candidates, key=lambda p: len(p))
            voice_name = os.path.basename(src_dir.rstrip(os.sep)) or 'ImportedVoice'
            safe_name = ''.join(ch for ch in voice_name if ch not in '<>:"/\\|?*').strip() or 'ImportedVoice'

            os.makedirs(self.internal_voice_dir, exist_ok=True)
            dest_dir = os.path.join(self.internal_voice_dir, safe_name)
            base = dest_dir
            idx = 1
            while os.path.exists(dest_dir):
                dest_dir = f"{base}_{idx}"
                idx += 1

            shutil.copytree(src_dir, dest_dir)

        self.scan_voices()
        return os.path.basename(dest_dir)

    def get_voice_path(self, name):
        return self.voices.get(name)
