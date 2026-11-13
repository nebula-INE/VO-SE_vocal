import json

import pytest

from modules.gui.mixins.project_io_mixin import (
    _ExternalProjectLoadWorker,
    _parse_external_project_file,
)


def test_external_json_project_parsing_returns_neutral_payload(tmp_path):
    path = tmp_path / "large-project.json"
    expected = {
        "tempo": 132.5,
        "tracks": [
            {
                "name": "Lead",
                "type": "vocal",
                "notes": [
                    {
                        "note_number": 60,
                        "start_time": 0.0,
                        "duration": 0.5,
                        "lyric": "あ",
                    }
                ],
            }
        ],
    }
    path.write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")

    payload = _parse_external_project_file(str(path))

    assert payload["format"] == "json"
    assert payload["data"] == expected
    assert len(payload["prepared_note_tracks"]) == 1
    assert payload["prepared_note_tracks"][0][0].lyric == "あ"


def test_external_project_parser_rejects_invalid_json_root(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="ルート"):
        _parse_external_project_file(str(path))


def test_external_project_parser_rejects_unsupported_extension(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("not a project", encoding="utf-8")

    with pytest.raises(ValueError, match="対応していない"):
        _parse_external_project_file(str(path))


def test_project_load_worker_signals_parsed_payload_and_sets_completion(tmp_path):
    path = tmp_path / "worker.json"
    path.write_text(json.dumps({"tempo": 120.0}), encoding="utf-8")
    worker = _ExternalProjectLoadWorker(str(path))
    completed = []
    failed = []
    worker.signals.completed.connect(
        lambda file_path, payload: completed.append((file_path, payload))
    )
    worker.signals.failed.connect(
        lambda file_path, error: failed.append((file_path, error))
    )

    worker.run()

    assert worker.done.is_set()
    assert failed == []
    assert completed[0][0] == str(path)
    assert completed[0][1]["data"] == {"tempo": 120.0}


def test_external_project_dispatcher_uses_background_worker():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "modules" / "gui" / "mixins" / "project_io_mixin.py").read_text(
        encoding="utf-8"
    )
    dispatcher = source.split(
        "def import_external_project(self: Any) -> None:", 1
    )[1].split(
        "def load_external_project_async", 1
    )[0]
    async_loader = source.split(
        "def load_external_project_async(self: Any, file_path: str) -> bool:", 1
    )[1].split(
        "def _on_external_project_loaded", 1
    )[0]

    assert "self.load_external_project_async(file_path)" in dispatcher
    assert "_ExternalProjectLoadWorker(file_path)" in async_loader
    assert "QThreadPool.globalInstance().start" in async_loader



def test_external_json_worker_rejects_invalid_note_before_gui_application(tmp_path):
    path = tmp_path / "invalid-note.json"
    path.write_text(
        json.dumps({
            "tempo": 120.0,
            "tracks": [{
                "name": "Lead",
                "type": "vocal",
                "notes": [{
                    "note_number": 60,
                    "start_time": 0.0,
                    "duration": float("nan"),
                    "lyric": "あ",
                }],
            }],
        }),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="範囲外"):
        _parse_external_project_file(str(path))


def test_external_json_worker_prepares_legacy_single_track_notes(tmp_path):
    path = tmp_path / "legacy.json"
    expected = {
        "tempo": 120.0,
        "notes": [{
            "note_number": 64,
            "start_time": 0.5,
            "duration": 0.25,
            "lyric": "い",
        }],
    }
    path.write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")

    payload = _parse_external_project_file(str(path))

    assert payload["prepared_note_tracks"] is None
    assert payload["prepared_legacy_notes"][0].note_number == 64


