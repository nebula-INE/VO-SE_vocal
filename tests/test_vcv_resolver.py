from modules.audio.vcv_resolver import VcvResolver
from modules.data.oto_parser import OtoEntry, OtoParser


def _entry(alias: str) -> OtoEntry:
    return OtoEntry(
        alias=alias,
        filename=f"{alias.replace(' ', '_')}.wav",
        voice_dir="/dummy",
        left_blank=0,
        fixed_range=0,
        right_blank=0,
        preutterance=0,
        overlap=0,
    )


def _parser() -> OtoParser:
    parser = OtoParser()
    parser._db["a い"] = _entry("a い")
    parser._db["- い"] = _entry("- い")
    parser._db["い"] = _entry("い")
    return parser


def test_vcv_resolver_uses_previous_vowel_when_continuous():
    resolver = VcvResolver(_parser(), use_g2p=False)

    alias, entry = resolver.resolve_note("い", "あ", is_continuous=True)

    assert alias == "a い"
    assert entry is not None


def test_vcv_resolver_drops_previous_vowel_after_gap():
    resolver = VcvResolver(_parser(), use_g2p=False)

    alias, entry = resolver.resolve_note("い", "あ", is_continuous=False)

    assert alias == "- い"
    assert entry is not None

def test_vcv_resolver_sequence_drops_context_after_gap():
    from modules.data.data_models import NoteEvent

    resolver = VcvResolver(_parser(), use_g2p=False)
    notes = [
        NoteEvent(note_number=60, lyric="あ", start_time=0.0, duration=0.5),
        NoteEvent(note_number=62, lyric="い", start_time=1.0, duration=0.5),
    ]

    resolved = resolver.resolve(notes)

    assert resolved[1].alias == "- い"


def test_vcv_resolver_first_note_does_not_use_vcv_context():
    from modules.data.data_models import NoteEvent

    resolver = VcvResolver(_parser(), use_g2p=False)
    notes = [
        NoteEvent(note_number=60, lyric="い", start_time=0.0, duration=0.5),
    ]

    resolved = resolver.resolve(notes)

    assert resolved[0].alias == "- い"
    assert resolved[0].oto_entry is not None


def test_vcv_resolver_missing_alias_returns_unresolved_without_substitution():
    resolver = VcvResolver(_parser(), use_g2p=False)

    alias, entry = resolver.resolve_note(
        "え",
        "あ",
        is_continuous=True,
    )

    assert alias == "え"
    assert entry is None


def test_vcv_resolver_continuity_threshold_follows_tempo():
    from modules.data.data_models import NoteEvent

    resolver = VcvResolver(_parser(), use_g2p=False)
    # 0.20s gap: continuous at 120 BPM (threshold 0.25s),
    # and continuous at 180 BPM only when the gap is <= 0.1667s.
    notes = [
        NoteEvent(note_number=60, lyric="あ", start_time=0.0, duration=0.5),
        NoteEvent(note_number=62, lyric="い", start_time=0.70, duration=0.5),
    ]

    assert resolver.resolve(notes, tempo_bpm=120.0)[1].alias == "a い"
    assert resolver.resolve(notes, tempo_bpm=180.0)[1].alias == "- い"

    notes[1].start_time = 0.69
    assert resolver.resolve(notes, tempo_bpm=120.0)[1].alias == "a い"
    assert resolver.resolve(notes, tempo_bpm=180.0)[1].alias == "- い"
