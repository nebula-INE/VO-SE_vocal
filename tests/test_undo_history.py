# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
import os

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"


@pytest.mark.smoke
def test_reset_selected_lyrics_records_undo_edit():
    from modules.gui.timeline_widget import TimelineWidget

    class Note:
        def __init__(self, lyrics, phoneme, selected=True):
            self.lyrics = lyrics
            self.phoneme = phoneme
            self.is_selected = selected

    widget = TimelineWidget.__new__(TimelineWidget)
    widget.notes_list = [Note("こんにちは", "konnichiwa"), Note("la", "la")]
    calls = []

    def fake_snapshot():
        return [object()]

    def fake_commit(before, description):
        calls.append((before, description))

    widget._snapshot_notes = fake_snapshot
    widget._commit_edit = fake_commit
    widget.notes_changed_signal = type("Signal", (), {"emit": lambda self: None})()
    widget.update = lambda: None

    widget._reset_selected_lyrics()

    assert widget.notes_list[0].lyrics == "la"
    assert widget.notes_list[0].phoneme == "la"
    assert len(calls) == 1
    assert calls[0][1] == "歌詞を 'la' にリセット"


@pytest.mark.smoke
def test_reset_selected_lyrics_does_not_record_when_already_la():
    from modules.gui.timeline_widget import TimelineWidget

    class Note:
        def __init__(self):
            self.lyrics = "la"
            self.phoneme = "la"
            self.is_selected = True

    widget = TimelineWidget.__new__(TimelineWidget)
    widget.notes_list = [Note()]
    calls = []
    widget._snapshot_notes = lambda: [object()]
    widget._commit_edit = lambda *args: calls.append(args)
    widget.update = lambda: None

    widget._reset_selected_lyrics()

    assert calls == []


@pytest.mark.smoke
def test_smooth_param_records_undo_and_redo():
    from modules.gui.timeline_widget import TimelineWidget

    class History:
        def __init__(self):
            self.commands = []

        def push(self, command):
            self.commands.append(command)

    class Signal:
        def emit(self):
            pass

    history = History()
    parent = type("Window", (), {"history": history})()
    widget = TimelineWidget.__new__(TimelineWidget)
    widget.current_param_layer = "Pitch"
    widget.parameters = {"Pitch": {0.0: 0.0, 1.0: 1.0, 2.0: 0.0}}
    widget.notes_changed_signal = Signal()
    widget.update = lambda: None
    widget.window = lambda: parent

    widget._smooth_param()

    expected = {0.0: 0.5, 1.0: 1 / 3, 2.0: 0.5}
    assert widget.parameters["Pitch"] == expected
    assert len(history.commands) == 1

    history.commands[0].undo()
    assert widget.parameters["Pitch"] == {0.0: 0.0, 1.0: 1.0, 2.0: 0.0}

    history.commands[0].redo()
    assert widget.parameters["Pitch"] == expected

@pytest.mark.smoke
def test_pitch_automation_only_edit_is_recorded_and_restorable_by_undo_redo():
    from modules.data.data_models import NoteEvent
    from modules.gui.timeline_widget import TimelineWidget

    class Signal:
        def __init__(self):
            self.calls = []

        def emit(self, *args):
            self.calls.append(args)

    widget = TimelineWidget.__new__(TimelineWidget)
    note = NoteEvent(note_number=60, start_time=0.0, duration=0.5, lyric="あ")
    widget.notes_list = [note]
    widget.edit_committed_signal = Signal()
    widget.notes_changed_signal = Signal()
    widget._invalidate_note_rects = lambda: None
    widget.update = lambda: None

    before = widget._snapshot_notes()
    note._ust_pbs = "-20;0"
    note._ust_pbw = "10,10"
    note._ust_pby = "0,0"
    widget._commit_edit(before, "UTAU P2P3 自動調声")

    assert len(widget.edit_committed_signal.calls) == 1
    saved_before, saved_after, description = widget.edit_committed_signal.calls[0]
    assert description == "UTAU P2P3 自動調声"

    widget._restore_notes_snapshot(saved_before)
    assert getattr(widget.notes_list[0], "_ust_pbs", None) is None
    assert getattr(widget.notes_list[0], "_ust_pbw", None) is None
    assert getattr(widget.notes_list[0], "_ust_pby", None) is None

    widget._restore_notes_snapshot(saved_after)
    assert widget.notes_list[0]._ust_pbs == "-20;0"
    assert widget.notes_list[0]._ust_pbw == "10,10"
    assert widget.notes_list[0]._ust_pby == "0,0"