def test_external_midi_worker_converts_notes_and_preserves_track_identity(
    tmp_path, monkeypatch
):
    from modules.data.data_models import NoteEvent
    import modules.data.midi_manager as midi_manager

    path = tmp_path / "multitrack.mid"
    path.write_bytes(b"test fixture")
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
                "note_number": 67,
                "start_time": 0.25,
                "duration": 0.5,
                "lyric": "い",
                "_midi_track_index": 2,
                "_midi_track_name": "Harmony",
            },
        ],
    )
    # This test stubs the MIDI parser and intentionally writes a non-MIDI fixture.
    # Stub the new metadata read as well, so this remains focused on track mapping.
    monkeypatch.setattr(midi_manager, "get_midi_initial_tempo", lambda _path: 137.5)

    payload = _parse_external_project_file(str(path))

    assert payload["format"] == "midi"
    assert isinstance(payload["notes_by_track"][0][0], NoteEvent)
    assert payload["notes_by_track"][0][0].lyric == "あ"
    assert payload["notes_by_track"][2][0].note_number == 67
    assert payload["names_by_track"] == {0: "Lead", 2: "Harmony"}
    assert payload["tempo_bpm"] == pytest.approx(137.5)


def test_external_vose_project_is_loaded_as_native_json_project(tmp_path):
    path = tmp_path / "saved-project.vose"
    expected = {
        "tempo": 124.0,
        "tracks": [{
            "name": "Lead",
            "type": "vocal",
            "notes": [{
                "note_number": 60,
                "start_time": 0.0,
                "duration": 0.5,
                "lyric": "あ",
            }],
        }],
    }
    path.write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")

    payload = _parse_external_project_file(str(path))

    assert payload["format"] == "json"
    assert payload["data"] == expected
    assert payload["prepared_note_tracks"][0][0].lyric == "あ"


def test_legacy_load_file_from_path_routes_to_async_project_loader():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "modules" / "gui" / "mixins" / "project_io_mixin.py"
    ).read_text(encoding="utf-8")
    loader = source.split("def load_file_from_path(", 1)[1].split(
        "def _parse_midi", 1
    )[0]
    assert "return self.load_external_project_async(filepath)" in loader



def test_voicebank_drop_dispatches_to_background_installer():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "modules" / "gui" / "mixins" / "project_io_mixin.py"
    ).read_text(encoding="utf-8")
    drop_handler = source.split("def dropEvent(self: Any, event):", 1)[1].split(
        "def load_file_from_path", 1
    )[0]
    assert "self.import_voice_bank(file_path, source_type=source_type)" in drop_handler
    assert "urlretrieve(" not in drop_handler


def test_voicebank_import_worker_downloads_installs_and_loads_models_off_ui_thread():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "modules" / "gui" / "mixins" / "project_io_mixin.py"
    ).read_text(encoding="utf-8")
    worker = source.split("class _VoiceBankImportWorker", 1)[1].split(
        "class _ExternalProjectLoadSignals", 1
    )[0]
    assert "urlretrieve(self.source_path, temporary_path)" in worker
    assert "installed_name = install(zip_path)" in worker
    assert 'getattr(self.manager, "install_voice_from_zip", None)' in worker
    assert "load_model(aural_model)" in worker
    assert "load_model(standard_model)" in worker
    assert "self.completed.emit({" in worker


def test_voicebank_import_entrypoint_starts_worker_and_reports_completion():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "modules" / "gui" / "mixins" / "project_io_mixin.py"
    ).read_text(encoding="utf-8")
    handler = source.split("def import_voice_bank(", 1)[1].split(
        "def generate_and_save_oto", 1
    )[0]
    assert "_VoiceBankImportWorker(manager, zip_path, source_type, self)" in handler
    assert "worker.start()" in handler
    assert "self._on_voice_bank_import_completed" in handler
    assert 'getattr(gallery, "_scan_worker", None)' in handler


def test_rendered_wav_preview_uses_background_mixer_start():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "modules" / "gui" / "main_window.py"
    ).read_text(encoding="utf-8")
    play_handler = source.split("def play_audio(self, path: str) -> None:", 1)[1].split(
        "def _check_for_updates", 1
    )[0]
    assert 'self._start_audio_mixer_command(' in play_handler
    assert 'play_file(' not in play_handler


