# modules/ffi/vose_api.py
# ============================================================
# VO-SE Core DLL の関数シグネチャを一元管理するモジュール。
#
# ✅ 修正前の問題:
#   argtypes / restype の設定が以下の3箇所に散在していた
#     1. main.py VoSeEngine._load_c_engine()
#        → process_voice のみ定義。execute_render は未定義のまま使用
#     2. modules/audio/vo_se_engine.py VO_SE_Engine._load_core_library()
#        → execute_render / set_vocal_timeline を定義
#     3. modules/talk/talk_manager.py VoseRendererBridge.__init__()
#        → init_official_engine / execute_render を再定義
#
#   結果: 同じ関数に異なるシグネチャが混在し、どれが正とも分からない状態。
#   C++ 側 vose_core.h の定義を唯一の正として、ここで集中管理する。
# ============================================================

import ctypes
import os
import platform
import sys
import logging
from typing import Optional

logger = logging.getLogger(__name__)


# ============================================================
# C 互換構造体（vose_core.h の定義に厳密に準拠）
# ============================================================

class CNoteEvent(ctypes.Structure):
    """
    C++ struct NoteEvent の Python ミラー。
    vose_core.h の #pragma pack(pop) 後の定義と _fields_ の順序・型を一致させること。
    """
    _fields_ = [
        ("wav_path",             ctypes.c_char_p),
        ("pitch_curve",          ctypes.POINTER(ctypes.c_double)),
        ("pitch_length",         ctypes.c_int),
        ("gender_curve",         ctypes.POINTER(ctypes.c_double)),
        ("tension_curve",        ctypes.POINTER(ctypes.c_double)),
        ("breath_curve",         ctypes.POINTER(ctypes.c_double)),
        ("vibrato_depth_curve",  ctypes.POINTER(ctypes.c_double)),
        ("vibrato_rate_curve",   ctypes.POINTER(ctypes.c_double)),
        ("vibrato_curve_length", ctypes.c_int),
        ("portamento_offsets", ctypes.POINTER(ctypes.c_double)),
        ("portamento_length", ctypes.c_int),
    ]


class CVoseFrame(ctypes.Structure):
    """C++ struct VoseFrame（8バイトアライメント）"""
    _pack_ = 8
    _fields_ = [
        ("time",    ctypes.c_double),
        ("phoneme", ctypes.c_char * 8),
        ("weight",  ctypes.c_double),
    ]


# ============================================================
# シグネチャバインド（唯一の正）
# ============================================================

def bind_all(lib: ctypes.CDLL) -> None:
    """
    lib に対して vose_core.h に定義された全エクスポート関数の
    argtypes / restype を設定する。
    関数が存在しない場合は警告のみ出して続行する（バージョン差異への耐性）。
    """
    _bind(lib, "load_embedded_resource", [
        ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_int16),
        ctypes.c_int,
    ], None)

    _bind(lib, "execute_render", [
        ctypes.POINTER(CNoteEvent),
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    ], None)

    _bind(lib, "set_vocal_timeline", [
        ctypes.POINTER(CVoseFrame),
        ctypes.c_int,
    ], None)

    _bind(lib, "get_engine_version", [], ctypes.c_float)
    _bind(lib, "clear_engine_cache", [], None)
    _bind(lib, "init_official_engine", [], None)

    # BigVGAN ボコーダー（Pro版のみ有効）。onnx_path が None なら無効化。
    _bind(lib, "set_bigvgan_model", [ctypes.c_char_p], None)

    # main.py VoSeEngine が呼ぶ process_voice（旧 API、互換性のため残す）
    _bind(lib, "process_voice", [
        ctypes.POINTER(ctypes.c_float),
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_float),
    ], None)


def _bind(lib: ctypes.CDLL, name: str, argtypes: list, restype) -> None:
    if hasattr(lib, name):
        fn = getattr(lib, name)
        fn.argtypes = argtypes
        fn.restype  = restype
    else:
        logger.warning("[vose_api] DLL にシンボル '%s' が見つかりません（バージョン差異の可能性）", name)


# ============================================================
# DLL ローダー（OS 判別 + パス解決を一元化）
# ============================================================

def load_engine(search_dirs: Optional[list] = None) -> Optional[ctypes.CDLL]:
    """
    DLL/dylib/so を探してロードし、bind_all() を適用して返す。
    ロード失敗時は None を返す（呼び出し側は None チェックすること）。

    Args:
        search_dirs: 追加の探索ディレクトリリスト。
                     None の場合は [bin/, 実行ファイルと同じ dir] を探す。
    """
    system = platform.system()
    if system == "Windows":
        lib_names = ("vose_core.dll",)
    elif system == "Darwin":
        lib_names = ("libvose_core.dylib", "vose_core.dylib")
    else:
        lib_names = ("libvose_core.so", "vose_core.so")

    base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    candidates = search_dirs or []
    candidates += [
        os.path.join(base, "bin"),
        os.path.join(os.getcwd(), "bin"),
        base,
        os.getcwd(),
    ]

    for directory in candidates:
        for lib_name in lib_names:
            path = os.path.join(directory, lib_name)
            if not os.path.exists(path):
                continue
            try:
                abs_path = os.path.abspath(path)

                # Windows: 依存 DLL の探索パスを追加
                if system == "Windows" and hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(directory)  # type: ignore[attr-defined]

                if system == "Darwin":
                    lib = ctypes.CDLL(abs_path, mode=ctypes.RTLD_GLOBAL)
                else:
                    lib = ctypes.CDLL(abs_path)

                bind_all(lib)
                logger.info("[vose_api] Engine loaded: %s", abs_path)
                return lib

            except OSError as e:
                logger.error("[vose_api] OSError loading %s: %s", path, e)
                if system == "Windows":
                    logger.error("Hint: MSVC Redistributable がインストールされているか確認してください。")
            except Exception as e:
                logger.error("[vose_api] Failed to load %s: %s", path, e)

    logger.warning("[vose_api] DLL が見つかりませんでした（探索パス: %s）", candidates)
    return None
