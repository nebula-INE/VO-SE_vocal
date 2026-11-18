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
