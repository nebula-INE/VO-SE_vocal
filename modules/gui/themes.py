import os
from PySide6.QtCore import QFile, QTextStream, QIODevice, QStringConverter
from PySide6.QtWidgets import QApplication

# QSSはリポジトリ直下の themes/ にある（このファイルは modules/gui/ なので2階層上）。
# PyInstaller配布版では vose_pro.spec の datas で themes/ を同名で同梱する。
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

THEME_FILES = {
    "dark": os.path.join(_REPO_ROOT, "themes", "dark.qss"),
    "light": os.path.join(_REPO_ROOT, "themes", "light.qss"),
}

def apply_theme(theme_name: str) -> bool:
    """
    アプリケーション全体に指定されたテーマ（QSS）を適用する。
    
    Args:
        theme_name (str): "dark" または "light"
        
    Returns:
        bool: 適用に成功した場合は True、失敗した場合は False
    """
    # 🌟 修正ポイント1: QApplication のインスタンスを取得
    # Pyright に 'QApplication'（または QWidget 派生）であることを明示し、
    # setStyleSheet メソッドを安全に呼び出せるようにします。
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        print("[Theme Error] QApplication instance does not exist or is not fully initialized.")
        return False

    # 指定されたテーマがない場合は "dark" をデフォルトにする
    file_path = THEME_FILES.get(theme_name)
    if not file_path:
        print(f"[Theme Warning] Theme '{theme_name}' not found. Falling back to 'dark'.")
        file_path = THEME_FILES["dark"]
    
    # QSSファイルの存在チェック
    if not os.path.exists(file_path):
        print(f"[Theme Error] QSS file not found at: {file_path}")
        return False

    # ファイルの読み込みと適用
    file = QFile(file_path)
    
    # 🌟 修正ポイント2: OpenModeFlag の明示 (Qt6 仕様)
    # QFile.ReadOnly や QFile.Text は PySide6 では属性エラーになります。
    # 正しくは QIODevice.OpenModeFlag からビットオアで結合します。
    if file.open(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Text):
        try:
            stream = QTextStream(file)
            
            # 🌟 修正ポイント3: QStringConverter を使ったエンコーディング指定 (Qt6 仕様)
            # QTextStream.Encoding.Utf8 は旧仕様（Qt5）です。
            # PySide6 では QStringConverter.Encoding.Utf8 を使用します。
            stream.setEncoding(QStringConverter.Encoding.Utf8) 
            qss = stream.readAll()
            
            # アプリケーション全体にスタイルシートを適用
            app.setStyleSheet(qss)
            # アイコン配色(dark/light)もテーマに連動させる
            from modules.gui.icons import set_icon_theme
            set_icon_theme("light" if "light" in os.path.basename(file_path) else "dark")
            return True
        except Exception as e:
            print(f"[Theme Error] Failed to read or apply QSS: {e}")
            return False
        finally:
            file.close()
    else:
        print(f"[Theme Error] Could not open file: {file_path}")
        return False
