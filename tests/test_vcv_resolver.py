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
