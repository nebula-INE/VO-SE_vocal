import json
import os

import pytest

from modules.utils.project_autosave import (
    AutosaveError,
    ProjectAutosaveStore,
    default_autosave_path,
)


def test_autosave_round_trip_preserves_project_and_source_metadata(tmp_path):
    path = tmp_path / "recovery.json"
    store = ProjectAutosaveStore(path)
    project = {
        "tempo": 128.0,
        "tracks": [{
            "name": "Vocal",
            "type": "vocal",
            "notes": [{"note_number": 64, "start_time": 0.0, "duration": 0.5}],
        }],
    }

    written = store.write(
        project,
        source_path=str(tmp_path / "song.vose"),
        was_saved=True,
    )
    restored = store.read()

    assert restored is not None
    assert restored["format"] == ProjectAutosaveStore.FORMAT_ID
    assert restored["version"] == ProjectAutosaveStore.FORMAT_VERSION
    assert restored["saved_at"] == written["saved_at"]
    assert restored["source_path"] == str((tmp_path / "song.vose").resolve())
    assert restored["was_saved"] is True
    assert restored["project"] == project


def test_autosave_write_replaces_existing_file_atomically(tmp_path, monkeypatch):
    path = tmp_path / "recovery.json"
    store = ProjectAutosaveStore(path)
    store.write({"tempo": 120.0})
    original = path.read_text(encoding="utf-8")

    def fail_replace(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(AutosaveError, match="書き込めません"):
        store.write({"tempo": 140.0})

    assert path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".vose-autosave-*.tmp")) == []


def test_autosave_rejects_corrupt_or_unknown_payload(tmp_path):
    path = tmp_path / "recovery.json"
    store = ProjectAutosaveStore(path)

    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(AutosaveError, match="読み込めません"):
        store.read()

    path.write_text(
        json.dumps({"format": "other", "version": 1, "project": {}}),
        encoding="utf-8",
    )
    with pytest.raises(AutosaveError, match="形式が対応していません"):
        store.read()


def test_autosave_rejects_non_finite_project_values(tmp_path):
    store = ProjectAutosaveStore(tmp_path / "recovery.json")
    with pytest.raises(AutosaveError, match="JSONに変換できません"):
        store.write({"tempo": float("nan")})


def test_autosave_fingerprint_is_stable_and_sensitive_to_edits():
    project = {"tempo": 120.0, "tracks": [{"name": "Vocal", "notes": []}]}

    first = ProjectAutosaveStore.fingerprint(project)
    same = ProjectAutosaveStore.fingerprint({"tracks": [{"notes": [], "name": "Vocal"}], "tempo": 120.0})
    changed = ProjectAutosaveStore.fingerprint({"tempo": 121.0, "tracks": [{"name": "Vocal", "notes": []}]})

    assert first == same
    assert first != changed


def test_autosave_clear_removes_recovery_file(tmp_path):
    store = ProjectAutosaveStore(tmp_path / "recovery.json")
    store.write({"tempo": 120.0})

    store.clear()

    assert store.read() is None


def test_default_autosave_path_honors_user_override(tmp_path, monkeypatch):
    override = tmp_path / "custom" / "recovery.json"
    monkeypatch.setenv("VOSE_AUTOSAVE_PATH", str(override))

    assert default_autosave_path() == override


from modules.data.data_models import NoteEvent
from modules.gui.mixins.project_io_mixin import ProjectIOMixin


class _AutosaveTrack:
    def __init__(self):
        self.name = "Vocal"
        self.track_type = "vocal"
        self.audio_path = ""
        self.volume = 1.0
        self.pan = 0.0
        self.is_muted = False
        self.is_solo = False
        self.engine_type = "Aural"
        self.color_label = ""
        self.voicebank_id = "voice-test"
        self.notes = []


class _AutosaveStatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, message, *_args):
        self.messages.append(message)


class _AutosaveWindow(ProjectIOMixin):
    def __init__(self):
        self.tracks = [_AutosaveTrack()]
        self.current_track_idx = 0
        self.timeline_widget = type(
            "Timeline",
            (),
            {
                "notes_list": [NoteEvent(note_number=60, start_time=0.0, duration=0.5)],
                "tempo": 120.0,
            },
        )()
        self.graph_editor_widget = type(
            "Graph",
            (),
            {"all_parameters": {"Pitch": [], "Gender": [], "Tension": [], "Breath": []}},
        )()
        self.current_playback_time = 0.0
        self.current_voice_id = "voice-test"
        self.current_voice = "Test Voice"
        self.current_project_path = ""
        self.is_playing = False
        self.is_playing_state = False
        self.is_recording = False
        self.audio_mixer = None
        self._status_bar = _AutosaveStatusBar()

    def statusBar(self):
        return self._status_bar


def test_project_mixin_autosaves_only_changed_project_data(tmp_path):
    window = _AutosaveWindow()
    store = ProjectAutosaveStore(tmp_path / "recovery.json")
    window._project_autosave_store = store
    source_path, _ = window._project_autosave_location()
    window._last_project_autosave_fingerprint = store.fingerprint(
        window._build_project_data(source_path)
    )

    assert window._run_project_autosave() is False
    assert store.read() is None

    window.timeline_widget.notes_list.append(
        NoteEvent(note_number=64, start_time=0.5, duration=0.5, lyric="い")
    )
    assert window._run_project_autosave() is True

    record = store.read()
    assert record is not None
    assert record["was_saved"] is False
    assert len(record["project"]["tracks"][0]["notes"]) == 2
    assert record["project"]["tempo"] == 120.0
    assert record["project"]["current_voice_id"] == "voice-test"


def test_project_mixin_skips_autosave_while_transport_is_active(tmp_path):
    window = _AutosaveWindow()
    store = ProjectAutosaveStore(tmp_path / "recovery.json")
    window._project_autosave_store = store
    window.timeline_widget.notes_list.append(
        NoteEvent(note_number=65, start_time=0.5, duration=0.5)
    )
    window.is_playing = True

    assert window._run_project_autosave() is False
    assert store.read() is None


def test_explicit_project_save_clears_recovery_snapshot(tmp_path, monkeypatch):
    import modules.gui.mixins.project_io_mixin as project_io_module

    window = _AutosaveWindow()
    store = ProjectAutosaveStore(tmp_path / "recovery.json")
    window._project_autosave_store = store
    store.write({"tempo": 99.0})
    target = tmp_path / "saved-project.vose"
    monkeypatch.setattr(
        project_io_module.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(target), "VO-SE Project (*.vose)"),
    )

    assert window.on_save_project_clicked() is True
    assert target.is_file()
    assert store.read() is None
    assert window.current_project_path == str(target.resolve())
