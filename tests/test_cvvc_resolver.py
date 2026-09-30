from modules.audio.cvvc_resolver import CvvcResolver
from modules.data.data_models import NoteEvent
from modules.data.oto_parser import OtoEntry, OtoParser


def _entry(alias: str) -> OtoEntry:
    return OtoEntry(
        alias=alias,
        filename=f"{alias.replace(' ', '_')}.wav",
        voice_dir="/dummy",
        left_blank=0,
        fixed_range=20,
        right_blank=-100,
        preutterance=50,
        overlap=20,
    )


def _parser() -> OtoParser:
    parser = OtoParser()
    parser._db["- か"] = _entry("- か")
    parser._db["か"] = _entry("か")
    parser._db["a k"] = _entry("a k")
    parser._db["i k"] = _entry("i k")
    parser._db["a き"] = _entry("a き")
    return parser


def test_detects_cvvc_voicebank():
    resolver = CvvcResolver(_parser())
    assert resolver.has_vc()
    assert resolver.classify_voicebank() == "mixed"


def test_resolves_cv_without_using_another_vowel_context():
    resolver = CvvcResolver(_parser())

    alias, entry = resolver.resolve_cv("か")

    assert alias == "か"
    assert entry is not None


def test_resolves_exact_vc_alias():
    resolver = CvvcResolver(_parser())

    alias, entry = resolver.resolve_vc("a", "k")

    assert alias == "a k"
    assert entry is not None


def test_does_not_substitute_missing_vc_with_another_vowel():
    resolver = CvvcResolver(_parser())

    alias, entry = resolver.resolve_vc("u", "k")

    assert alias == ""
    assert entry is None


def test_does_not_misclassify_vcv_as_vc():
    resolver = CvvcResolver(_parser())

    assert not resolver._is_vc_alias("a き")
    assert not resolver._is_vc_alias("a ka")
    assert resolver._is_vc_alias("a k")
    assert resolver._is_vc_alias("a sh")


def test_sequence_keeps_cv_and_vc_as_distinct_segments():
    resolver = CvvcResolver(_parser())
    notes = [
        NoteEvent(note_number=60, lyric="あ", start_time=0.0, duration=0.5),
        NoteEvent(note_number=62, lyric="か", start_time=0.5, duration=0.5),
    ]

    # The first CV is unresolved in this deliberately minimal fixture.
    # The second note contributes its CV; the VC transition is resolved
    # separately and is never silently merged into the CV alias.
    segments = resolver.resolve_sequence(
        notes,
        previous_vowels=[None, "a"],
        next_consonants=[None, "k"],
    )

    assert [(segment.kind, segment.alias) for segment in segments] == [
        ("cv", "か"),
        ("vc", "a k"),
    ]
    assert all(segment.duration >= 0.0 for segment in segments)


def test_resolve_notes_uses_analyzed_phonemes_for_vc():
    resolver = CvvcResolver(_parser())
    notes = [
        NoteEvent(note_number=60, lyric="あ", start_time=0.0, duration=0.5, phonemes=["a"]),
        NoteEvent(note_number=62, lyric="か", start_time=0.5, duration=0.5, phonemes=["k", "a"]),
    ]

    segments = resolver.resolve_notes(notes)

    assert [(segment.kind, segment.alias) for segment in segments] == [
        ("cv", "か"),
        ("vc", "a k"),
    ]
