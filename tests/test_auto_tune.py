from modules.data.auto_tune import apply_p2p3_autotune
from modules.data.data_models import NoteEvent


def test_p2p3_autotune_adds_continuous_transition_and_vibrato_fields():
    notes = [
        NoteEvent(note_number=60, start_time=0.0, duration=0.5, lyric="あ"),
        NoteEvent(note_number=64, start_time=0.5, duration=1.0, lyric="い"),
    ]
    changed = apply_p2p3_autotune(notes, 120.0)
    assert changed == 2
    assert notes[0]._ust_pbs
    assert notes[0]._ust_pbw
    assert notes[1]._ust_pbs.startswith("-")
    assert notes[1]._ust_pby


def test_p2p3_autotune_resets_rest_portamento_fields():
    note = NoteEvent(note_number=60, start_time=0.0, duration=0.25, lyric="R")
    apply_p2p3_autotune([note], 120.0)
    assert note._ust_pbs == "0;0"
    assert note._ust_pbw == ""
    assert note._ust_pby == ""


def test_p2p3_autotune_rejects_invalid_tempo():
    try:
        apply_p2p3_autotune([], 0)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid tempo should fail")
