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
        "def _on_external_project_loaded", 1
    )[0]

    assert "_ExternalProjectLoadWorker(file_path)" in dispatcher
    assert "QThreadPool.globalInstance().start" in dispatcher



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

    payload = _parse_external_project_file(str(path))

    assert payload["format"] == "midi"
    assert isinstance(payload["notes_by_track"][0][0], NoteEvent)
    assert payload["notes_by_track"][0][0].lyric == "あ"
    assert payload["notes_by_track"][2][0].note_number == 67
    assert payload["names_by_track"] == {0: "Lead", 2: "Harmony"}


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
    assert "self.manager.install_voice_from_zip(zip_path)" in worker
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
