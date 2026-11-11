from types import SimpleNamespace

import pytest


def _load_midi_test_dependencies():
    """Skip before importing modules that require the optional mido package."""
    mido = pytest.importorskip("mido")

    from modules.data.midi_manager import load_midi_file, save_midi_file
    from modules.gui.mixins.project_io_mixin import ProjectIOMixin
    import modules.data.midi_manager as midi_manager

    return mido, load_midi_file, save_midi_file, ProjectIOMixin, midi_manager


mido, load_midi_file, save_midi_file, ProjectIOMixin, midi_manager = (
    _load_midi_test_dependencies()
)


def test_midi_import_applies_global_tempo_changes_and_keeps_overlaps(tmp_path):
    midi_path = tmp_path / "tempo-overlap.mid"
    tempo_track = mido.MidiTrack()
    tempo_track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    tempo_track.append(mido.MetaMessage("set_tempo", tempo=1_000_000, time=480))
    tempo_track.append(mido.MetaMessage("end_of_track", time=0))

    note_track = mido.MidiTrack()
    note_track.append(mido.Message("note_on", note=60, velocity=80, time=0))
    note_track.append(mido.Message("note_on", note=60, velocity=100, time=240))
    note_track.append(mido.Message("note_off", note=60, velocity=0, time=240))
    note_track.append(mido.Message("note_off", note=60, velocity=0, time=240))
    note_track.append(mido.MetaMessage("end_of_track", time=0))

    midi_file = mido.MidiFile(type=1, ticks_per_beat=480)
    midi_file.tracks.extend([tempo_track, note_track])
    midi_file.save(midi_path)

    notes = load_midi_file(str(midi_path))

    assert notes is not None
    assert len(notes) == 2
    assert [note["start_time"] for note in notes] == pytest.approx([0.0, 0.25])
    assert [note["duration"] for note in notes] == pytest.approx([0.5, 0.75])
    assert [note["velocity"] for note in notes] == [80, 100]


def test_midi_initial_tempo_uses_tick_zero_meta_event(tmp_path):
    midi_path = tmp_path / "initial-tempo.mid"
    tempo_track = mido.MidiTrack()
    tempo_track.append(mido.MetaMessage("set_tempo", tempo=400_000, time=0))
    tempo_track.append(mido.MetaMessage("set_tempo", tempo=750_000, time=480))
    tempo_track.append(mido.MetaMessage("end_of_track", time=0))
    note_track = mido.MidiTrack()
    note_track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    note_track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    note_track.append(mido.MetaMessage("end_of_track", time=0))
    midi_file = mido.MidiFile(type=1, ticks_per_beat=480)
    midi_file.tracks.extend([tempo_track, note_track])
    midi_file.save(midi_path)

    assert midi_manager.get_midi_initial_tempo(str(midi_path)) == pytest.approx(150.0)


def test_midi_export_writes_track_names_tempo_and_notes(tmp_path):
    midi_path = tmp_path / "roundtrip.mid"
    result = save_midi_file(
        str(midi_path),
        [
            {
                "name": "Lead",
                "notes": [
                    {
                        "start_time": 0.0,
                        "duration": 0.5,
                        "note_number": 60,
                        "velocity": 96,
                        "lyrics": "あ",
                    },
                    {
                        "start_time": 0.5,
                        "duration": 0.25,
                        "note_number": 64,
                        "velocity": 88,
                        "lyrics": "い",
                    },
                ],
            },
            {"name": "Harmony", "notes": []},
        ],
        tempo=120.0,
    )

    assert result == str(midi_path)
    loaded = mido.MidiFile(midi_path)
    assert len(loaded.tracks) == 2
    assert any(msg.type == "track_name" and msg.name == "Lead" for msg in loaded.tracks[0])
    assert any(msg.type == "set_tempo" and msg.tempo == 500_000 for msg in loaded.tracks[0])
    assert len([msg for msg in loaded.tracks[0] if msg.type == "note_on"]) == 2

    imported = load_midi_file(str(midi_path))
    assert imported is not None
    assert [note["lyric"] for note in imported] == ["あ", "い"]


def test_midi_export_rejects_invalid_tempo(tmp_path):
    with pytest.raises(ValueError, match="テンポ"):
        save_midi_file(
            str(tmp_path / "invalid.mid"),
            [{"name": "Lead", "notes": []}],
            tempo=0.0,
        )



def test_desktop_midi_import_restores_source_tracks(monkeypatch):
    class Track:
        def __init__(self, name, track_type="vocal"):
            self.name = name
            self.track_type = track_type
            self.notes = []
            self.audio_path = ""
            self.volume = 1.0
            self.pan = 0.0
            self.is_muted = False
            self.is_solo = False
            self.engine_type = "Aural"
            self.color_label = ""

    class StatusBar:
        def showMessage(self, *_args, **_kwargs):
            pass

    class Window(ProjectIOMixin):
        def __init__(self):
            self.tracks = [Track("Existing")]
            self.current_track_idx = 0
            self.timeline_widget = SimpleNamespace(
                notes_list=[],
                tempo=120.0,
                set_notes=lambda notes: setattr(self.timeline_widget, "notes_list", list(notes)),
            )
            self.graph_editor_widget = None
            self.current_playback_time = 0.0
            self.current_voice_id = "NONE"
            self.current_voice = "未選択"
            self.history = None
            self._status_bar = StatusBar()

        def statusBar(self):
            return self._status_bar

    monkeypatch.setattr(midi_manager, "get_midi_initial_tempo", lambda _path: 150.0)
    monkeypatch.setattr(
        midi_manager,
        "load_midi_file",
        lambda _path: [
            {
                "note_number": 60,
                "start_time": 0.0,
                "duration": 0.5,
                "lyric": "あ",
                "_midi_track_index": 0,
                "_midi_track_name": "Lead",
            },
            {
                "note_number": 64,
                "start_time": 0.25,
                "duration": 0.5,
                "lyric": "い",
                "_midi_track_index": 2,
                "_midi_track_name": "Harmony",
            },
        ],
    )
    window = Window()

    assert window.load_midi_file_from_path("song.mid") is True
    assert [track.name for track in window.tracks] == ["Lead", "Harmony"]
    assert [note.note_number for note in window.tracks[0].notes] == [60]
    assert [note.note_number for note in window.tracks[1].notes] == [64]
    assert [note.note_number for note in window.timeline_widget.notes_list] == [60]
    assert window.timeline_widget.tempo == pytest.approx(150.0)



def test_midi_import_decodes_cp932_japanese_lyrics(tmp_path):
    midi_path = tmp_path / "cp932-lyrics.mid"
    midi_file = mido.MidiFile(type=0, ticks_per_beat=480, charset="cp932")
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("lyrics", text="波音リツ", time=0))
    track.append(mido.Message("note_on", note=60, velocity=100, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    track.append(mido.MetaMessage("end_of_track", time=0))
    midi_file.tracks.append(track)
    midi_file.save(midi_path)

    imported = load_midi_file(str(midi_path))

    assert imported is not None
    assert len(imported) == 1
    assert imported[0]["lyric"] == "波音リツ"
