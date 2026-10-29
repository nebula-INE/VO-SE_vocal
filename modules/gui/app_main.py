#app_main.py

import sys
import os
import ctypes
import logging

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from PySide6.QtCore import QTimer

# --- 自作モジュールのインポート ---
# フォルダ構成に合わせてパスを調整（絶対インポートを推奨）
from modules.gui.main_window import MainWindow

# PyInstallerのスプラッシュスクリーン制御
try:
    import pyi_splash
except ImportError:
    pyi_splash = None

def main():
    # 1. 環境設定
    # Windows/Macでの高DPIスケーリングを有効化
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    os.environ["QT_AUTOSCREEN_SCALE_FACTOR"] = "1"
    
    # ロギング設定
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

    app = QApplication(sys.argv)
    app.setApplicationName("VO-SE Pro")
    app.setOrganizationName("VO-SE Project")
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))

    # --- 2. スタイルシート（themes/*.qss に一本化） ---
    from modules.gui.themes import apply_theme
    from PySide6.QtCore import QSettings
    apply_theme(str(QSettings("VO-SE", "vocal").value("theme", "dark")))

    # --- 3. バックエンドの初期化（スプラッシュ表示中に実行） ---
    try:
        import importlib
        if pyi_splash:
            pyi_splash.update_text("UIを構築中...")

        # 音声出力は軽量なので先に作っておく
        AudioOutput = importlib.import_module("modules.audio.audio_output").AudioOutput  # type: ignore[attr-defined]
        audio_device = AudioOutput(sample_rate=44100, block_size=256)

        # ウィンドウを先に作成（エンジンは後でセット）
        window = MainWindow(engine=None, ai=None)
        window.audio_output = audio_device

        if pyi_splash:
            pyi_splash.close()

        window.show()

        # --- 【遅延実行】重いエンジンとAIを非同期でロード ---
        def load_heavy_engines():
            try:
                VO_SE_Engine = importlib.import_module("modules.audio.vo_se_engine").VO_SE_Engine  # type: ignore[attr-defined]
                AIManager = importlib.import_module("modules.ai.ai_manager").AIManager  # type: ignore[attr-defined]

                window.statusBar().showMessage("音声エンジンをロード中...")
                engine = VO_SE_Engine()
                window.vo_se_engine = engine

                window.statusBar().showMessage("AI推論モデルを最適化中...")
                ai = AIManager()
                ai.init_model()
                window.ai_manager = ai

                # エンジンがロードされたらステータス更新
                if hasattr(window, 'statusBar'):
                    window.statusBar().showMessage("VO-SE Pro エンジン準備完了", 3000)

                # もし初期ノートがあればキャッシュ準備
                if hasattr(window, 'on_timeline_updated'):
                    window.on_timeline_updated()

            except Exception as e:
                print(f"エンジン遅延ロードエラー: {e}")
                if hasattr(window, 'statusBar'):
                    window.statusBar().showMessage(f"エンジンロードエラー: {e}", 5000)

        # メインウィンドウ表示後 200ms 待ってからロード開始
        QTimer.singleShot(200, load_heavy_engines)

    except Exception as e:
        logging.critical(f"アプリケーションの起動に失敗しました: {e}")
        if pyi_splash:
            pyi_splash.close()
        return

    sys.exit(app.exec())
    
# Windowsのタスクバーアイコン個別認識用（PyInstallerで必須）
if platform_system := os.name == 'nt':
    try:
        myappid = 'vose.pro.editor.v1'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

if __name__ == "__main__":
    main()