def test_ust_export_recreates_silent_gaps_without_display_nodes(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from modules.data.data_models import NoteEvent
    from modules.data.ust_parser import UstConverter, UstParser
    from modules.gui.mixins import project_io_mixin
    from modules.gui.mixins.project_io_mixin import ProjectIOMixin

    output_path = tmp_path / "gap-roundtrip.ust"
    monkeypatch.setattr(
        project_io_mixin.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(output_path), "UTAU プロジェクト (*.ust)"),
    )

    class FakeStatusBar:
        def showMessage(self, *_args, **_kwargs):
            pass

    window = SimpleNamespace(
        timeline_widget=SimpleNamespace(
            tempo=120.0,
            notes_list=[
                NoteEvent(note_number=60, start_time=0.0, duration=0.5, lyric="あ"),
                NoteEvent(note_number=62, start_time=1.0, duration=1.0, lyric="い"),
            ],
        ),
        statusBar=lambda: FakeStatusBar(),
    )

    window.timeline_widget.notes_list[1]._ust_tempo = 60.0

    ProjectIOMixin.export_as_ust(window)

    exported = output_path.read_text(encoding="cp932")
    assert "Lyric=R" in exported
    assert "Tempo=60.00" in exported

    parsed = UstParser().load(str(output_path))
    notes = UstConverter.to_note_dicts(parsed)
    assert [note["lyric"] for note in notes] == ["あ", "い"]
    assert notes[1]["start_time"] == pytest.approx(1.0)
    assert notes[1]["duration"] == pytest.approx(1.0)
    assert notes[1]["_ust_tempo"] == pytest.approx(60.0)


def test_ust_export_restores_tie_sections_and_section_local_settings(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from modules.data.data_models import NoteEvent
    from modules.data.ust_parser import UstConverter, UstParser
    from modules.gui.mixins import project_io_mixin
    from modules.gui.mixins.project_io_mixin import ProjectIOMixin

    output_path = tmp_path / "tie-roundtrip.ust"
    monkeypatch.setattr(
        project_io_mixin.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(output_path), "UTAU プロジェクト (*.ust)"),
    )

    class FakeStatusBar:
        def showMessage(self, *_args, **_kwargs):
            pass

    first = NoteEvent(
        note_number=60, start_time=0.0, duration=0.5 + (2.0 / 3.0), lyric="あ"
    )
    first._ust_tempo = 120.0
    first._ust_base_duration = 0.5
    first._ust_base_portamento = {
        "pbs": "0;0",
        "pbw": "50",
        "pby": "10",
        "pbm": "s,j",
    }
    first._ust_pbs = "0;0"
    first._ust_pbw = "50"
    first._ust_pby = "10"
    first._ust_pbm = "s,j"
    first._ust_tie_sections = [{
        "length": 480,
        "tempo": 90.0,
        "note_num": 60,
        "intensity": 123.0,
        "modulation": 17.0,
        "flags": "g+3",
        "flags_present": True,
        "pbs": "-25;50",
        "pbw": "60",
        "pby": "5",
        "pbm": "r",
        "vibrato": {
            "length": 70.0,
            "cycle": 190.0,
            "depth": 38.0,
            "fade_in": 11.0,
            "fade_out": 13.0,
            "phase": 4.0,
            "height": 1.0,
        },
        "pre_utterance": 12.0,
        "overlap": 4.0,
    }]
    second = NoteEvent(
        note_number=62,
        start_time=1.0 + (1.0 / 6.0),
        duration=0.5,
        lyric="い",
    )
    second._ust_tempo = 90.0

    window = SimpleNamespace(
        timeline_widget=SimpleNamespace(tempo=120.0, notes_list=[first, second]),
        statusBar=lambda: FakeStatusBar(),
    )

    ProjectIOMixin.export_as_ust(window)

    exported = output_path.read_text(encoding="cp932")
    assert "Lyric=+" in exported
    assert "Tempo=90.00" in exported
    assert "Intensity=123" in exported
    assert "Modulation=17" in exported
    assert "Flags=g+3" in exported
    assert "VBR=70.0,190.0,38.0,11.0,13.0,4.0,1.0" in exported
    assert "PreUtterance=12.000" in exported
    assert "VoiceOverlap=4.000" in exported
    assert "PBS=-25;50" in exported
    assert "PBW=60" in exported
    assert "PBY=5" in exported
    assert "PBM=s,j" in exported
    assert "PBM=r" in exported
    assert exported.count("PBS=") == 2
    assert "PBS=0;0" in exported
    assert "PBS=-25;50" in exported

    parsed = UstParser().load(str(output_path))
    notes = UstConverter.to_note_dicts(parsed)
    assert [note["lyric"] for note in notes] == ["あ", "い"]
    assert notes[0]["duration"] == pytest.approx(1.0 + (1.0 / 6.0))
    assert notes[1]["start_time"] == pytest.approx(1.0 + (1.0 / 6.0))
    restored_tie = notes[0]["_ust_tie_sections"][0]
    assert restored_tie["length"] == 480
    assert restored_tie["tempo"] == pytest.approx(90.0)
    assert restored_tie["intensity"] == pytest.approx(123.0)
    assert restored_tie["flags"] == "g+3"
    assert restored_tie["vibrato"]["cycle"] == pytest.approx(190.0)
    assert restored_tie["pre_utterance"] == pytest.approx(12.0)
    assert restored_tie["overlap"] == pytest.approx(4.0)
    assert notes[0]["_ust_base_portamento"] == {
        "pbs": "0;0",
        "pbw": "50",
        "pby": "10",
        "pbm": "s,j",
    }
    # The displayed combined curve is rebuilt once from the original base
    # curve plus the original '+' curve, rather than exporting the merged
    # curve and adding the tie curve a second time.
    assert notes[0]["_ust_pbw"] == "50,425,60"


