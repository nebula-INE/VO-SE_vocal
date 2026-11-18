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


def test_p2p3_autotune_preserves_explicit_ust_portamento():
    plain_prev = NoteEvent(note_number=60, start_time=0.0, duration=0.5, lyric="あ")
    explicit_note = NoteEvent.from_dict({
        "note_number": 64,
        "start_time": 0.5,
        "duration": 0.5,
        "lyric": "い",
        "_ust_pbs": "-29;-40",
        "_ust_pbw": "56",
        "_ust_pby": "12",
        "_ust_has_pbs": True,
        "_ust_has_pbw": True,
        "_ust_has_pby": True,
    })
    placeholder_next = NoteEvent.from_dict({
        "note_number": 67,
        "start_time": 1.0,
        "duration": 0.5,
        "lyric": "う",
        "_ust_pbs": "0;0",
        "_ust_pbw": "50",
        "_ust_pby": "0",
    })
    changed = apply_p2p3_autotune([plain_prev, explicit_note, placeholder_next], 120.0)
    assert changed == 2
    assert explicit_note._ust_pbs == "-29;-40"
    assert explicit_note._ust_pbw == "56"
    assert explicit_note._ust_pby == "12"
    assert placeholder_next._ust_pbw != "50"
    assert placeholder_next._ust_pbs.startswith("-")
