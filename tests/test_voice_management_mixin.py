"""GUI compatibility OTO parsing remains unrestricted by legacy defaults."""

from modules.data import oto_parser as oto_parser_module
from modules.gui.mixins.voice_management_mixin import VoiceManagementMixin


def test_gui_oto_parser_accepts_large_records_and_ignores_legacy_caps(tmp_path, monkeypatch):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()
    long_alias = "あ" * 70_000
    (voice_dir / "oto.ini").write_text(
        f"large.wav={long_alias},0,0,0,20,5\nsmall.wav=い,0,0,0,20,5\n",
        encoding="utf-8",
    )

    # Compatibility names injected by an older caller must not restore limits.
    monkeypatch.setattr(oto_parser_module, "MAX_OTO_FILE_BYTES", 1, raising=False)
    monkeypatch.setattr(oto_parser_module, "MAX_OTO_LINE_CHARS", 1, raising=False)
    monkeypatch.setattr(oto_parser_module, "MAX_VOICEBANK_OTO_ENTRIES", 1, raising=False)

    parser = VoiceManagementMixin()
    oto_map = parser.parse_oto_ini(str(voice_dir))

    assert len(oto_map) == 2
    assert oto_map[long_alias]["wav_path"].endswith("large.wav")
    assert oto_map["い"]["wav_path"].endswith("small.wav")
