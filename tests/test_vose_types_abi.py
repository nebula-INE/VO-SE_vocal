import ctypes

from modules.ffi.vose_types import CNoteEvent


def test_note_event_abi_includes_UTAU_timing_fields():
    assert hasattr(CNoteEvent, "preutterance_ms")
    assert hasattr(CNoteEvent, "overlap_ms")
    assert hasattr(CNoteEvent, "timing_override")
    assert ctypes.sizeof(CNoteEvent) == 128
