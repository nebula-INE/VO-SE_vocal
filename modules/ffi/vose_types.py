#vose_types.py
import ctypes
from typing import Iterable


class COtoEntry(ctypes.Structure):
    """C++ struct OtoEntry from include/vose_core.h."""

    _fields_ = [
        ("filename", ctypes.c_char_p),
        ("cutoff", ctypes.c_double),
        ("alias", ctypes.c_char * 64),
        ("wav_path", ctypes.c_char * 512),
        ("offset", ctypes.c_double),
        ("consonant", ctypes.c_double),
        ("blank", ctypes.c_double),
        ("preutterance", ctypes.c_double),
        ("overlap", ctypes.c_double),
    ]


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
        ("start_time_ms", ctypes.c_double),
        ("preutterance_ms", ctypes.c_double),
        ("overlap_ms", ctypes.c_double),
    ]


def as_c_double_array(values: Iterable[float]) -> ctypes.Array[ctypes.c_double]:
    """Python iterable を C の `double[]` に変換する。"""

    seq = tuple(float(v) for v in values)
    return (ctypes.c_double * len(seq))(*seq)

def validate_note_event_layout() -> None:
    """Validate the 64-bit NoteEvent layout shared with the native renderer."""
    pointer_size = ctypes.sizeof(ctypes.c_void_p)
    if pointer_size != 8:
        raise RuntimeError(
            "VO-SE desktop rendering requires a 64-bit process; "
            f"pointer size is {pointer_size}"
        )

    expected_size = 128
    actual_size = ctypes.sizeof(CNoteEvent)
    if actual_size != expected_size:
        raise RuntimeError(
            f"CNoteEvent ABI mismatch: expected {expected_size} bytes, "
            f"got {actual_size} bytes"
        )

    expected_offsets = {
        "start_time_ms": 104,
        "preutterance_ms": 112,
        "overlap_ms": 120,
    }
    actual_offsets = {
        field_name: getattr(CNoteEvent, field_name).offset
        for field_name in expected_offsets
    }
    if actual_offsets != expected_offsets:
        raise RuntimeError(
            "CNoteEvent ABI field-offset mismatch: "
            f"expected {expected_offsets}, got {actual_offsets}"
        )


def note_event_abi_signature() -> int:
    """Return the exact size/offset fingerprint expected from the loaded C++ core."""
    validate_note_event_layout()
    return (
        (ctypes.sizeof(CNoteEvent) << 48)
        | (CNoteEvent.start_time_ms.offset << 32)
        | (CNoteEvent.preutterance_ms.offset << 16)
        | CNoteEvent.overlap_ms.offset
    )



def validate_oto_entry_layout():
    """Validate the native OtoEntry ABI on 64-bit hosts."""
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        expected = 632
        actual = ctypes.sizeof(COtoEntry)
        if actual != expected:
            raise RuntimeError(
                f"COtoEntry ABI mismatch: expected {expected} bytes, got {actual} bytes"
            )


__all__ = [
    "COtoEntry",
    "CNoteEvent",
    "as_c_double_array",
    "validate_note_event_layout",
    "validate_oto_entry_layout",
]
