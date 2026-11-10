import json
from types import SimpleNamespace

from modules.data.data_models import NoteEvent
from modules.gui.mixins.project_io_mixin import ProjectIOMixin
import modules.gui.mixins.project_io_mixin as project_io_module


class _Track:
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


class _Timeline:
    def __init__(self):
        self.notes_list = [NoteEvent(note_number=60, start_time=0.0, duration=0.5)]
        self.tempo = 120.0

    def set_notes(self, notes):
        self.notes_list = list(notes)

    def update(self):
        pass


class _StatusBar:
    def showMessage(self, *_args, **_kwargs):
        pass


class _ProjectWindow:
    def __init__(self):
        track = _Track("Current")
        track.notes = [NoteEvent(note_number=60, start_time=0.0, duration=0.5)]
        self.tracks = [track]
        self.current_track_idx = 0
        self.timeline_widget = _Timeline()
        self.graph_editor_widget = SimpleNamespace(tempo=120.0, all_parameters={})
        self.current_playback_time = 0.0
        self.current_voice_id = "voice-before"
        self.current_voice = "Before"
        self._status_bar = _StatusBar()

    def statusBar(self):
        return self._status_bar


def test_invalid_later_track_does_not_replace_current_project(tmp_path, monkeypatch):
    window = _ProjectWindow()
    original_track = window.tracks[0]
    original_notes = list(window.timeline_widget.notes_list)
    original_tempo = window.timeline_widget.tempo

    project_path = tmp_path / "broken.vose"
    project_path.write_text(
        json.dumps({
            "tempo": 180.0,
            "tracks": [
                {
                    "name": "Would-be replacement",
                    "type": "vocal",
                    "notes": [{
                        "note_number": 64,
                        "start_time": 0.0,
                        "duration": 0.75,
                        "lyric": "あ",
                    }],
                },
                {"name": "Broken", "type": "vocal", "notes": "not-an-array"},
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(project_io_module.QMessageBox, "critical", lambda *_a, **_k: None)

    result = ProjectIOMixin.load_json_project(window, str(project_path))

    assert result is False
    assert window.tracks == [original_track]
    assert window.tracks[0].name == "Current"
    assert window.timeline_widget.notes_list == original_notes
    assert window.timeline_widget.tempo == original_tempo


def test_non_finite_track_mixer_values_are_rejected_before_commit(tmp_path, monkeypatch):
    window = _ProjectWindow()
    project_path = tmp_path / "non-finite.vose"
    project_path.write_text(
        json.dumps(
            {
                "tempo": 140.0,
                "tracks": [{
                    "name": "Invalid mixer",
                    "type": "vocal",
                    "notes": [],
                    "volume": float("nan"),
                    "pan": 0.0,
                }],
            },
            allow_nan=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(project_io_module.QMessageBox, "critical", lambda *_a, **_k: None)

    result = ProjectIOMixin.load_json_project(window, str(project_path))

    assert result is False
    assert len(window.tracks) == 1
    assert window.tracks[0].name == "Current"
    assert window.timeline_widget.tempo == 120.0


def test_non_finite_note_timing_is_rejected_before_commit(tmp_path, monkeypatch):
    window = _ProjectWindow()
    project_path = tmp_path / "invalid-note.vose"
    project_path.write_text(
        json.dumps(
            {
                "tempo": 130.0,
                "tracks": [{
                    "name": "Invalid note",
                    "type": "vocal",
                    "notes": [{
                        "note_number": 60,
                        "start_time": 0.0,
                        "duration": float("nan"),
                        "lyric": "あ",
                    }],
                }],
            },
            allow_nan=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(project_io_module.QMessageBox, "critical", lambda *_a, **_k: None)

    result = ProjectIOMixin.load_json_project(window, str(project_path))

    assert result is False
    assert len(window.tracks) == 1
    assert window.tracks[0].name == "Current"
    assert window.timeline_widget.tempo == 120.0



def test_project_load_restores_saved_voice_identifier(tmp_path, monkeypatch):
    window = _ProjectWindow()
    project_path = tmp_path / "saved-voice.vose"
    project_path.write_text(
        json.dumps({
            "tempo": 120.0,
            "current_voice_id": "utau:voice-a",
            "current_voice": "Singer A",
            "tracks": [{
                "name": "Vocal 1",
                "type": "vocal",
                "notes": [],
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(project_io_module.QMessageBox, "critical", lambda *_a, **_k: None)

    result = ProjectIOMixin.load_json_project(window, str(project_path))

    assert result is True
    assert window.current_voice_id == "utau:voice-a"
    assert window.current_voice == "Singer A"


def test_project_save_persists_selected_voice_identifier(tmp_path, monkeypatch):
    window = _ProjectWindow()
    window.current_voice_id = "utau:voice-a"
    window.current_voice = "Singer A"
    project_path = tmp_path / "saved-voice.vose"
    monkeypatch.setattr(
        project_io_module.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(project_path), "VO-SE Project (*.vose)"),
    )

    result = ProjectIOMixin.on_save_project_clicked(window)

    assert result is True
    saved = json.loads(project_path.read_text(encoding="utf-8"))
    assert saved["current_voice_id"] == "utau:voice-a"
    assert saved["current_voice"] == "Singer A"


def test_project_undo_snapshot_restores_voice_identifier():
    window = _ProjectWindow()
    state = ProjectIOMixin._capture_project_edit_state(window)
    window.current_voice_id = "utau:voice-b"
    window.current_voice = "Singer B"

    ProjectIOMixin._restore_project_edit_state(window, state)

    assert window.current_voice_id == "voice-before"
    assert window.current_voice == "Before"
