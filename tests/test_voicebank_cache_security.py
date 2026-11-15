import json
import os
import pickle
from pathlib import Path

from modules.gui.mixins.voice_management_mixin import VoiceManagementMixin


class _UnpickleCommand:
    def __init__(self, marker: Path) -> None:
        self.marker = marker

    def __reduce__(self):
        return (os.system, (f'touch "{self.marker}"',))


def test_untrusted_legacy_oto_cache_is_never_unpickled(tmp_path):
    voicebank = tmp_path / "voicebank"
    voicebank.mkdir()
    (voicebank / "oto.ini").write_text("a.wav=あ,0,0,0,0,0", encoding="utf-8")
    marker = tmp_path / "pickle-executed"
    cache = voicebank / "oto_cache.vose"
    cache.write_bytes(pickle.dumps(_UnpickleCommand(marker)))
    future = cache.stat().st_mtime + 5
    os.utime(cache, (future, future))

    expected = {"あ": {
        "wav_path": str(voicebank / "a.wav"),
        "offset": 0.0, "consonant": 0.0, "blank": 0.0,
        "preutterance": 0.0, "overlap": 0.0,
    }}
    mixin = VoiceManagementMixin()
    mixin.parse_oto_ini = lambda path: expected

    assert mixin.get_cached_oto(str(voicebank)) == expected
    assert not marker.exists()
    with cache.open(encoding="utf-8") as cache_file:
        assert json.load(cache_file) == expected
