from modules.data.data_models import NoteEvent
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
        lyric="a",
        start_time=1.0,
        duration=0.5,
        pre_utterance=0.0,
        overlap=0.0,
    )
    note._ust_preutterance_explicit = True
    note._ust_overlap_explicit = True

    notes, _timeline = analyzer.align_vocal_timing([note], object())

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
        lyric="a",
        start_time=1.0,
        duration=0.5,
        pre_utterance=None,
        overlap=None,
    )

    notes, _timeline = analyzer.align_vocal_timing([note], object())

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
        lyric="a",
        start_time=1.0,
        duration=0.5,
        pre_utterance=0.0,
        overlap=0.0,
    )

    notes, _timeline = analyzer.align_vocal_timing([note], object())

    assert notes[0].pre_utterance == 120.0
    assert notes[0].overlap == 30.0
