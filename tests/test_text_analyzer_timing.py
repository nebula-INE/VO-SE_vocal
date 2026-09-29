from typing import Any, cast

from modules.data.data_models import NoteEvent
from modules.data.oto_parser import OtoParser
from modules.data.text_analyzer import TextAnalyzer


class _FakeOto:
    @property
    def preutterance_sec(self):
        return 0.12

    @property
    def overlap_sec(self):
        return 0.03


class _FakeResolver:
    def __init__(self, *_args, **_kwargs):
        pass

    def resolve_note(self, *_args, **_kwargs):
        return ("a", _FakeOto())


def test_explicit_zero_preutterance_and_overlap_override_oto(monkeypatch):
    analyzer = TextAnalyzer()
    monkeypatch.setattr(
        "modules.data.text_analyzer.VcvResolver",
        _FakeResolver,
    )
    monkeypatch.setattr(
        analyzer,
        "_lyric_to_phonemes",
        lambda _lyric: ["a"],
    )

    note = NoteEvent(
        note_number=60,
        lyric="a",
        start_time=1.0,
        duration=0.5,
        pre_utterance=0.0,
        overlap=0.0,
    )
    cast(Any, note)._ust_preutterance_explicit = True
    cast(Any, note)._ust_overlap_explicit = True

    notes, _timeline = analyzer.align_vocal_timing([note], cast(OtoParser, object()))

    assert notes[0].pre_utterance == 0.0
    assert notes[0].overlap == 0.0
    assert notes[0].onset == 1.0


def test_missing_ust_overrides_uses_oto(monkeypatch):
    analyzer = TextAnalyzer()
    monkeypatch.setattr(
        "modules.data.text_analyzer.VcvResolver",
        _FakeResolver,
    )
    monkeypatch.setattr(
        analyzer,
        "_lyric_to_phonemes",
        lambda _lyric: ["a"],
    )

    note = NoteEvent(
        note_number=60,
        lyric="a",
        start_time=1.0,
        duration=0.5,
    )

    notes, _timeline = analyzer.align_vocal_timing([note], cast(OtoParser, object()))

    assert notes[0].pre_utterance == 120.0
    assert notes[0].overlap == 30.0
    assert notes[0].onset == 0.88

def test_default_note_event_zero_uses_oto(monkeypatch):
    analyzer = TextAnalyzer()
    monkeypatch.setattr(
        "modules.data.text_analyzer.VcvResolver",
        _FakeResolver,
    )
    monkeypatch.setattr(
        analyzer,
        "_lyric_to_phonemes",
        lambda _lyric: ["a"],
    )

    note = NoteEvent(
        note_number=60,
        lyric="a",
        start_time=1.0,
        duration=0.5,
        pre_utterance=0.0,
        overlap=0.0,
    )

    notes, _timeline = analyzer.align_vocal_timing([note], cast(OtoParser, object()))

    assert notes[0].pre_utterance == 120.0
    assert notes[0].overlap == 30.0


def test_vcv_is_disabled_after_a_long_gap(monkeypatch):
    analyzer = TextAnalyzer()
    calls = []

    class _RecordingResolver:
        def __init__(self, *_args, **_kwargs):
            pass

        def resolve_note(self, *args, **kwargs):
            calls.append((args, kwargs))
            return ("- い", None)

    monkeypatch.setattr(
        "modules.data.text_analyzer.VcvResolver",
        _RecordingResolver,
    )
    monkeypatch.setattr(
        analyzer,
        "_lyric_to_phonemes",
        lambda _lyric: ["a"],
    )

    notes = [
        NoteEvent(note_number=60, lyric="あ", start_time=0.0, duration=0.5),
        NoteEvent(note_number=62, lyric="い", start_time=1.0, duration=0.5),
    ]

    analyzer.align_vocal_timing(notes, cast(OtoParser, object()))

    assert len(calls) == 2
    assert calls[0][1]["is_continuous"] is False
    assert calls[1][1]["is_continuous"] is False
