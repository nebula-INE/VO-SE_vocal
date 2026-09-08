# main.py

import sys
import os
import platform
import ctypes
import ctypes.util
import json
import threading

import importlib
from importlib.util import find_spec

try:
    from modules.utils.pyside_stub import setup_pyside_stub
    setup_pyside_stub()
except Exception:
    pass

from typing import Any

if sys.platform.startswith("win"):
    try:
        # 静的解析を回避するため一旦 Any にキャストしてから hasattr で確認して呼ぶ
        _out: Any = sys.stdout
        _err: Any = sys.stderr
        if hasattr(_out, "reconfigure"):
            _out.reconfigure(encoding="utf-8")
        if hasattr(_err, "reconfigure"):
            _err.reconfigure(encoding="utf-8")
    except Exception:
        # reconfigure が無い環境や埋め込み環境向けのフォールバック
        import os as _os
        _os.environ.setdefault("PYTHONUTF8", "1")

def global_exception_handler(exctype, value, tb):
    """
    アプリ全体の未キャッチ例外をすべて捕捉し、無言クラッシュを防ぐグローバルハンドラー。
    """
    import traceback
    error_message = "".join(traceback.format_exception(exctype, value, tb))
    print(f"[Fatal Crash] {error_message}", file=sys.stderr)
    
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
        if QApplication.instance():
            QMessageBox.critical(
                None,
                "致命的なエラー (VO-SE Pro)",
                f"アプリケーション内で予期しないエラーが発生しました。\n"
                f"開発者にこのエラーを報告してください。\n\n"
                f"【エラー内容】\n{value}\n\n"
                f"※詳細はコンソールログ、またはログファイルを確認してください。"
            )
    except Exception as e:
        print(f"Failed to show crash dialog: {e}", file=sys.stderr)
        
    sys.exit(1)

sys.excepthook = global_exception_handler


# --- [1] リソースパス解決関数 (PyInstaller対応) ---
def get_resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def get_engine_library_path():
    system = platform.system()
    if system == "Windows":
        lib_names = ("vose_core.dll",)
    elif system == "Darwin":
        lib_names = ("libvose_core.dylib", "vose_core.dylib")
    else:
        lib_names = ("libvose_core.so", "vose_core.so")

    for lib_name in lib_names:
        dll_path = get_resource_path(os.path.join("bin", lib_name))
        if os.path.exists(dll_path):
            return dll_path

    return get_resource_path(os.path.join("bin", lib_names[0]))


# --- [2] 設定管理クラス (ConfigHandler) ---
# [FIX] temp/ ではなく OS 標準のユーザーデータディレクトリを使用する。
# PyInstaller バンドル後も書き込み権限が確保され、バージョンアップでも消えない。
def _get_user_config_path() -> str:
    """OS ごとの標準設定ディレクトリに config.json のパスを返す。"""
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(base, "VO-SE Pro", "config.json")
    elif system == "Darwin":
        return os.path.expanduser("~/Library/Application Support/VO-SE Pro/config.json")
    else:  # Linux / その他
        xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
        return os.path.join(xdg, "vo-se-pro", "config.json")


