from pathlib import Path
import numpy as np
import soundfile as sf

from modules.audio.vo_se_engine import VO_SE_Engine
from modules.audio.vo_se_engine_patch import _export_to_wav_v2
from modules.data.data_models import NoteEvent
from modules.data.oto_parser import OtoParser
from modules.audio.vcv_resolver import VcvResolver


def _write_tone(path: Path, frequency: float, duration: float = 0.08) -> np.ndarray:
    sample_rate = 44100
    t = np.arange(int(sample_rate * duration), dtype=np.float64) / sample_rate
    data = (0.35 * np.sin(2.0 * np.pi * frequency * t)).astype(np.float32)
    sf.write(path, data, sample_rate, subtype="PCM_16")
    return data


class _RenderCaptureLib:
    def __init__(self) -> None:
        self.selected_wavs = []

    def execute_render(self, notes, note_count, output_path, _mode_flag):
        self.selected_wavs = []
        rendered = []

        for index in range(note_count):
            raw = bytes(notes[index].wav_path or b"")
            wav_path = raw.split(b"\x00", 1)[0].decode("utf-8")
            self.selected_wavs.append(wav_path)
            if not wav_path:
                continue
            samples, sample_rate = sf.read(wav_path, dtype="float32")
            if samples.ndim > 1:
                samples = samples[:, 0]
            rendered.append(samples)

        if rendered:
            output = np.concatenate(rendered)
        else:
            output = np.zeros(1, dtype=np.float32)

        sf.write(output_path.decode("utf-8"), output, 44100, subtype="PCM_16")


def _make_engine(voice_dir: Path) -> tuple[VO_SE_Engine, _RenderCaptureLib]:
    engine = VO_SE_Engine.__new__(VO_SE_Engine)
    engine.lib = _RenderCaptureLib()
    engine.voice_lib_path = str(voice_dir)
    engine.oto_parser = OtoParser()
    engine.oto_parser.load_voice_dir(str(voice_dir), use_cache=False)
    engine.vcv_resolver = VcvResolver(engine.oto_parser, use_g2p=True)
    engine.cvvc_resolver = None
    engine.text_analyzer = SimpleNamespace(
        align_vocal_timing=lambda notes, _oto_parser, tempo_bpm=120.0: (notes, [])
    )
    engine.aural_ai = None
    engine._temp_refs = []
    return engine, engine.lib


def test_multipitch_render_selects_real_wav_and_playback_uses_generated_file(
    tmp_path, monkeypatch
):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()

    (voice_dir / "prefix.map").write_text(
        "C1\t\t_C4\nF4\t\t_F4\n",
        encoding="utf-8",
    )

    c4_wav = voice_dir / "a_C4.wav"
    f4_wav = voice_dir / "a_F4.wav"
    c4_data = _write_tone(c4_wav, 261.625565)
    f4_data = _write_tone(f4_wav, 349.228231)

    (voice_dir / "oto.ini").write_text(
        "a_C4.wav=あ_C4,0,0,0,0,0\n"
        "a_F4.wav=あ_F4,0,0,0,0,0\n",
        encoding="utf-8",
    )

    engine, render_lib = _make_engine(voice_dir)

    notes = [
        NoteEvent(note_number=64, lyric="あ", start_time=0.0, duration=0.08),
        NoteEvent(note_number=65, lyric="あ", start_time=0.08, duration=0.08),
    ]
    output_path = tmp_path / "rendered.wav"

    empty_curves = {
        "Pitch": [],
        "Gender": [],
        "Tension": [],
        "Breath": [],
    }

    rendered_path = _export_to_wav_v2(
        engine,
        notes,
        empty_curves,
        str(output_path),
        tempo_bpm=120.0,
    )

    assert rendered_path == str(output_path.resolve())
    assert output_path.exists()

    assert render_lib.selected_wavs == [
        str(c4_wav),
        str(f4_wav),
    ]

    rendered, sample_rate = sf.read(output_path, dtype="float32")
    assert sample_rate == 44100
    assert rendered.size == c4_data.size + f4_data.size

    split = c4_data.size
    np.testing.assert_allclose(rendered[:split], c4_data, atol=2e-4)
    np.testing.assert_allclose(rendered[split:], f4_data, atol=2e-4)

    playback_calls = []

    class _FakeSoundDevice:
        @staticmethod
        def play(data, fs):
            playback_calls.append((np.asarray(data), fs))

    import modules.audio.vo_se_engine as engine_module

    monkeypatch.setattr(engine_module, "sd", _FakeSoundDevice)

    engine.play(str(output_path))

    assert len(playback_calls) == 1
    playback_data, playback_rate = playback_calls[0]
    assert playback_rate == 44100
    np.testing.assert_allclose(playback_data, rendered, atol=0.0)


def test_multipitch_boundaries_keep_c4_and_f4_selection_separate(tmp_path):
    voice_dir = tmp_path / "voice"
    voice_dir.mkdir()

    (voice_dir / "prefix.map").write_text(
        "C1\t\t_C4\nF4\t\t_F4\n",
        encoding="utf-8",
    )
    (voice_dir / "a_C4.wav").write_bytes(b"RIFF")
    (voice_dir / "a_F4.wav").write_bytes(b"RIFF")
    (voice_dir / "oto.ini").write_text(
        "a_C4.wav=あ_C4,0,0,0,0,0\n"
        "a_F4.wav=あ_F4,0,0,0,0,0\n",
        encoding="utf-8",
    )

    parser = OtoParser()
    parser.load_voice_dir(str(voice_dir), use_cache=False)

    c4 = parser.resolve_alias("あ", None, note_num=64)
    f4 = parser.resolve_alias("あ", None, note_num=65)

    assert c4 is not None
    assert f4 is not None
    assert c4.alias == "あ_C4"
    assert f4.alias == "あ_F4"
    assert c4.wav_path == str(voice_dir / "a_C4.wav")
    assert f4.wav_path == str(voice_dir / "a_F4.wav")
