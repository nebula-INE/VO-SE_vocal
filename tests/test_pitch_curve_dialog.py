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
