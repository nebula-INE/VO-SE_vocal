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