class ConfigHandler:
    def __init__(self, config_path: str | None = None):
        # config_path が明示的に渡されなければ OS 標準パスを使う
        self.config_path = config_path if config_path else _get_user_config_path()
        self.default_config = {
            "last_save_dir": os.path.expanduser("~"),
            "default_voice": "mei_normal",
            "volume": 0.8
        }

    def load_config(self):
        if not os.path.exists(self.config_path):
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            self.save_config(self.default_config)
            return self.default_config
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return self.default_config

    def save_config(self, config_dict):
        try:
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(config_dict, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Config save error: {e}")


# --- [3] エンジンクラス (VoSeEngine) ---
# [FIX-SAMPLE-RATE] C++ コア (kFs_internal = 44100) に合わせて固定。
_INTERNAL_SAMPLE_RATE = 44100

class VoSeEngine:
    def __init__(self):
        self.os_name = platform.system()
        self.c_engine = None
        # [FIX-LOCK] _lock を __init__ で確実に生成しレースコンディションを根絶する
        self._lock = threading.Lock()
        self._load_c_engine()

    def _load_c_engine(self):
        """
        OSに応じたライブラリ（DLL/dylib）を最適なパスからロードします。
        Windows では依存DLLのパス問題を解決するため、add_dll_directory と winmode を使用します。
        """
        dll_path = get_engine_library_path()

        # --- macOS: バンドル内の代替パスをチェック ---
        if self.os_name == "Darwin":
            if not os.path.exists(dll_path):
                meipass = getattr(sys, '_MEIPASS', None)
                if meipass:
                    bundle_dir = os.path.dirname(os.path.dirname(meipass))
                    alt_path = os.path.join(bundle_dir, "Frameworks", "bin", os.path.basename(dll_path))
                    if os.path.exists(alt_path):
                        dll_path = alt_path
                        print(f"[Info] Mac Frameworks path used: {dll_path}")

        # --- ファイル存在チェック ---
        if not os.path.exists(dll_path):
            print(f"[Warning] C-Engine file not found at: {dll_path}")
            self.c_engine = None
            return

        abs_dll_path = os.path.abspath(dll_path)
        dll_dir = os.path.dirname(abs_dll_path)

        try:
            # --- Windows 固有のDLLロード処理 ---
            if self.os_name == "Windows":
                # 1. 依存DLL（MSVCランタイムなど）の検索パスにDLLディレクトリを追加
                if hasattr(os, "add_dll_directory"):
                    try:
                        # getattr を使って静的解析のエラーを回避
                        getattr(os, "add_dll_directory")(dll_dir)
                        print(f"[Info] Added DLL directory: {dll_dir}")
                    except Exception as e:
                        print(f"[Warning] add_dll_directory failed: {e}")

                # 2. WinDLL を使用し、LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR フラグ (0x0008) を指定
                #    これにより、DLLのあるディレクトリが優先的に検索される
                if hasattr(ctypes, "WinDLL"):
                    try:
                        # getattr を使って静的解析のエラーを回避
                        self.c_engine = getattr(ctypes, "WinDLL")(abs_dll_path, winmode=0x0008)
                    except Exception as e:
                        print(f"[Warning] WinDLL with winmode failed, falling back to CDLL: {e}")
                        self.c_engine = ctypes.CDLL(abs_dll_path)
                else:
                    # 古い環境 / 非Windows でのフォールバック（実際にはここには来ない）
                    self.c_engine = ctypes.CDLL(abs_dll_path)
            else:
                # macOS / Linux: RTLD_GLOBAL (mode=10) でロード
                self.c_engine = ctypes.CDLL(abs_dll_path, mode=10)  # RTLD_GLOBAL

            # --- 関数シグネチャの設定（process_voice が存在する場合のみ） ---
            if hasattr(self.c_engine, 'process_voice'):
                self.c_engine.process_voice.argtypes = [
                    ctypes.POINTER(ctypes.c_float),
                    ctypes.c_int,
                    ctypes.POINTER(ctypes.c_float)
                ]
                self.c_engine.process_voice.restype = None
                print(f"[Success] C-Engine loaded: {abs_dll_path}")
            else:
                # process_voice が無い場合は非対応エンジンとして扱うが、一応ロードは成功とみなす
                print(f"[Warning] C-Engine loaded but 'process_voice' not found: {abs_dll_path}")
                # 必要に応じてここで self.c_engine = None にしても良いが、他の関数が使える可能性もあるので残す

        except OSError as e:
            # OSError（例：依存DLL不足）は特に詳細に表示
            print(f"[Error] Failed to load C-Engine (OSError): {e}")
            if self.os_name == "Windows":
                print("[Hint] Microsoft Visual C++ Redistributable がインストールされているか確認してください。")
            import traceback
            traceback.print_exc()
            self.c_engine = None
        except Exception as e:
            print(f"[Error] Failed to load C-Engine: {e}")
            import traceback
            traceback.print_exc()
            self.c_engine = None
            
    def analyze_intonation(self, text):
        """【読み上げ用】音韻解析"""
        print(f"\n--- 読み上げ解析実行: '{text}' ---")
        try:
            pyopenjtalk = importlib.import_module("pyopenjtalk")
            labels = pyopenjtalk.extract_fullcontext(text)
            return labels
        except Exception as e:
            return [f"Analysis failed: {str(e)}"]

    def analyze_singing_pitch(self, notes, frame_period_ms=5.0):
        """
        【歌唱用・WORLDエンジン連携】ノート列からF0カーブ（フレーム単位の周波数配列）を生成する。

        [FIX-SAMPLE-RATE] sample_rate 引数を削除し、C++ コアと同じ kFs_internal=44100 に固定。
        以前は Python 側がデフォルト 48000 Hz を使っており、C++ 側の 44100 Hz と
        フレーム計算がずれてピッチエラーが生じていた。
        """
        print("--- WORLD歌唱ピッチ解析実行 ---")
        try:
            import sys as _sys
            if "numpy" in _sys.modules:
                np = _sys.modules["numpy"]
            else:
                import importlib as _il
                np = _il.import_module("numpy")

            if not notes:
                return np.zeros(1, dtype=np.float32)

            frame_sec = max(frame_period_ms / 1000.0, 1e-4)
            min_hz = 20.0
            max_hz = 5000.0

            def _read_note_value(note_obj, key, default=None):
                if isinstance(note_obj, dict):
                    return note_obj.get(key, default)
                return getattr(note_obj, key, default)

            hz_segments = []
            for note in notes:
                if note is None:
                    continue
                
                try:
                    duration = float(_read_note_value(note, "duration", 0.0) or 0.0)
                except (TypeError, ValueError):
                    duration = 0.0

                if duration <= 0:
                    continue

                midi_note = _read_note_value(
                    note,
                    "note_number",
                    _read_note_value(note, "pitch", _read_note_value(note, "note", 69))
                )
                try:
                    midi_value = float(midi_note)
                except (TypeError, ValueError):
                    midi_value = 69.0

                hz = 440.0 * (2.0 ** ((midi_value - 69.0) / 12.0))
                hz = min(max(hz, min_hz), max_hz)

                frame_count = max(1, int(round(duration / frame_sec)))
                hz_segments.append(np.full(frame_count, hz, dtype=np.float32))

            if not hz_segments:
                return np.zeros(1, dtype=np.float32)

            f0_curve = np.concatenate(hz_segments)

            smooth_window = max(1, int(round(0.03 / frame_sec)))  # 約30ms
            if smooth_window > 1 and len(f0_curve) > smooth_window:
                kernel = np.hanning(smooth_window)
                kernel_sum = float(kernel.sum())
                if kernel_sum > 0:
                    kernel /= kernel_sum
                    f0_curve = np.convolve(f0_curve, kernel, mode='same').astype(np.float32)
                    
            return f0_curve

        except Exception as e:
            print(f"[Error] analyze_singing_pitch failed: {e}")
            try:
                import sys as _sys
                if "numpy" in _sys.modules:
                    np = _sys.modules["numpy"]
                else:
                    import numpy as np
                # [FIX-NONE-RETURN] 最悪ケースでも None ではなくゼロ配列を返す。
                # None を受け取った process_with_c が Segfault を起こすのを防ぐ。
                return np.zeros(1, dtype=np.float32)
            except Exception:
                # numpy すら壊れている場合は空リストで代替（C++ 側が長さ 0 を検知して skip）
                return []

    def process_with_c(self, data_array, f0_array=None):
        """
        【共通処理】波形データとピッチデータ（WORLD F0カーブなど）をC++エンジンに送り込みます。
        """
        if not self.c_engine or not hasattr(self.c_engine, 'process_voice'):
            print("[Warning] C-Engine not available, skipping processing")
            return data_array

        # [FIX-LOCK] _lock は __init__ で生成済みなので hasattr チェック不要
        try:
            import sys as _sys
            if "numpy" in _sys.modules:
                np = _sys.modules["numpy"]
            else:
                import importlib as _il
                np = _il.import_module("numpy")

            with self._lock:
                wav_float = np.ascontiguousarray(data_array, dtype=np.float32)
                wav_ptr = wav_float.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
                length = len(wav_float)

                f0_float = None
                f0_ptr = None
                if f0_array is not None and len(f0_array) > 0:
                    f0_float = np.ascontiguousarray(f0_array, dtype=np.float32)
                    f0_ptr = f0_float.ctypes.data_as(ctypes.POINTER(ctypes.c_float))

                self.c_engine.process_voice(wav_ptr, length, f0_ptr)

            return wav_float

        except Exception as e:
            print(f"C-Process error: {e}")
            return data_array


PYTHON_RUNTIME_PACKAGES = (
    "numpy",
    "pyopenjtalk",
    "PySide6",
    "sounddevice",
    "soundfile",
)

OS_RUNTIME_LIBRARIES = {
    "Linux": (
        ("libGL.so.1", "GL", "libgl1"),
        ("libEGL.so.1", "EGL", "libegl1"),
        ("libxkbcommon.so.0", "xkbcommon", "libxkbcommon0"),
        ("libxkbcommon-x11.so.0", "xkbcommon-x11", "libxkbcommon-x11-0"),
        ("libdbus-1.so.3", "dbus-1", "libdbus-1-3"),
        ("libxcb-cursor.so.0", "xcb-cursor", "libxcb-cursor0"),
        ("libXrender.so.1", "Xrender", "libxrender1"),
        ("libXi.so.6", "Xi", "libxi6"),
        ("libSM.so.6", "SM", "libsm6"),
        ("libXext.so.6", "Xext", "libxext6"),
        ("libfontconfig.so.1", "fontconfig", "libfontconfig1"),
        ("libpulse.so.0", "pulse", "libpulse0"),
        ("libasound.so.2", "asound", "libasound2t64/libasound2"),
        ("libsndfile.so.1", "sndfile", "libsndfile1"),
        ("libportaudio.so.2", "portaudio", "portaudio19-dev"),
    ),
    "Darwin": (
        ("libportaudio.dylib", "portaudio", "portaudio"),
        ("libsndfile.dylib", "sndfile", "libsndfile"),
    ),
}

OS_DEPENDENCY_INSTALL_HINTS = {
    "Linux": (
        "Ubuntu/Debian: sudo apt-get install -y "
        "libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libdbus-1-3 "
        "libxcb-cursor0 libxrender1 libxi6 libsm6 libxext6 libfontconfig1 "
        "libpulse0 libasound2t64 libsndfile1 portaudio19-dev"
    ),
    "Darwin": "macOS: brew install portaudio libsndfile",
}


def _is_os_library_loadable(library_lookup_name):
    library_path = ctypes.util.find_library(library_lookup_name)
    if library_path is None:
        return False
    try:
        ctypes.CDLL(library_path)
    except OSError:
        return False
    return True


def _check_runtime_requirements():
    """起動前に実行環境をチェックし、足りない要件をユーザーへ明示する。"""
    missing = []

    for module_name in PYTHON_RUNTIME_PACKAGES:
        if find_spec(module_name) is None:
            missing.append(f"Python package: {module_name}")

    system_name = platform.system()
    check_os_libraries = not (getattr(sys, "frozen", False) and system_name == "Darwin")
    if check_os_libraries:
        for display_name, lookup_name, package_name in OS_RUNTIME_LIBRARIES.get(system_name, ()):
            if not _is_os_library_loadable(lookup_name):
                missing.append(f"OS library: {display_name} ({package_name})")

    return missing


# --- [4] メイン実行処理 ---
def main():
    missing = _check_runtime_requirements()
    is_smoke_test = os.environ.get("VOSE_STARTUP_SMOKE_TEST") == "1"
    if missing:
        print("[Fatal] 起動に必要な依存関係が不足しています。")
        for item in missing:
            print(f"  - {item}")
        if is_smoke_test:
            print("[SmokeTest] 依存関係情報出力完了 (Smoke Test OK).")
            return 0
        print("requirements.txt と OS 依存ライブラリをインストールして再実行してください。")
        install_hint = OS_DEPENDENCY_INSTALL_HINTS.get(platform.system())
        if install_hint:
            print(f"例: {install_hint}")
        sys.exit(1)

    if platform.system() == "Linux":
        if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
            print("[Info] Linux headless mode detected. QT_QPA_PLATFORM=offscreen を使用します。")

    try:
        QtWidgets = importlib.import_module("PySide6.QtWidgets")
        QtGui = importlib.import_module("PySide6.QtGui")
        QtCore = importlib.import_module("PySide6.QtCore")
    except Exception as e:
        print(f"[Fatal] GUI モジュールの読み込みに失敗しました: {e}")
        sys.exit(1)

    try:
        MainWindow = importlib.import_module("modules.gui.main_window").MainWindow
    except Exception as e:
        print(f"[Fatal] メインウィンドウの読み込みに失敗しました: {e}")
        if hasattr(sys, "stderr"):
            import traceback
            traceback.print_exc()
        sys.exit(1)

    QApplication = QtWidgets.QApplication
    QMessageBox = QtWidgets.QMessageBox
    QIcon = QtGui.QIcon
    QTimer = QtCore.QTimer
    Qt = QtCore.Qt

    app = QApplication(sys.argv)
    app.setApplicationName("VO-SE Pro")

    for icon_rel in ("assets/icon.png", "assets/icon.icns", "assets/icon.ico"):
        icon_path = get_resource_path(icon_rel)
        if os.path.exists(icon_path):
            app.setWindowIcon(QIcon(icon_path))
            break

    dll_path = get_engine_library_path()
    if not os.path.exists(dll_path):
        QMessageBox.warning(
            None,
            "コアエンジン未検出",
            f"VO-SE Core Engine が見つかりません。\n"
            f"音声合成・歌声合成機能は利用できません。\n\n"
            f"期待されるパス:\n{dll_path}"
        )

    config_handler = ConfigHandler()
    config = config_handler.load_config()
    engine = VoSeEngine()

    try:
        window = MainWindow()
        # app_main.py の MainWindow 生成直後
        from modules.gui.main_window_patch import patch_main_window
        patch_main_window(window)
    except Exception as e:
        QMessageBox.critical(None, "起動エラー", f"メイン画面の初期化でエラーが発生しました。\n{e}")
        if hasattr(sys, "stderr"):
            import traceback
            traceback.print_exc()
        sys.exit(1)

    window.vo_se_engine = engine
    window.config = config

    if engine.c_engine:
        window.statusBar().showMessage("VO-SE Core Engine: Ready")
    else:
        window.statusBar().showMessage("VO-SE Core Engine: Not Found (Offline Mode)")

    if os.environ.get("VOSE_STARTUP_SMOKE_TEST") == "1":
        print("[SmokeTest] VO-SE Pro initialized successfully.")
        config_handler.save_config(config)
        app.quit()
        return 0

    def show_main_window():
        if window.isMinimized():
            window.showNormal()
        else:
            window.show()
        window.raise_()
        window.activateWindow()
        window_handle = window.windowHandle()
        if window_handle is not None:
            window_handle.requestActivate()

    def release_startup_frontmost_hint():
        window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, False)
        show_main_window()

    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    show_main_window()
    QTimer.singleShot(0, show_main_window)
    QTimer.singleShot(300, show_main_window)
    QTimer.singleShot(1200, release_startup_frontmost_hint)

    result = app.exec()
    config_handler.save_config(config)
    return result


if __name__ == "__main__":
    sys.exit(main())
