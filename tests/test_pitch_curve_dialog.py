from types import SimpleNamespace

from modules.gui.pitch_curve_dialog import PitchCurveEditorDialog, note_pitch_fields


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
    dialog._fields["PBW"].setText("56,44")
    assert dialog._preview.error == ""

    dialog._fields["PBY"].setText("20,0")
    dialog.apply_to_note(note)
    assert note._ust_pby == "20,0"
    assert note._ust_pbs == "-29;-20"
