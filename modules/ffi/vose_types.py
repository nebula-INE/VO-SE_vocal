#vose_types.py
import ctypes
from typing import Iterable


class CNoteEvent(ctypes.Structure):
    """`include/vose_core.h` の NoteEvent と ABI を一致させる。"""

    _fields_ = [
        ("wav_path", ctypes.c_char_p),
        ("pitch_curve", ctypes.POINTER(ctypes.c_double)),
        ("pitch_length", ctypes.c_int),
        ("gender_curve", ctypes.POINTER(ctypes.c_double)),
        ("tension_curve", ctypes.POINTER(ctypes.c_double)),
        ("breath_curve", ctypes.POINTER(ctypes.c_double)),
        ("vibrato_depth_curve", ctypes.POINTER(ctypes.c_double)),
        ("vibrato_rate_curve", ctypes.POINTER(ctypes.c_double)),
        ("vibrato_curve_length", ctypes.c_int),
        ("portamento_offsets", ctypes.POINTER(ctypes.c_double)),
        ("portamento_length", ctypes.c_int),
        ("intensity", ctypes.c_double),
        ("modulation", ctypes.c_double),
    ]


def as_c_double_array(values: Iterable[float]) -> ctypes.Array[ctypes.c_double]:
    """Python iterable を C の `double[]` に変換する。"""

    seq = tuple(float(v) for v in values)
    return (ctypes.c_double * len(seq))(*seq)

def validate_note_event_layout():
    """CNoteEvent のレイアウト検証。

    C++ 側の NoteEvent は 64bit 環境で pointer x 8 + int x 3 + double x 2 の
    8-byte alignment になるため 104 bytes になる。
    （2026-08-04 現在の vose_core.h の定義に基づく）
    """

    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    if pointer_size == 8:
        expected = 104
        actual = ctypes.sizeof(CNoteEvent)
        if actual != expected:
            raise RuntimeError(
                f"CNoteEvent ABI mismatch: expected {expected} bytes, "
                f"got {actual} bytes"
            )



__all__ = ["CNoteEvent", "as_c_double_array", "validate_note_event_layout"]
