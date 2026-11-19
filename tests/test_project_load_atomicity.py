import json
from pathlib import Path
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
        self.voicebank_id = ""
        self.voicebank_name = ""
        self.web_track_id = ""
        self.effects = []


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


class _EffectsPanel:
    def __init__(self):
        self.effects = []

    def get_effect_chain(self):
        return [
            {
                "id": item["id"],
                "type": item["type"],
                "enabled": item["enabled"],
                "params": dict(item["params"]),
            }
            for item in self.effects
        ]

    def set_effect_chain(self, chain):
        self.effects = [
            {
                "id": item["id"],
                "type": item["type"],
                "enabled": item["enabled"],
                "params": dict(item["params"]),
            }
            for item in chain
        ]


class _AudioMixer:
    def __init__(self):
        self.effect_chain = []

    def set_effect_chain(self, chain):
        self.effect_chain = [
            {
                "id": item["id"],
                "type": item["type"],
                "enabled": item["enabled"],
                "params": dict(item["params"]),
            }
            for item in chain
        ]

    def restore_tracks_preserving_sources(self, tracks):
        self.restored_tracks = list(tracks)


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
        self.effects_panel = _EffectsPanel()
        self.audio_mixer = _AudioMixer()
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
    window.tracks[0].voicebank_id = "voice-a"
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
    assert saved["tracks"][0]["voicebank_id"] == "voice-a"
    assert window.current_project_path == str(project_path.resolve())


def test_save_shortcut_reuses_current_project_path(tmp_path, monkeypatch):
    window = _ProjectWindow()
    project_path = tmp_path / "existing-project.vose"
    window.current_project_path = str(project_path)
    window.on_save_project_clicked = lambda path=None: ProjectIOMixin.on_save_project_clicked(window, path)
    monkeypatch.setattr(
        project_io_module.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Save dialog should not open")),
    )
    assert ProjectIOMixin.save_file_dialog_and_save_midi(window) is True
    assert project_path.is_file()


