"""Desktop multi-track audio mixer for VO-SE Vocal.

The mixer owns the actual desktop Wave playback path. Track objects remain the
source of truth for volume/pan/mute/solo, so UI changes are reflected by the
next audio callback without rebuilding the UI or restarting playback.
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Iterable

import numpy as np

try:
    import sounddevice as sd
except Exception:
    sd = None

try:
    import soundfile as sf
except Exception:
    sf = None


class AudioMixer:
    """Callback-based multi-track mixer using sounddevice."""

    def __init__(self, sample_rate: int = 44100, block_size: int = 1024) -> None:
        self.sample_rate = int(sample_rate)
        self.block_size = int(block_size)
        self._lock = threading.RLock()
        self._stream: Any = None
        self._tracks: list[Any] = []
        self._buffers: Dict[int, np.ndarray] = {}
        self._position = 0
        self.last_load_errors: list[str] = []

    @property
    def is_playing(self) -> bool:
        stream = self._stream
        return bool(stream is not None and getattr(stream, "active", False))

    @property
    def position_sec(self) -> float:
        with self._lock:
            return self._position / float(self.sample_rate)

    def set_tracks(self, tracks: Iterable[Any]) -> None:
        if sf is None:
            raise RuntimeError("soundfile is required for desktop audio mixing")

        new_tracks = list(tracks)
        buffers: Dict[int, np.ndarray] = {}
        load_errors: list[str] = []

        for track in new_tracks:
            path = self._track_path(track)
            if not path:
                continue
            try:
                data, source_rate = sf.read(path, dtype="float32", always_2d=True)
            except Exception as exc:
                load_errors.append(f"{path}: {exc}")
                continue

            try:
                data = self._resample(data, int(source_rate))
                stereo = self._to_stereo(data)
            except Exception as exc:
                load_errors.append(f"{path}: {exc}")
                continue

            if len(stereo) == 0:
                load_errors.append(f"{path}: empty audio")
                continue
            buffers[id(track)] = stereo

        if not buffers:
            details = "; ".join(load_errors) if load_errors else "no audio paths"
            raise RuntimeError(f"No playable audio tracks ({details})")

        with self._lock:
            self._tracks = new_tracks
            self._buffers = buffers
            self.last_load_errors = load_errors

    def update_tracks(self, tracks: Iterable[Any]) -> None:
        with self._lock:
            self._tracks = list(tracks)

    def play(self, start_sec: float = 0.0) -> None:
        if sd is None:
            raise RuntimeError("sounddevice is not available")

        self.stop()

        with self._lock:
            self._position = max(0, int(float(start_sec) * self.sample_rate))

        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=2,
            dtype="float32",
            blocksize=self.block_size,
            callback=self._callback,
        )
        self._stream.start()

    def play_file(self, file_path: str, start_sec: float = 0.0) -> None:
        """Play one rendered file through the same mixer path used by tracks."""
        class _PlaybackTrack:
            def __init__(self, path: str) -> None:
                self.audio_path = path
                self.playback_path = ""
                self.volume = 1.0
                self.pan = 0.0
                self.is_muted = False
                self.is_solo = False

        track = _PlaybackTrack(file_path)
        self.set_tracks([track])
        self.play(start_sec)

    def pause(self) -> None:
        """Pause while preserving the current sample position."""
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()

    def set_position(self, position_ms: int) -> None:
        with self._lock:
            self._position = max(0, int(float(position_ms) * self.sample_rate / 1000.0))

    def stop(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        with self._lock:
            self._position = 0

    def _callback(self, outdata: np.ndarray, frames: int, _time: Any, status: Any) -> None:
        if status:
            pass

        with self._lock:
            start = self._position
            end = start + frames
            tracks = list(self._tracks)
            buffers = dict(self._buffers)

            mix = np.zeros((frames, 2), dtype=np.float32)
            solo_exists = any(bool(getattr(t, "is_solo", False)) for t in tracks)
            has_audio = False
            all_finished = True

            for track in tracks:
                if not self._track_is_active(track, solo_exists):
                    continue

                data = buffers.get(id(track))
                if data is None:
                    continue
                if start < len(data):
                    all_finished = False
                if start >= len(data):
                    continue

                chunk = data[start:min(end, len(data))]
                if len(chunk) == 0:
                    continue
                has_audio = True

                gain = float(np.clip(getattr(track, "volume", 1.0), 0.0, 1.0))
                pan = float(np.clip(getattr(track, "pan", 0.0), -1.0, 1.0))
                left_gain, right_gain = self._pan_gains(pan)

                count = min(len(chunk), frames)
                mix[:count, 0] += chunk[:count, 0] * gain * left_gain
                mix[:count, 1] += chunk[:count, 1] * gain * right_gain

            self._position = end

            should_stop = bool(buffers) and all_finished and not has_audio

        np.clip(mix, -1.0, 1.0, out=mix)
        outdata[:] = mix
        if should_stop and sd is not None:
            raise sd.CallbackStop()

    @staticmethod
    def _track_is_active(track: Any, solo_exists: bool) -> bool:
        if bool(getattr(track, "is_muted", False)):
            return False
        if solo_exists and not bool(getattr(track, "is_solo", False)):
            return False
        return True

    @staticmethod
    def _track_path(track: Any) -> str:
        playback_path = str(getattr(track, "playback_path", "") or "")
        if playback_path:
            return playback_path
        return str(getattr(track, "audio_path", "") or "")

    @staticmethod
    def _pan_gains(pan: float) -> tuple[float, float]:
        angle = (pan + 1.0) * np.pi / 4.0
        return float(np.cos(angle)), float(np.sin(angle))

    def _resample(self, data: np.ndarray, source_rate: int) -> np.ndarray:
        if source_rate == self.sample_rate:
            return data
        if source_rate <= 0:
            raise ValueError("Invalid source sample rate")

        target_len = max(1, int(round(len(data) * self.sample_rate / source_rate)))
        source_x = np.linspace(0.0, 1.0, len(data), endpoint=False)
        target_x = np.linspace(0.0, 1.0, target_len, endpoint=False)
        channels = [
            np.interp(target_x, source_x, data[:, channel])
            for channel in range(data.shape[1])
        ]
        return np.stack(channels, axis=1).astype(np.float32, copy=False)

    @staticmethod
    def _to_stereo(data: np.ndarray) -> np.ndarray:
        if data.ndim == 1:
            data = data[:, None]
        if data.shape[1] == 1:
            return np.repeat(data, 2, axis=1).astype(np.float32, copy=False)
        if data.shape[1] >= 2:
            return data[:, :2].astype(np.float32, copy=False)
        return np.zeros((0, 2), dtype=np.float32)
