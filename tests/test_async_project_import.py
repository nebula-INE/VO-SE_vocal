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
