"""Bounded-memory waveform peak extraction for desktop timeline previews."""
from __future__ import annotations

import os
from typing import List


def read_waveform_peaks(file_path: str, num_peaks: int = 2000) -> List[float]:
    """Read an audio file in bounded blocks and return normalized per-window peaks.

    The routine deliberately avoids decoding the entire file into memory. Formats
    supported by the active libsndfile build (including PCM/float WAV and supported
    compressed formats) can be previewed by the same path.
    """
    import numpy as np
    import soundfile as sf

    if not file_path or not os.path.isfile(file_path):
        raise FileNotFoundError(file_path or "empty audio path")
    requested_peaks = max(1, int(num_peaks))

    with sf.SoundFile(file_path, mode="r") as audio:
        total_frames = int(audio.frames)
        if total_frames <= 0:
            return []
        bucket_count = min(requested_peaks, total_frames)
        peaks = np.zeros(bucket_count, dtype=np.float32)
        frame_offset = 0

        while frame_offset < total_frames:
            block = audio.read(
                min(65536, total_frames - frame_offset),
                dtype="float32",
                always_2d=True,
            )
            if block.size == 0:
                break
            frame_indices = np.arange(
                frame_offset,
                frame_offset + len(block),
                dtype=np.int64,
            )
            bucket_indices = np.minimum(
                (frame_indices * bucket_count) // total_frames,
                bucket_count - 1,
            )
            frame_peaks = np.max(np.abs(block), axis=1)
            np.maximum.at(peaks, bucket_indices, frame_peaks)
            frame_offset += len(block)

    max_value = float(np.max(peaks)) if peaks.size else 0.0
    if max_value <= 0.0:
        return [0.0 for _ in range(len(peaks))]
    return [float(value / max_value) for value in peaks]
