from types import SimpleNamespace

from modules.gui.timeline_widget import TimelineWidget


def test_ghost_track_collection_excludes_active_track_and_non_vocal_tracks():
    active = SimpleNamespace(
        track_type="vocal",
        notes=[SimpleNamespace(lyric="active")],
    )
    other_vocal = SimpleNamespace(
        track_type="vocal",
        notes=[SimpleNamespace(lyric="ghost")],
    )
    audio_track = SimpleNamespace(
        track_type="wave",
        notes=[SimpleNamespace(lyric="audio")],
    )
    window = SimpleNamespace(
        tracks=[active, other_vocal, audio_track],
        current_track_idx=0,
    )

    class FakeTimeline:
        def window(self):
            return window

    entries = TimelineWidget._get_ghost_track_entries(FakeTimeline())

    assert entries == [(other_vocal, other_vocal.notes)]


def test_ghost_track_collection_handles_missing_window_tracks():
    class FakeTimeline:
        def window(self):
            return SimpleNamespace(tracks=[], current_track_idx=0)

    assert TimelineWidget._get_ghost_track_entries(FakeTimeline()) == []

def test_selected_notes_duplicate_as_one_relative_block_with_undo_commit():
    original_a = SimpleNamespace(
        start_time=1.0,
        duration=0.5,
        lyrics="あ",
        is_selected=True,
        _ust_pbs="0;0",
    )
    original_b = SimpleNamespace(
        start_time=2.0,
        duration=0.25,
        lyrics="い",
        is_selected=True,
    )
    untouched = SimpleNamespace(
        start_time=4.0,
        duration=0.5,
        lyrics="う",
        is_selected=False,
    )

    class Signal:
        def __init__(self):
            self.count = 0

        def emit(self):
            self.count += 1

    class FakeTimeline:
        def __init__(self):
            self.notes_list = [original_a, original_b, untouched]
            self.notes_changed_signal = Signal()
            self.commits = []
            self.invalidations = 0
            self.updated = False

        def _snapshot_notes(self):
            return [
                (note.start_time, note.duration, note.lyrics)
                for note in self.notes_list
            ]

        def _invalidate_note_rects(self):
            self.invalidations += 1

        def _commit_edit(self, before, description):
            self.commits.append((before, description))

        def update(self):
            self.updated = True

    timeline = FakeTimeline()
    TimelineWidget._duplicate_selected_notes(timeline)

    assert len(timeline.notes_list) == 5
    duplicate_a, duplicate_b = timeline.notes_list[3:]
    assert duplicate_a.start_time == 2.25
    assert duplicate_b.start_time == 3.25
    assert duplicate_a.lyrics == "あ"
    assert duplicate_b.lyrics == "い"
    assert duplicate_a._ust_pbs == "0;0"
    assert original_a.is_selected is False
    assert original_b.is_selected is False
    assert duplicate_a.is_selected is True
    assert duplicate_b.is_selected is True
    assert untouched.is_selected is False
    assert timeline.notes_changed_signal.count == 1
    assert timeline.commits[0][1] == "ノート複製"
    assert timeline.invalidations == 1
    assert timeline.updated is True
