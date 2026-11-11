import ctypes
import logging
from types import SimpleNamespace

from modules.ffi.vose_api import _bind, bind_all


class _StubFunction:
    argtypes = None
    restype = None


def test_bind_all_silently_accepts_missing_legacy_process_voice(caplog):
    exported_symbols = (
        "load_embedded_resource",
        "set_oto_data",
        "execute_render",
        "execute_render_cancelable",
        "set_vocal_timeline",
        "get_engine_version",
        "clear_engine_cache",
        "init_official_engine",
        "set_bigvgan_model",
    )
    lib = SimpleNamespace(
        **{name: _StubFunction() for name in exported_symbols}
    )

    with caplog.at_level(logging.WARNING, logger="modules.ffi.vose_api"):
        bind_all(lib)

    assert "process_voice" not in caplog.text
    assert lib.execute_render.argtypes[1] is ctypes.c_int
    assert lib.execute_render.restype is None


def test_bind_warns_for_missing_symbols_by_default(caplog):
    lib = SimpleNamespace()

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
        "clear_engine_cache",
        "init_official_engine",
        "set_bigvgan_model",
        "process_voice",
    )
    lib = SimpleNamespace(
        **{name: _StubFunction() for name in exported_symbols}
    )

    bind_all(lib)

    assert lib.process_voice.argtypes == [
        ctypes.POINTER(ctypes.c_float),
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_float),
    ]
    assert lib.process_voice.restype is None

