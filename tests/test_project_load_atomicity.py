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
    def __init__(self):
        self.messages = []

    def showMessage(self, message, *_args, **_kwargs):
        self.messages.append(message)


class _VoiceCard:
    def __init__(self, display_name):
        self.display_name = display_name
        self.is_recruiting = False
        self.selected = False

    def set_selected(self, selected):
        self.selected = selected


class _VoiceGallery:
    def __init__(self, window):
        self.window = window
        self.cards = {
            "voice-a": _VoiceCard("Voice A"),
            "voice-b": _VoiceCard("Voice B"),
        }

    def on_card_clicked(self, display_name, internal_id):
        for card in self.cards.values():
            card.set_selected(False)
        self.cards[internal_id].set_selected(True)
        self.window.on_voice_changed(display_name, internal_id)


class _ProjectWindow:
    def __init__(self):
        track = _Track("Current")
        track.notes = [NoteEvent(note_number=60, start_time=0.0, duration=0.5)]
        self.tracks = [track]
        self.current_track_idx = 0
        self.timeline_widget = _Timeline()
        self.graph_editor_widget = SimpleNamespace(
            tempo=120.0,
            all_parameters={},
            update=lambda: None,
        )
        self.current_playback_time = 0.0
        self.current_voice_id = "voice-a"
        self.current_voice = "Voice A"
        self.voice_gallery = _VoiceGallery(self)
        self.character_selector = None
        self.history = None
        self._status_bar = _StatusBar()

    def statusBar(self):
        return self._status_bar

    def on_voice_changed(self, display_name, internal_id):
        self.current_voice = display_name
        self.current_voice_id = internal_id


def _bind_project_mixin_methods(window):
    window._capture_project_edit_state = lambda: ProjectIOMixin._capture_project_edit_state(window)
    window._restore_project_edit_state = lambda state: ProjectIOMixin._restore_project_edit_state(window, state)
    window._apply_project_voice_selection = lambda voice_id, name: ProjectIOMixin._apply_project_voice_selection(
        window, voice_id, name
    )
    window._record_project_state_edit = lambda before, after, description: ProjectIOMixin._record_project_state_edit(
        window, before, after, description
    )


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


def test_project_save_persists_the_current_voice_identity(tmp_path, monkeypatch):
    window = _ProjectWindow()
    project_path = tmp_path / "voice-project.vose"
    monkeypatch.setattr(
        project_io_module.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(project_path), "VO-SE Project (*.vose)"),
    )

    assert ProjectIOMixin.on_save_project_clicked(window) is True

    saved = json.loads(project_path.read_text(encoding="utf-8"))
    assert saved["current_voice_id"] == "voice-a"
    assert saved["current_voice"] == "Voice A"


def test_project_load_restores_saved_voice_when_installed(tmp_path, monkeypatch):
    window = _ProjectWindow()
    _bind_project_mixin_methods(window)
    project_path = tmp_path / "saved-voice.vose"
    project_path.write_text(
        json.dumps({
            "tempo": 135.0,
            "current_voice_id": "voice-b",
            "current_voice": "Voice B",
            "tracks": [{
                "name": "Loaded",
                "type": "vocal",
                "notes": [],
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(project_io_module.QMessageBox, "critical", lambda *_a, **_k: None)

    assert ProjectIOMixin.load_json_project(window, str(project_path)) is True
    assert window.current_voice_id == "voice-b"
    assert window.current_voice == "Voice B"
    assert window.voice_gallery.cards["voice-b"].selected is True


def test_project_load_keeps_current_voice_when_saved_voice_is_missing(tmp_path, monkeypatch):
    window = _ProjectWindow()
    _bind_project_mixin_methods(window)
    project_path = tmp_path / "missing-voice.vose"
    project_path.write_text(
        json.dumps({
            "tempo": 135.0,
            "current_voice_id": "voice-not-installed",
            "current_voice": "Removed Voice",
            "tracks": [{
                "name": "Loaded",
                "type": "vocal",
                "notes": [],
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(project_io_module.QMessageBox, "critical", lambda *_a, **_k: None)

    assert ProjectIOMixin.load_json_project(window, str(project_path)) is True
    assert window.current_voice_id == "voice-a"
    assert window.current_voice == "Voice A"
    assert any("保存時の音源が未検出" in msg for msg in window._status_bar.messages)


def test_project_undo_snapshot_restores_voice_identity():
    window = _ProjectWindow()
    _bind_project_mixin_methods(window)
    before = ProjectIOMixin._capture_project_edit_state(window)

    window.current_voice_id = "voice-b"
    window.current_voice = "Voice B"
    ProjectIOMixin._restore_project_edit_state(window, before)

    assert window.current_voice_id == "voice-a"
    assert window.current_voice == "Voice A"
    assert window.voice_gallery.cards["voice-a"].selected is True



def test_project_load_resolves_relative_audio_paths_from_project_folder(tmp_path, monkeypatch):
    window = _ProjectWindow()
    _bind_project_mixin_methods(window)
    project_path = tmp_path / "project.vose"
    project_path.write_text(
        json.dumps({
            "tempo": 120.0,
            "tracks": [{
                "name": "Backing",
                "type": "wave",
                "audio_path": "media/backing.wav",
                "notes": [],
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(project_io_module.QMessageBox, "critical", lambda *_a, **_k: None)

    assert ProjectIOMixin.load_json_project(window, str(project_path)) is True
    assert window.tracks[0].audio_path == str((tmp_path / "media" / "backing.wav").resolve())


def test_project_history_snapshot_does_not_deepcopy_entire_note_tree():
    window = _ProjectWindow()
    note = window.tracks[0].notes[0]

    snapshot = ProjectIOMixin._capture_project_edit_state(window)

    window.tracks[0].name = "Changed after snapshot"
    window.tracks[0].notes = []

    assert snapshot["tracks"][0].name == "Current"
    assert len(snapshot["tracks"][0].notes) == 1
    assert snapshot["tracks"][0].notes[0] is note
