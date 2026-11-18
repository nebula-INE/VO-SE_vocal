from types import SimpleNamespace

from modules.gui.pitch_curve_dialog import (
    PitchCurveEditorDialog,
    note_pitch_fields,
    serialize_pitch_points,
)


def test_pitch_curve_dialog_preserves_ust_mode2_fields(qtbot):
    note = SimpleNamespace(
        duration=0.5,
        _ust_pbs="-29;-20",
        _ust_pbw="56,44",
        _ust_pby="10,0",
        _ust_pbm="s,j",
    )
    dialog = PitchCurveEditorDialog(note, tempo=120.0)
    qtbot.addWidget(dialog)

    assert note_pitch_fields(note) == ("-29;-20", "56,44", "10,0", "s,j")
    assert dialog._preview.error == ""

    dialog._fields["PBW"].setText("not-a-number")
    assert dialog._preview.error
    assert not dialog._apply_button.isEnabled()
    dialog._fields["PBW"].setText("56,44")
    assert dialog._preview.error == ""
    assert dialog._apply_button.isEnabled()

    dialog._fields["PBY"].setText("20,0")
    dialog.apply_to_note(note)
    assert note._ust_pby == "20,0"
    assert note._ust_pbs == "-29;-20"


def test_pitch_point_serialization_keeps_ust_units_and_segment_shapes():
    fields = serialize_pitch_points(
        [(-29.0, -2.0), (27.0, 1.0), (71.0, 0.0)],
        ["s", "j"],
    )
    assert fields == ("-29;-20", "56,44", "10,0", "s,j")


def test_pitch_point_serialization_fills_missing_shapes_with_linear():
    fields = serialize_pitch_points([(0.0, 0.0), (100.0, 0.5), (200.0, -0.5)])
    assert fields == ("0;0", "100,100", "5,-5", "s,s")

def test_pitch_curve_editor_preserves_empty_pbm_shape_segments(qtbot):
    note = SimpleNamespace(
        duration=0.5,
        _ust_pbs="0;0",
        _ust_pbw="50,50,50",
        _ust_pby="10,0,-10",
        _ust_pbm="s,,j",
    )
    dialog = PitchCurveEditorDialog(note, tempo=120.0)
    qtbot.addWidget(dialog)

    assert dialog._preview.shapes == ["s", "", "j"]
    dialog.apply_to_note(note)
    assert note._ust_pbs == "0;0"
    assert note._ust_pbw == "50,50,50"
    assert note._ust_pby == "10,0,-10"
    assert note._ust_pbm == "s,,j"


def test_pitch_point_serialization_keeps_blank_pbm_shape_entries():
    fields = serialize_pitch_points(
        [(0.0, 0.0), (50.0, 1.0), (100.0, 0.0)],
        ["s", ""],
    )
    assert fields == ("0;0", "50,50", "10,0", "s,")

def test_note_inspector_updates_existing_ust_metadata_without_renderer_changes(qtbot):
    from modules.gui.note_inspector_dialog import NoteInspectorDialog

    note = SimpleNamespace(
        note_number=60,
        lyric="あ",
        lyrics="あ",
        duration=0.5,
        _ust_intensity=120.0,
        _ust_flags="g-5",
        _ust_flags_present=False,
    )
    dialog = NoteInspectorDialog(note, tempo=120.0)
    qtbot.addWidget(dialog)

    assert dialog.length_spin.value() == 480
    assert dialog.intensity_spin.value() == 120.0
    assert dialog.flags_edit.text() == "g-5"

    dialog.lyric_edit.setText("い")
    dialog.note_number_spin.setValue(62)
    dialog.length_spin.setValue(960)
    dialog.intensity_spin.setValue(135.0)
    dialog.flags_edit.setText("g-10")

    assert dialog.apply_to_note(note) is True
    assert note.lyric == "い"
    assert note.note_number == 62
    assert note.duration == 1.0
    assert note._ust_intensity == 135.0
    assert note._ust_flags == "g-10"
    assert note._ust_flags_present is True


def test_note_inspector_preserves_inherited_flags_when_not_edited(qtbot):
    from modules.gui.note_inspector_dialog import NoteInspectorDialog

    note = SimpleNamespace(
        note_number=60,
        lyric="あ",
        lyrics="あ",
        duration=0.5,
        _ust_flags="g-5",
        _ust_flags_present=False,
    )
    dialog = NoteInspectorDialog(note, tempo=120.0)
    qtbot.addWidget(dialog)

    assert dialog.apply_to_note(note) is False
    assert note._ust_flags == "g-5"
    assert note._ust_flags_present is False
