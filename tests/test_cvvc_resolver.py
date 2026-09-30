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
    parser._db["き"] = _entry("き")
    parser._db["a k"] = _entry("a k")
    parser._db["a n"] = _entry("a n")
    parser._db["i k"] = _entry("i k")
    parser._db["a き"] = _entry("a き")
    return parser


def test_detects_cvvc_voicebank():
    resolver = CvvcResolver(_parser())
    assert resolver.has_vc()
    assert resolver.classify_voicebank() == "mixed"

def test_detects_pure_cvvc_voicebank():
    parser = OtoParser()
    parser._db["- か"] = _entry("- か")
    parser._db["か"] = _entry("か")
    parser._db["a k"] = _entry("a k")

    resolver = CvvcResolver(parser)

    assert resolver.has_vc()
    assert resolver.classify_voicebank() == "cvvc"



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


def test_resolves_moraic_nasal_vc_alias():
    resolver = CvvcResolver(_parser())

    alias, entry = resolver.resolve_vc("a", "n")

    assert alias == "a n"
    assert entry is not None


def test_initial_moraic_nasal_is_not_dropped_as_a_vowel():
    resolver = CvvcResolver(_parser())
    note = NoteEvent(
        note_number=62,
        lyric="ん",
        start_time=0.5,
        duration=0.5,
        phonemes=["n"],
    )

    assert resolver._initial_consonant(note) == "n"


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


def test_resolve_notes_does_not_insert_vc_across_rest():
    resolver = CvvcResolver(_parser())
    notes = [
        NoteEvent(note_number=60, lyric="あ", start_time=0.0, duration=0.5, phonemes=["a"]),
        NoteEvent(note_number=0, lyric="R", start_time=0.5, duration=0.25, phonemes=[]),
        NoteEvent(note_number=62, lyric="か", start_time=0.75, duration=0.5, phonemes=["k", "a"]),
    ]

    segments = resolver.resolve_notes(notes)

    assert [(segment.kind, segment.alias) for segment in segments] == [
        ("cv", "か"),
    ]


def test_resolve_notes_prefers_analyzed_previous_vowel():
    resolver = CvvcResolver(_parser())
    notes = [
        NoteEvent(note_number=60, lyric="う", start_time=0.0, duration=0.5, phonemes=["a"]),
        NoteEvent(note_number=62, lyric="か", start_time=0.5, duration=0.5, phonemes=["k", "a"]),
    ]

    segments = resolver.resolve_notes(notes)

    assert [(segment.kind, segment.alias) for segment in segments] == [
        ("cv", "か"),
        ("vc", "a k"),
    ]


def test_expand_notes_for_render_inserts_moraic_nasal_vc():
    resolver = CvvcResolver(_parser())
    notes = [
        NoteEvent(note_number=60, lyric="あ", start_time=0.0, duration=0.5, phonemes=["a"]),
        NoteEvent(note_number=62, lyric="ん", start_time=0.5, duration=0.5, phonemes=["n"], pre_utterance=0.06),
    ]

    expanded = resolver.expand_notes_for_render(notes)

    assert len(expanded) == 3
    vc = expanded[1]
    assert vc._cvvc_render_kind == "vc"
    assert vc._cvvc_render_alias == "a n"
    assert vc.duration == 0.06


def test_expand_notes_for_render_uses_following_preutterance_for_vc():
    resolver = CvvcResolver(_parser())
    notes = [
        NoteEvent(note_number=60, lyric="あ", start_time=0.0, duration=0.5, phonemes=["a"]),
        NoteEvent(note_number=62, lyric="か", start_time=0.5, duration=0.5, phonemes=["k", "a"], pre_utterance=0.08),
    ]

    expanded = resolver.expand_notes_for_render(notes)

    assert len(expanded) == 3
    vc, cv = expanded[1], expanded[2]
    assert vc._cvvc_render_kind == "vc"
    assert vc._cvvc_render_alias == "a k"
    assert vc.lyric == "a k"
    assert vc.start_time == 0.42
    assert vc.duration == 0.08
    assert vc.pre_utterance == 0.0
    assert cv._cvvc_render_kind == "cv"
    assert cv.lyric == "か"
    assert notes[1].lyric == "か"
    assert notes[1].start_time == 0.5


def test_expand_notes_for_render_skips_missing_vc_without_changing_cv():
    resolver = CvvcResolver(_parser())
    notes = [
        NoteEvent(note_number=60, lyric="あ", start_time=0.0, duration=0.5, phonemes=["a"]),
        NoteEvent(note_number=62, lyric="き", start_time=0.5, duration=0.5, phonemes=["k", "i"], pre_utterance=0.08),
    ]

    expanded = resolver.expand_notes_for_render(notes)

    assert len(expanded) == 2
    assert [getattr(note, "_cvvc_render_kind", None) for note in expanded] == ["cv", "cv"]
    assert [note.lyric for note in expanded] == ["あ", "き"]


def test_expand_notes_for_render_does_not_cross_rest():
    resolver = CvvcResolver(_parser())
    notes = [
        NoteEvent(note_number=60, lyric="あ", start_time=0.0, duration=0.5, phonemes=["a"]),
        NoteEvent(note_number=0, lyric="R", start_time=0.5, duration=0.25, phonemes=[]),
        NoteEvent(note_number=62, lyric="か", start_time=0.75, duration=0.5, phonemes=["k", "a"], pre_utterance=0.08),
    ]

    expanded = resolver.expand_notes_for_render(notes)

    assert len(expanded) == 3
    assert all(getattr(note, "_cvvc_render_kind", None) != "vc" for note in expanded)


def test_expand_notes_for_render_skips_long_gap():
    resolver = CvvcResolver(_parser())
    notes = [
        NoteEvent(note_number=60, lyric="あ", start_time=0.0, duration=0.5, phonemes=["a"]),
        NoteEvent(note_number=62, lyric="か", start_time=1.0, duration=0.5, phonemes=["k", "a"], pre_utterance=0.08),
    ]

    expanded = resolver.expand_notes_for_render(notes, tempo_bpm=120.0)

    assert len(expanded) == 2
    assert all(getattr(note, "_cvvc_render_kind", None) != "vc" for note in expanded)
