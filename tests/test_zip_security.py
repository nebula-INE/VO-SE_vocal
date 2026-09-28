import zipfile

import pytest

from modules.utils.zip_handler import ZipHandler


@pytest.mark.smoke
def test_zip_handler_rejects_path_traversal(tmp_path):
    archive = tmp_path / "malicious.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../../outside.txt", "blocked")

    ok, message = ZipHandler.extract_voice_bank(str(archive), str(tmp_path / "voices"))

    assert ok is False
    assert "ZIP" in message or "不正" in message
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.smoke
def test_zip_handler_extracts_normal_archive(tmp_path):
    archive = tmp_path / "normal.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("Voice/oto.ini", "a.wav=a,0,0,0,0,0")

    ok, bank_name = ZipHandler.extract_voice_bank(str(archive), str(tmp_path / "voices"))

    assert ok is True
    assert bank_name == "normal"
    assert (tmp_path / "voices" / "normal" / "Voice" / "oto.ini").exists()