def test_project_load_remembers_absolute_path(tmp_path, monkeypatch):
    window = _ProjectWindow()
    _bind_project_mixin_methods(window)
    project_path = tmp_path / "remember-path.vose"
    project_path.write_text(
        json.dumps({
            "tempo": 121.0,
            "tracks": [{"name": "Loaded", "type": "vocal", "notes": []}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(project_io_module.QMessageBox, "critical", lambda *_a, **_k: None)
    assert ProjectIOMixin.load_json_project(window, str(project_path)) is True
    assert window.current_project_path == str(project_path.resolve())


def test_apply_project_tempo_updates_all_desktop_surfaces():
    class _TempoInput:
        def __init__(self):
            self.value = ""
            self.signals_blocked = False
        def blockSignals(self, blocked):
            previous = self.signals_blocked
            self.signals_blocked = blocked
            return previous
        def setText(self, value):
            self.value = value

    window = _ProjectWindow()
    window.tempo_input = _TempoInput()
    engine_tempos = []
    window.vo_se_engine = SimpleNamespace(set_tempo=engine_tempos.append)
    window.update_scrollbar_range = lambda: None
    applied = ProjectIOMixin._apply_project_tempo(window, 137.5)
    assert applied == 137.5
    assert window.timeline_widget.tempo == 137.5
    assert window.graph_editor_widget.tempo == 137.5
    assert window.tempo_input.value == "137.5"
    assert engine_tempos == [137.5]


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
                "voicebank_id": "voice-b",
                "notes": [],
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(project_io_module.QMessageBox, "critical", lambda *_a, **_k: None)

    assert ProjectIOMixin.load_json_project(window, str(project_path)) is True
    assert window.tracks[0].voicebank_id == "voice-b"
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


def test_legacy_project_load_uses_lightweight_track_container_clones():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "modules" / "gui" / "mixins" / "project_io_mixin.py"
    ).read_text(encoding="utf-8")
    loader = source.split("def load_json_project(", 1)[1].split(
        "def load_midi_file_from_path(", 1
    )[0]
    assert "_clone_track_collection(existing_tracks)" in loader
    assert "deepcopy(existing_tracks)" not in loader



def test_project_save_persists_the_master_effect_chain(tmp_path, monkeypatch):
    window = _ProjectWindow()
    window.effects_panel.set_effect_chain([{
        "id": "compressor-main",
        "type": "compressor",
        "enabled": True,
        "params": {"threshold_db": -21.0, "ratio": 5.0},
    }])
    project_path = tmp_path / "effects-project.vose"
    monkeypatch.setattr(
        project_io_module.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(project_path), "VO-SE Project (*.vose)"),
    )

    assert ProjectIOMixin.on_save_project_clicked(window) is True
    saved = json.loads(project_path.read_text(encoding="utf-8"))
    assert saved["effect_chain"][0]["type"] == "compressor"
    assert saved["effect_chain"][0]["params"]["threshold_db"] == -21.0
    assert saved["effect_chain"][0]["params"]["ratio"] == 5.0


def test_project_load_restores_master_effect_chain_into_panel_and_mixer(tmp_path, monkeypatch):
    window = _ProjectWindow()
    _bind_project_mixin_methods(window)
    effect_chain = [{
        "id": "project-eq",
        "type": "eq",
        "enabled": True,
        "params": {"frequency_hz": 2400.0, "gain_db": -3.0, "q": 1.2},
    }]
    project_path = tmp_path / "effects-load.vose"
    project_path.write_text(
        json.dumps({
            "tempo": 120.0,
            "effect_chain": effect_chain,
            "tracks": [{"name": "Loaded", "type": "vocal", "notes": []}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(project_io_module.QMessageBox, "critical", lambda *_a, **_k: None)

    assert ProjectIOMixin.load_json_project(window, str(project_path)) is True
    assert window.effects_panel.get_effect_chain() == effect_chain
    assert window.audio_mixer.effect_chain == effect_chain


def test_project_load_rejects_invalid_master_effect_before_commit(tmp_path, monkeypatch):
    window = _ProjectWindow()
    original_tracks = list(window.tracks)
    project_path = tmp_path / "invalid-effect.vose"
    project_path.write_text(
        json.dumps({
            "tempo": 135.0,
            "effect_chain": [{
                "id": "invalid-compressor",
                "type": "compressor",
                "enabled": True,
                "params": {"ratio": 99.0},
            }],
            "tracks": [{"name": "Would replace", "type": "vocal", "notes": []}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(project_io_module.QMessageBox, "critical", lambda *_a, **_k: None)

    assert ProjectIOMixin.load_json_project(window, str(project_path)) is False
    assert window.tracks == original_tracks
    assert window.effects_panel.get_effect_chain() == []
    assert window.audio_mixer.effect_chain == []


def test_project_undo_snapshot_restores_master_effect_chain():
    window = _ProjectWindow()
    _bind_project_mixin_methods(window)
    before_chain = [{
        "id": "saved-reverb",
        "type": "reverb",
        "enabled": True,
        "params": {
            "room_size": 0.45,
            "damping": 0.5,
            "wet": 0.25,
            "dry": 0.8,
            "width": 0.75,
        },
    }]
    window.effects_panel.set_effect_chain(before_chain)
    before = ProjectIOMixin._capture_project_edit_state(window)
    window.effects_panel.set_effect_chain([])
    window.audio_mixer.set_effect_chain([])

    ProjectIOMixin._restore_project_edit_state(window, before)

    assert window.effects_panel.get_effect_chain() == before_chain
    assert window.audio_mixer.effect_chain == before_chain
    assert window.audio_mixer.restored_tracks == window.tracks



def test_legacy_project_without_track_voicebank_uses_saved_global_voice(tmp_path, monkeypatch):
    window = _ProjectWindow()
    _bind_project_mixin_methods(window)
    project_path = tmp_path / "legacy-global-voice.vose"
    project_path.write_text(
        json.dumps({
            "tempo": 120.0,
            "current_voice_id": "voice-b",
            "current_voice": "Voice B",
            "tracks": [{
                "name": "Legacy vocal",
                "type": "vocal",
                "notes": [],
            }],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(project_io_module.QMessageBox, "critical", lambda *_a, **_k: None)

    assert ProjectIOMixin.load_json_project(window, str(project_path)) is True
    assert window.tracks[0].voicebank_id == "voice-b"
    assert window.current_voice_id == "voice-b"



def test_project_load_restores_per_track_effect_chain(tmp_path, monkeypatch):
    window = _ProjectWindow()
    _bind_project_mixin_methods(window)
    project_path = tmp_path / "track-effects.vose"
    effect_chain = [{
        "id": "vocal-eq-1",
        "type": "eq",
        "enabled": True,
        "params": {"frequency_hz": 2400.0, "gain_db": 2.5, "q": 1.1},
    }]
    project_path.write_text(json.dumps({
        "tempo": 120.0,
        "tracks": [{
            "name": "Vocal FX",
            "type": "vocal",
            "notes": [],
            "effects": effect_chain,
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(project_io_module.QMessageBox, "critical", lambda *_a, **_k: None)

    assert ProjectIOMixin.load_json_project(window, str(project_path)) is True
    assert window.tracks[0].effects == effect_chain


def test_project_load_rejects_invalid_per_track_effect_before_commit(tmp_path, monkeypatch):
    window = _ProjectWindow()
    project_path = tmp_path / "invalid-track-effect.vose"
    project_path.write_text(json.dumps({
        "tempo": 120.0,
        "tracks": [{
            "name": "Invalid FX",
            "type": "vocal",
            "notes": [],
            "effects": [{
                "id": "bad-eq",
                "type": "eq",
                "enabled": True,
                "params": {"gain_db": 80.0},
            }],
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(project_io_module.QMessageBox, "critical", lambda *_a, **_k: None)

    assert ProjectIOMixin.load_json_project(window, str(project_path)) is False
    assert window.tracks[0].name == "Current"

def test_project_save_persists_each_track_effect_chain(tmp_path):
    window = _ProjectWindow()
    window.tracks[0].effects = [{
        "id": "voice-comp",
        "type": "compressor",
        "enabled": True,
        "params": {
            "threshold_db": -18.0,
            "ratio": 3.0,
            "attack_ms": 10.0,
            "release_ms": 100.0,
            "makeup_gain_db": 0.0,
        },
    }]
    project_path = tmp_path / "track-effects-save.vose"

    assert ProjectIOMixin.on_save_project_clicked(window, str(project_path)) is True
    saved = json.loads(project_path.read_text(encoding="utf-8"))
    assert saved["tracks"][0]["effects"] == window.tracks[0].effects



def test_project_history_snapshot_keeps_track_effects_detached():
    from modules.gui.mixins.project_io_mixin import _clone_track_collection

    window = _ProjectWindow()
    window.tracks[0].effects = [{
        "id": "snapshot-eq",
        "type": "eq",
        "enabled": True,
        "params": {"frequency_hz": 1500.0, "gain_db": 2.0, "q": 1.0},
    }]
    snapshot = _clone_track_collection(window.tracks)
    window.tracks[0].effects[0]["params"]["gain_db"] = 12.0
    assert snapshot[0].effects[0]["params"]["gain_db"] == 2.0


def test_selected_track_effects_are_not_serialized_as_the_master_chain():
    from modules.gui.mixins.project_io_mixin import _get_master_effect_chain

    window = _ProjectWindow()
    master_chain = [{
        "id": "master-limiter",
        "type": "compressor",
        "enabled": True,
        "params": {
            "threshold_db": -6.0,
            "ratio": 8.0,
            "attack_ms": 2.0,
            "release_ms": 80.0,
            "makeup_gain_db": 0.0,
        },
    }]
    selected_track_chain = [{
        "id": "track-room",
        "type": "reverb",
        "enabled": True,
        "params": {
            "room_size": 0.4,
            "damping": 0.5,
            "wet": 0.2,
            "dry": 0.9,
            "width": 0.8,
        },
    }]
    window.effects_panel.get_scope = lambda: "track"
    window.effects_panel.get_effect_chain = lambda: selected_track_chain
    window.audio_mixer.get_effect_chain = lambda: master_chain

    assert _get_master_effect_chain(window) == master_chain
    assert _get_master_effect_chain(window) != selected_track_chain


def test_track_effect_scope_without_master_getter_never_falls_back_to_track_chain():
    from modules.gui.mixins.project_io_mixin import _get_master_effect_chain

    window = _ProjectWindow()
    track_chain = [{
        "id": "selected-eq",
        "type": "eq",
        "enabled": True,
        "params": {"frequency_hz": 1800.0, "gain_db": 2.0, "q": 1.0},
    }]
    window.effects_panel.get_scope = lambda: "track"
    window.effects_panel.get_effect_chain = lambda: track_chain

    assert _get_master_effect_chain(window) == []


def test_project_save_keeps_master_chain_separate_when_track_scope_is_selected(tmp_path):
    window = _ProjectWindow()
    project_path = tmp_path / "separate-master-track.vose"
    master_chain = [{
        "id": "master-eq",
        "type": "eq",
        "enabled": True,
        "params": {"frequency_hz": 1000.0, "gain_db": -1.0, "q": 0.8},
    }]
    track_chain = [{
        "id": "track-reverb",
        "type": "reverb",
        "enabled": True,
        "params": {
            "room_size": 0.45,
            "damping": 0.5,
            "wet": 0.2,
            "dry": 0.9,
            "width": 0.75,
        },
    }]
    window.tracks[0].effects = track_chain
    window.effects_panel.get_scope = lambda: "track"
    window.effects_panel.get_effect_chain = lambda: track_chain
    window.audio_mixer.get_effect_chain = lambda: master_chain

    assert ProjectIOMixin.on_save_project_clicked(window, str(project_path)) is True
    saved = json.loads(project_path.read_text(encoding="utf-8"))

    assert saved["effect_chain"] == master_chain
    assert saved["tracks"][0]["effects"] == track_chain


def test_shared_web_vose_fixture_loads_all_track_state_on_desktop(tmp_path, monkeypatch):
    fixture_path = Path(__file__).resolve().parents[1] / "specs" / "vose_project_roundtrip.json"
    project_data = json.loads(fixture_path.read_text(encoding="utf-8"))
    project_path = tmp_path / "shared-parity.vose"
    project_path.write_text(json.dumps(project_data, ensure_ascii=False), encoding="utf-8")

    window = _ProjectWindow()
    _bind_project_mixin_methods(window)
    monkeypatch.setattr(project_io_module.QMessageBox, "critical", lambda *_a, **_k: None)

    assert ProjectIOMixin.load_json_project(window, str(project_path)) is True
    assert len(window.tracks) == 2

    lead, backing = window.tracks
    assert (lead.name, lead.track_type, lead.voicebank_id) == (
        "Lead Vocal", "vocal", "voice-a"
    )
    assert lead.voicebank_name == "Voice A"
    assert lead.web_track_id == "track_lead"
    assert lead.volume == 0.72
    assert lead.pan == -0.25
    assert lead.is_muted is False
    assert lead.is_solo is True
    assert lead.effects == project_data["tracks"][0]["effects"]
    assert len(lead.notes) == 1
    assert lead.notes[0].note_number == 64
    assert lead.notes[0].start_time == 0.25
    assert lead.notes[0].duration == 0.5
    assert lead.notes[0]._ust_flags == "g-5"
    assert lead.notes[0]._ust_pbm == "0"
    assert lead.notes[0].vibrato_depth == 0.15

    assert (backing.name, backing.track_type) == ("Backing Audio", "wave")
    assert backing.audio_path == str((tmp_path / "backing.wav").resolve())
    assert backing.volume == 0.35
    assert backing.pan == 0.6
    assert backing.is_muted is True
    assert backing.effects == project_data["tracks"][1]["effects"]

    assert window.current_track_idx == 1
    assert window.current_voice_id == "voice-a"
    assert window.current_voice == "Voice A"
    assert window.timeline_widget.tempo == 120.0
    assert window.current_playback_time == 1.25
    assert window.audio_mixer.effect_chain == project_data["effect_chain"]
    assert window.graph_editor_widget.all_parameters["Pitch"][1].value == 12.0

    resaved_path = tmp_path / "desktop-resaved.vose"
    assert ProjectIOMixin.on_save_project_clicked(window, str(resaved_path)) is True
    resaved = json.loads(resaved_path.read_text(encoding="utf-8"))
    assert resaved["tracks"][0]["web_track_id"] == "track_lead"
    assert resaved["tracks"][0]["voicebank_name"] == "Voice A"
    assert resaved["tracks"][0]["notes"][0]["_ust_flags"] == "g-5"
    assert resaved["tracks"][0]["notes"][0]["_ust_pbm"] == "0"
    assert resaved["tracks"][0]["notes"][0]["vibrato_depth"] == 0.15


def test_effect_slider_history_is_coalesced_and_flushed_before_undo(qtbot):
    from PySide6.QtCore import QObject
    from modules.gui.main_window import MainWindow

    class FakeHistory:
        def __init__(self):
            self.undo_called = False

        def undo(self):
            self.undo_called = True

    class FakeStatusBar:
        def showMessage(self, *_args):
            return None

    class FakeWindow(QObject):
        def __init__(self):
            super().__init__()
            self.recorded = None
            self._pending_effect_history_before = None
            self.history = FakeHistory()
            self._capture_project_edit_state = lambda: {"track_effects": ["final"]}
            self._record_project_state_edit = (
                lambda before, after, description:
                    setattr(self, "recorded", (before, after, description))
            )
            self._flush_pending_effect_history = (
                MainWindow._flush_pending_effect_history.__get__(self, FakeWindow)
            )
            self.statusBar = lambda: FakeStatusBar()

    window = FakeWindow()
    before = {"track_effects": ["initial"]}
    # Repeated slider changes share the original state snapshot and one timer.
    MainWindow._queue_effect_history_snapshot(window, before)
    timer = window._effect_history_timer
    MainWindow._queue_effect_history_snapshot(window, {"track_effects": ["intermediate"]})
    assert window._effect_history_timer is timer
    assert timer.isActive()

    # The actual Undo entry point must flush the pending gesture before undoing.
    MainWindow.undo(window)

    assert window.history.undo_called is True
    assert window._pending_effect_history_before is None
    assert window.recorded == (
        before,
        {"track_effects": ["final"]},
        "エフェクト設定",
    )
