import ctypes
import logging
from types import SimpleNamespace
from typing import Any, cast

from modules.ffi.vose_api import _bind, bind_all
from modules.ffi.vose_types import (
    COtoEntry,
    CNoteEvent,
    note_event_abi_signature,
    oto_entry_abi_signature,
)


class _StubFunction:
    argtypes: Any = None
    restype: Any = None


def _stub_library(**symbols: _StubFunction) -> Any:
    # bind_all/_bind need only attribute access and mutable argtypes/restype.
    return cast(Any, SimpleNamespace(**symbols))


def test_bind_all_silently_accepts_missing_legacy_process_voice(caplog):
    exported_symbols = (
        "load_embedded_resource",
        "set_oto_data",
        "execute_render",
        "execute_render_cancelable",
        "set_vocal_timeline",
        "get_engine_version",
        "get_note_event_abi",
        "get_oto_entry_abi",
        "clear_engine_cache",
        "init_official_engine",
        "set_bigvgan_model",
    )
    lib = _stub_library(**{name: _StubFunction() for name in exported_symbols})

    with caplog.at_level(logging.WARNING, logger="modules.ffi.vose_api"):
        bind_all(lib)

    assert "process_voice" not in caplog.text
    assert lib.execute_render.argtypes[1] is ctypes.c_int
    assert lib.execute_render.restype is None
    assert lib.get_note_event_abi.argtypes == []
    assert lib.get_note_event_abi.restype is ctypes.c_uint64
    assert lib.get_oto_entry_abi.argtypes == []
    assert lib.get_oto_entry_abi.restype is ctypes.c_uint64


def test_bind_warns_for_missing_symbols_by_default(caplog):
    lib = _stub_library()

    with caplog.at_level(logging.WARNING, logger="modules.ffi.vose_api"):
        _bind(lib, "required_symbol", [], None)

    assert "required_symbol" in caplog.text


def test_bind_all_preserves_legacy_process_voice_signature_when_exported():
    exported_symbols = (
        "load_embedded_resource",
        "set_oto_data",
        "execute_render",
        "execute_render_cancelable",
        "set_vocal_timeline",
        "get_engine_version",
        "get_note_event_abi",
        "get_oto_entry_abi",
        "clear_engine_cache",
        "init_official_engine",
        "set_bigvgan_model",
        "process_voice",
    )
    lib = _stub_library(**{name: _StubFunction() for name in exported_symbols})

    bind_all(lib)

    assert lib.process_voice.argtypes == [
        ctypes.POINTER(ctypes.c_float),
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_float),
    ]
    assert lib.process_voice.restype is None



def test_python_note_event_abi_fingerprint_matches_native_contract():
    assert ctypes.sizeof(CNoteEvent) == 128
    assert CNoteEvent.start_time_ms.offset == 104
    assert CNoteEvent.preutterance_ms.offset == 112
    assert CNoteEvent.overlap_ms.offset == 120
    assert note_event_abi_signature() == (
        (128 << 48) | (104 << 32) | (112 << 16) | 120
    )



def test_python_oto_entry_abi_fingerprint_matches_native_contract():
    assert ctypes.sizeof(COtoEntry) == 632
    assert COtoEntry.wav_path.offset == 80
    assert COtoEntry.offset.offset == 592
    assert COtoEntry.overlap.offset == 624
    assert oto_entry_abi_signature() == (
        (632 << 48) | (80 << 32) | (592 << 16) | 624
    )
