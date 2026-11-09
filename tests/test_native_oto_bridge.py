import ctypes
from typing import Any, cast

from modules.audio.vo_se_engine import VO_SE_Engine
from modules.data.oto_parser import OtoEntry
from modules.ffi.vose_types import COtoEntry, validate_oto_entry_layout


class _FakeLib:
    def __init__(self):
        self.calls = []

    def set_oto_data(self, entries, count):
        self.calls.append((entries, count))


def test_oto_entry_abi_layout():
    validate_oto_entry_layout()
    assert ctypes.sizeof(COtoEntry) == 632


def test_set_oto_data_skips_alias_that_exceeds_native_abi_limit():
    engine = VO_SE_Engine.__new__(VO_SE_Engine)
    fake_lib = _FakeLib()
    engine.lib = cast(Any, fake_lib)

    entry = OtoEntry(
        alias="a" * 64,
        filename="a.wav",
        voice_dir="/tmp/voice",
    )
    engine.set_oto_data({"long-alias": entry})

    assert len(fake_lib.calls) == 1
    entries, count = fake_lib.calls[0]
    assert entries is None
    assert count == 0


def test_set_oto_data_skips_wav_path_that_exceeds_native_abi_limit():
    engine = VO_SE_Engine.__new__(VO_SE_Engine)
    fake_lib = _FakeLib()
    engine.lib = cast(Any, fake_lib)

    entry = OtoEntry(
        alias="あ",
        filename="a.wav",
        voice_dir="/tmp/" + ("v" * 520),
    )
    engine.set_oto_data({"あ": entry})

    assert len(fake_lib.calls) == 1
    entries, count = fake_lib.calls[0]
    assert entries is None
    assert count == 0


def test_set_oto_data_compacts_valid_entries_after_skipping_overlong_one():
    engine = VO_SE_Engine.__new__(VO_SE_Engine)
    fake_lib = _FakeLib()
    engine.lib = cast(Any, fake_lib)

    too_long = OtoEntry(
        alias="x" * 64,
        filename="a.wav",
        voice_dir="/tmp/voice",
    )
    valid = OtoEntry(
        alias="あ",
        filename="a.wav",
        voice_dir="/tmp/voice",
    )
    engine.set_oto_data({"too-long": too_long, "valid": valid})

    assert len(fake_lib.calls) == 1
    entries, count = fake_lib.calls[0]
    assert count == 1
    assert entries[0].alias == "あ".encode("utf-8")
    assert entries[0].wav_path == b"/tmp/voice/a.wav"


def test_set_oto_data_syncs_alias_and_wav_path():
    engine = VO_SE_Engine.__new__(VO_SE_Engine)
    fake_lib = _FakeLib()
    engine.lib = cast(Any, fake_lib)

    entry = OtoEntry(
        alias="a い",
        filename="a.wav",
        voice_dir="/tmp/voice",
        left_blank=10.0,
        fixed_range=20.0,
        right_blank=-30.0,
        preutterance=40.0,
        overlap=5.0,
    )

    engine.set_oto_data({"a い": entry})

    assert len(fake_lib.calls) == 1
    entries, count = fake_lib.calls[0]
    assert count == 1
    assert entries[0].alias == "a い".encode("utf-8")
    assert entries[0].wav_path == b"/tmp/voice/a.wav"
    assert entries[0].offset == 10.0
    assert entries[0].consonant == 20.0
    assert entries[0].blank == -30.0
    assert entries[0].cutoff == -30.0
    assert entries[0].preutterance == 40.0
    assert entries[0].overlap == 5.0
