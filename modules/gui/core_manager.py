import ctypes
import os
import platform
from typing import Optional

from modules.ffi import CNoteEvent, validate_note_event_layout
from modules.ffi.vose_api import load_engine


class VoseCoreManager:
    """DLLロードと C 関数シグネチャ管理を行うシングルトン。"""

    _instance: Optional["VoseCoreManager"] = None
    lib: Optional[ctypes.CDLL] = None
    _initialized: bool
    _disabled_reason: Optional[str]

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(VoseCoreManager, cls).__new__(cls)
            cls._instance._initialized = False
            cls._instance._disabled_reason = None
        return cls._instance

    def _library_names(self) -> tuple[str, ...]:
        system = platform.system()
        if system == "Windows":
            return ("vose_core.dll",)
        if system == "Darwin":
            return ("libvose_core.dylib", "vose_core.dylib")
        return ("libvose_core.so", "vose_core.so")


    def _candidate_paths(self) -> list[str]:
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        base_dirs = [
            os.path.join(repo_root, "bin"),
            os.path.join(os.path.dirname(__file__), "..", "bin"),
            os.path.join(os.getcwd(), "bin"),
        ]
        return [os.path.join(base_dir, lib_name) for base_dir in base_dirs for lib_name in self._library_names()]

    def _init_engine(self):
        if self._initialized:
            return

        disable_native = os.getenv("VOSE_DISABLE_NATIVE_CORE", "").lower() in {"1", "true", "yes", "on"}
        if disable_native:
            self._disabled_reason = "VOSE_DISABLE_NATIVE_CORE is enabled"
            self._initialized = True
            self.lib = None
            print(f"⚠️ VOSE Core disabled: {self._disabled_reason}")
            return

        try:
            repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
            search_dirs = [
                os.path.join(repo_root, "bin"),
                os.path.join(os.path.dirname(__file__), "..", "bin"),
                os.path.join(os.getcwd(), "bin"),
            ]
            self.lib = load_engine(search_dirs=search_dirs)
            if self.lib is not None:
                self._load_bigvgan_model()
                print("[OK] VOSE Core Engine Loaded via unified FFI loader")
                self._initialized = True
                return
            raise OSError("Unified VOSE Core loader could not load the engine")
        except Exception as exc:
            self._disabled_reason = str(exc)
            self.lib = None

        # [FIX-1] DLL未検出時は理由を記録し、後から get_lib() で参照できるようにする
        reason = "DLL not found in any candidate path"
        if load_errors:
            reason = "DLL found but failed to load:\n" + "\n".join(load_errors)
        self._disabled_reason = reason
        print(f"[Warning] VOSE Core DLL not found. Engine is offline.\n  Reason: {reason}")
        self.lib = None
        self._initialized = True

    def _setup_prototypes(self) -> None:
        if not self.lib:
            return

        validate_note_event_layout()

        # [FIX-2] 各シンボルの存在確認を try/except で確実に行う
        try:
            self.lib.execute_render.argtypes = [
                ctypes.POINTER(CNoteEvent),
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
            ]
            self.lib.execute_render.restype = None
        except AttributeError:
            print("[Warning] execute_render not found in DLL — skipping prototype setup")

        try:
            self.lib.init_official_engine.argtypes = []
            self.lib.init_official_engine.restype = None
        except AttributeError:
            pass  # オプションのシンボルなので警告不要

        try:
            self.lib.synthesize_by_name.argtypes = [ctypes.c_char_p, ctypes.c_float]
            self.lib.synthesize_by_name.restype = ctypes.POINTER(ctypes.c_float)
        except AttributeError:
            pass  # オプションのシンボルなので警告不要

        try:
            self.lib.set_bigvgan_model.argtypes = [ctypes.c_char_p]
            self.lib.set_bigvgan_model.restype = None
        except AttributeError:
            pass  # 無印版ビルドにはシンボルが無いので警告不要

    def _load_bigvgan_model(self) -> None:
        """
        models/bigvgan/bigvgan_generator.onnx をC++コアへ登録する。
        無印版ビルド（VOSE_PRO未定義）では set_bigvgan_model 自体がシンボル未export
        の可能性があるため、存在チェックしてから呼ぶ。モデルが無い場合はWORLD直接出力
        にフォールバックする（C++側の g_bigvgan_session が未設定のまま）。
        """
        if not self.lib or not hasattr(self.lib, "set_bigvgan_model"):
            return

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        onnx_path = os.path.join(repo_root, "models", "bigvgan", "bigvgan_generator.onnx")

        if os.path.exists(onnx_path):
            try:
                self.lib.set_bigvgan_model(onnx_path.encode("utf-8"))
                print(f"[OK] BigVGAN model loaded: {onnx_path}")
            except Exception as e:
                print(f"[Warning] Failed to load BigVGAN model ({onnx_path}): {e}")
        else:
            print(f"[Info] BigVGAN model not found at {onnx_path}; using WORLD output directly.")

    def get_lib(self) -> Optional[ctypes.CDLL]:
        if not self._initialized:
            self._init_engine()

        # [FIX-3] None の理由をログに残し、呼び出し側のデバッグを助ける
        if self.lib is None:
            reason = self._disabled_reason or "unknown reason"
            print(f"[Warning] VOSE Core Engine is not available ({reason}).")

        return self.lib

    def is_available(self) -> bool:
        """エンジンが利用可能かどうかを返す。get_lib() の None チェックの代替。"""
        if not self._initialized:
            self._init_engine()
        return self.lib is not None

    def disabled_reason(self) -> Optional[str]:
        """エンジンが無効になっている理由を返す（デバッグ用）。"""
        return self._disabled_reason


vose_manager = VoseCoreManager()
__all__ = ["VoseCoreManager", "vose_manager", "CNoteEvent"]