def test_ust_export_applies_duration_edits_to_final_tie_section(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from modules.data.data_models import NoteEvent
    from modules.data.ust_parser import UstConverter, UstParser
    from modules.gui.mixins import project_io_mixin
    from modules.gui.mixins.project_io_mixin import ProjectIOMixin

    output_path = tmp_path / "edited-tie-roundtrip.ust"
    monkeypatch.setattr(
        project_io_mixin.QFileDialog,
        "getSaveFileName",
        lambda *_args, **_kwargs: (str(output_path), "UTAU プロジェクト (*.ust)"),
    )

    class FakeStatusBar:
        def showMessage(self, *_args, **_kwargs):
            pass

    edited = NoteEvent(
        note_number=60,
        start_time=0.0,
        duration=1.0,
        lyric="あ",
    )
    edited._ust_tempo = 120.0
    edited._ust_base_duration = 0.5
    edited._ust_tie_sections = [{
        "length": 480,
        "tempo": 90.0,
        "note_num": 60,
        "intensity": 100.0,
        "modulation": 0.0,
        "flags": "",
        "flags_present": False,
        "pbs": "",
        "pbw": "",
        "pby": "",
        "pbm": "",
        "vibrato": None,
        "pre_utterance": None,
        "overlap": None,
    }]
    following = NoteEvent(
        note_number=62, start_time=1.0, duration=0.5, lyric="い"
    )
    following._ust_tempo = 90.0

    window = SimpleNamespace(
        timeline_widget=SimpleNamespace(tempo=120.0, notes_list=[edited, following]),
        statusBar=lambda: FakeStatusBar(),
    )
    ProjectIOMixin.export_as_ust(window)

    parsed = UstParser().load(str(output_path))
    sections = [note for note in parsed.notes if note.lyric == "+"]
    assert len(sections) == 1
    assert sections[0].length == 360

    notes = UstConverter.to_note_dicts(parsed)
    assert notes[0]["duration"] == pytest.approx(1.0)
    assert notes[1]["start_time"] == pytest.approx(1.0)
