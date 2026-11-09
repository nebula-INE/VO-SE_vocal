import json

import main


def test_main_config_save_replaces_existing_file_atomically(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text('{"volume": 0.2}', encoding="utf-8")
    handler = main.ConfigHandler(str(config_path))

    handler.save_config({"volume": 0.75, "default_voice": "Test Voice"})

    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "volume": 0.75,
        "default_voice": "Test Voice",
    }


def test_main_config_save_failure_preserves_existing_file(tmp_path, monkeypatch):
    config_path = tmp_path / "config.json"
    original = '{"volume": 0.2}'
    config_path.write_text(original, encoding="utf-8")
    handler = main.ConfigHandler(str(config_path))

    def fail_replace(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(main.os, "replace", fail_replace)
    handler.save_config({"volume": 0.9})

    assert config_path.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob(".vose-config-*.tmp")) == []
