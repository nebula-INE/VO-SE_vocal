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
