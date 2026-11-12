"""Runtime smoke probe for the SciPy APIs used by VO-SE.

This is frozen as a small one-file executable by the Linux validation workflow
with scipy.stats excluded and scipy.optimize retained because scipy.interpolate\nimports it internally. It proves the required signal,
FFT, and linear-algebra APIs remain usable without those unrelated subpackages.
"""
from __future__ import annotations

import numpy as np
from typing import cast
from scipy.fft import rfft, rfftfreq
from scipy.linalg import solve_toeplitz
from scipy.signal import find_peaks, hilbert, lfilter


def main() -> int:
    sample_count = 64
    signal = np.sin(np.linspace(0.0, 2.0 * np.pi, sample_count, endpoint=False))

    analytic_signal = cast(np.ndarray, hilbert(signal))
    assert analytic_signal.shape == signal.shape
    assert np.all(np.isfinite(analytic_signal))

    peaks, _properties = find_peaks(signal)
    assert peaks.size == 1

    filtered = cast(np.ndarray, lfilter([1.0], [1.0], signal))
    assert np.allclose(filtered, signal)

    spectrum = cast(np.ndarray, rfft(signal))
    frequencies = cast(np.ndarray, rfftfreq(sample_count, d=1.0 / sample_count))
    assert spectrum.shape == (sample_count // 2 + 1,)
    assert frequencies.shape == spectrum.shape

    solution = cast(np.ndarray, solve_toeplitz((2.0, 1.0), np.array([1.0, 0.0])))
    assert np.allclose(solution, np.array([2.0 / 3.0, -1.0 / 3.0]))

    print(
        "SciPy subset probe passed: hilbert, find_peaks, lfilter, "
        "rfft, rfftfreq, solve_toeplitz"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
