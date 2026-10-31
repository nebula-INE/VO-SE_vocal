import os
import sys

from PySide6.QtCore import QFile, QTextStream, QIODevice, QStringConverter
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

_MODULE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _search_roots() -> list:
    roots = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(meipass)
    roots.append(_MODULE_ROOT)
    roots.append(os.getcwd())
    exe_dir = os.path.dirname(os.path.abspath(sys.argv[0])) if sys.argv and sys.argv[0] else ""
    if exe_dir:
        roots.append(exe_dir)
    return list(dict.fromkeys(roots))


def _find_qss(theme_name: str):
    for root in _search_roots():
        for rel in (("assets", "themes"), ("themes",)):
            candidate = os.path.join(root, *rel, f"{theme_name}.qss")
            if os.path.isfile(candidate):
                return candidate
    return None


THEME_FILES = {name: _find_qss(name) or "" for name in ("dark", "light")}


def _apply_fallback_palette(app: QApplication, theme_name: str) -> None:
    app.setStyle("Fusion")
    if theme_name == "light":
        app.setPalette(app.style().standardPalette())
        return

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1e1e21"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#e6e6ea"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#16171b"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#1e1e21"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e6e6ea"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#2a2a2e"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e6e6ea"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#2a2a2e"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#e6e6ea"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#0a84ff"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    app.setPalette(palette)


def apply_theme(theme_name: str) -> bool:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        print("[Theme Error] QApplication instance does not exist or is not fully initialized.")
        return False

    if theme_name not in ("dark", "light"):
        print(f"[Theme Warning] Theme '{theme_name}' not found. Falling back to 'dark'.")
        theme_name = "dark"

    try:
        from modules.gui.icons import set_icon_theme
        set_icon_theme(theme_name)
    except Exception as exc:
        print(f"[Theme Warning] icon theme switch failed: {exc}")

    qss = ""
    file_path = _find_qss(theme_name)
    if file_path is None:
        print(f"[Theme Error] {theme_name}.qss not found. searched: {_search_roots()}")
    else:
        file = QFile(file_path)
        if file.open(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Text):
            try:
                stream = QTextStream(file)
                stream.setEncoding(QStringConverter.Encoding.Utf8)
                qss = stream.readAll()
            except Exception as exc:
                print(f"[Theme Error] Failed to read QSS: {exc}")
            finally:
                file.close()
        else:
            print(f"[Theme Error] Could not open file: {file_path}")

    if qss:
        app.setStyle("Fusion")
        app.setStyleSheet(qss)
    else:
        app.setStyleSheet("")
        _apply_fallback_palette(app, theme_name)
    return True
