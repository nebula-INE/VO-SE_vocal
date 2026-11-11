import numpy as np
import pytest

from modules.audio.waveform import read_waveform_peaks

# This module imports soundfile lazily, so the dependency check can follow imports.
sf = pytest.importorskip("soundfile")


def test_waveform_peaks_support_24_bit_stereo_without_loading_full_file(tmp_path):
    path = tmp_path / "stereo-24bit.wav"
    samples = np.zeros((131_071, 2), dtype=np.float32)
    samples[::101, 0] = 0.75
    samples[::137, 1] = -0.5
    sf.write(path, samples, 22_050, subtype="PCM_24")

    peaks = read_waveform_peaks(str(path), num_peaks=257)

    assert len(peaks) == 257
    assert max(peaks) == pytest.approx(1.0)
    assert all(0.0 <= peak <= 1.0 for peak in peaks)


def test_waveform_peaks_support_float_wav_and_silence(tmp_path):
    path = tmp_path / "silence-float.wav"
    sf.write(path, np.zeros((4096, 1), dtype=np.float32), 44_100, subtype="FLOAT")

    peaks = read_waveform_peaks(str(path), num_peaks=64)

    assert len(peaks) == 64
    assert peaks == [0.0] * 64


def test_waveform_peaks_reject_missing_files(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_waveform_peaks(str(tmp_path / "missing.wav"))
