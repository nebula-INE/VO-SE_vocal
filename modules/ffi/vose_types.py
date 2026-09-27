#vose_types.py
import ctypes
from typing import Iterable


class CNoteEvent(ctypes.Structure):
    """ABI-compatible NoteEvent definition from include/vose_core.h."""

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
        ("preutterance_ms", ctypes.c_double),
        ("overlap_ms", ctypes.c_double),
        ("timing_override", ctypes.c_int),
    ]

def as_c_double_array(values: Iterable[float]) -> ctypes.Array[ctypes.c_double]:
    """Python iterable を C の `double[]` に変換する。"""

    seq = tuple(float(v) for v in values)
    return (ctypes.c_double * len(seq))(*seq)

def validate_note_event_layout():
    """CNoteEvent のレイアウト検証。

    C++ 側の NoteEvent は 64bit 環境で 8-byte alignment され、
    現在の定義では 128 bytes になる。
    """

    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    if pointer_size == 8:
        expected = 128
        actual = ctypes.sizeof(CNoteEvent)
        if actual != expected:
            raise RuntimeError(
                f"CNoteEvent ABI mismatch: expected {expected} bytes, "
                f"got {actual} bytes"
            )



__all__ = ["CNoteEvent", "as_c_double_array", "validate_note_event_layout"]
