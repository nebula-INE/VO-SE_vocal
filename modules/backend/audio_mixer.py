"""Desktop callback-based multi-track audio mixer for VO-SE Vocal.

Audio files are decoded on a bounded producer thread. The PortAudio callback
only copies already prepared blocks, so long projects do not need to be fully
resident in memory and disk I/O never runs in the real-time callback.
"""
from __future__ import annotations

import math
import queue
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
    """Bounded-memory stereo mixer with a disk-reading producer thread."""

    def __init__(
        self,
        sample_rate: int = 44100,
        block_size: int = 1024,
        prefetch_blocks: int = 8,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.block_size = int(block_size)
        if self.sample_rate <= 0 or self.block_size <= 0:
            raise ValueError("sample_rate and block_size must be positive")

        self._lock = threading.RLock()
        self._stream: Any = None
        self._tracks: list[Any] = []
        self._sources: Dict[int, Dict[str, Any]] = {}
        self._position = 0
        self.last_load_errors: list[str] = []

        self._queue: queue.Queue[tuple[int, np.ndarray, int]] = queue.Queue(
            maxsize=max(2, int(prefetch_blocks))
        )
        self._producer_thread: threading.Thread | None = None
        self._producer_stop = threading.Event()
        self._producer_done = threading.Event()
        self._producer_ready = threading.Event()
        self._producer_error = ""
        self._pending_block: tuple[int, np.ndarray, int] | None = None
        self._pending_offset = 0
        self._output_underflow_count = 0
        self._callback_status_count = 0

    @property
    def is_playing(self) -> bool:
        stream = self._stream
        return bool(stream is not None and getattr(stream, "active", False))

    @property
    def position_sec(self) -> float:
        with self._lock:
            return self._position / float(self.sample_rate)

    @property
    def output_underflow_count(self) -> int:
        return self._output_underflow_count

    @property
    def last_error(self) -> str:
        return self._producer_error

    def set_tracks(self, tracks: Iterable[Any]) -> None:
        """Index track metadata only; do not decode full audio files here."""
        if sf is None:
            raise RuntimeError("soundfile is required for desktop audio mixing")

        self._shutdown(reset_position=True)
        new_tracks = list(tracks)
        sources: Dict[int, Dict[str, Any]] = {}
        load_errors: list[str] = []
        with self._lock:
            self._tracks = []
            self._sources = {}
            self.last_load_errors = []

        for track in new_tracks:
            path = self._track_path(track)
            if not path:
                continue
            try:
                info = sf.info(path)
                source_rate = int(info.samplerate)
                source_frames = int(info.frames)
                if source_rate <= 0 or source_frames <= 0:
                    raise ValueError("empty audio or invalid sample rate")
                sources[id(track)] = {
                    "path": path,
                    "sample_rate": source_rate,
                    "frames": source_frames,
                    "array": None,
                }
            except Exception as exc:
                load_errors.append(f"{path}: {exc}")

        if not sources:
            details = "; ".join(load_errors) if load_errors else "no audio paths"
            raise RuntimeError(f"No playable audio tracks ({details})")

        with self._lock:
            self._tracks = new_tracks
            self._sources = sources
            self.last_load_errors = load_errors

    def update_tracks(self, tracks: Iterable[Any]) -> None:
        """Update live mute/solo/volume/pan values without reloading file data."""
        with self._lock:
            self._tracks = list(tracks)

    def play(self, start_sec: float = 0.0) -> None:
        if sd is None:
            raise RuntimeError("sounddevice is not available")
        if not math.isfinite(float(start_sec)):
            raise ValueError("start_sec must be finite")

        self._shutdown(reset_position=False)
        with self._lock:
            if not self._sources:
                raise RuntimeError("No audio tracks have been loaded")
            self._position = max(0, int(float(start_sec) * self.sample_rate))

        self._producer_stop = threading.Event()
        self._producer_done = threading.Event()
        self._producer_ready = threading.Event()
        self._producer_error = ""
        self._output_underflow_count = 0
        self._callback_status_count = 0
        self._pending_block = None
        self._pending_offset = 0
        self._clear_queue()

        self._producer_thread = threading.Thread(
            target=self._producer_loop,
            name="VO-SE-AudioProducer",
            daemon=True,
        )
        self._producer_thread.start()
        # Give the worker a brief opportunity to prepare the first block. The
        # callback remains non-blocking even when a slow disk misses this window.
        self._producer_ready.wait(timeout=0.25)

        try:
            self._stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=2,
                dtype="float32",
                blocksize=self.block_size,
                callback=self._audio_callback,
            )
            self._stream.start()
        except Exception:
            self._shutdown(reset_position=False)
            raise

    def play_buffer(
        self,
        audio: np.ndarray,
        sample_rate: int | None = None,
        start_sec: float = 0.0,
    ) -> None:
        """Play an in-memory buffer through the same bounded callback path."""
        data = np.asarray(audio, dtype=np.float32)
        if data.ndim == 1:
            data = data[:, None]
        if data.ndim != 2 or data.shape[0] == 0:
            raise ValueError("audio buffer is empty or has an invalid shape")

        source_rate = int(sample_rate or self.sample_rate)
        if source_rate <= 0:
            raise ValueError("sample_rate must be positive")
        stereo = self._to_stereo(data)

        class _BufferTrack:
            volume = 1.0
            pan = 0.0
            is_muted = False
            is_solo = False

        track = _BufferTrack()
        self._shutdown(reset_position=True)
        with self._lock:
            self._tracks = [track]
            self._sources = {
                id(track): {
                    "path": "",
                    "sample_rate": source_rate,
                    "frames": len(stereo),
                    "array": stereo,
                }
            }
        self.play(start_sec)

    def play_file(self, file_path: str, start_sec: float = 0.0) -> None:
        """Load one audio file by metadata and stream it from disk."""
        class _PlaybackTrack:
            def __init__(self, path: str) -> None:
                self.audio_path = path
                self.playback_path = ""
                self.volume = 1.0
                self.pan = 0.0
                self.is_muted = False
                self.is_solo = False

        self.set_tracks([_PlaybackTrack(file_path)])
        self.play(start_sec)

    def pause(self) -> None:
        """Pause playback while retaining the last consumed sample position."""
        self._shutdown(reset_position=False)

    def set_position(self, position_ms: int) -> None:
        """Seek safely by restarting the producer at the requested position."""
        target = max(0, int(float(position_ms) * self.sample_rate / 1000.0))
        was_playing = self.is_playing
        if was_playing:
            self.pause()
        with self._lock:
            self._position = target
        if was_playing:
            self.play(target / float(self.sample_rate))

    def stop(self) -> None:
        self._shutdown(reset_position=True)

    def _shutdown(self, reset_position: bool) -> None:
        stop_event = self._producer_stop
        stop_event.set()

        stream = self._stream
        if stream is not None:
            stop_error: Exception | None = None
            try:
                stream.stop()
            except Exception as exc:
                stop_error = exc
            try:
                stream.close()
            except Exception as exc:
                raise RuntimeError(
                    f"Audio output stream could not be closed: {exc}"
                ) from exc

            # A successful close must leave PortAudio inactive. A stop error
            # alone is tolerated only when close has actually deactivated it.
            try:
                still_active = bool(getattr(stream, "active", False))
            except Exception:
                still_active = stop_error is not None
            if still_active:
                detail = f": {stop_error}" if stop_error is not None else ""
                raise RuntimeError(
                    "Audio output stream is still active after close" + detail
                ) from stop_error
            self._stream = None

        producer = self._producer_thread
        if producer is not None and producer.is_alive():
            producer.join(timeout=5.0)
            if producer.is_alive():
                raise RuntimeError("Audio producer did not stop within 5 seconds")
        self._producer_thread = None
        self._producer_done.set()
        self._clear_queue()
        self._pending_block = None
        self._pending_offset = 0
        if reset_position:
            with self._lock:
                self._position = 0

    def _clear_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def _producer_loop(self) -> None:
        handles: Dict[int, Any] = {}
        try:
            with self._lock:
                tracks = list(self._tracks)
                sources = dict(self._sources)

            durations = {
                track_id: max(
                    1,
                    int(round(source["frames"] * self.sample_rate / source["sample_rate"])),
                )
                for track_id, source in sources.items()
            }
            for track in tracks:
                source = sources.get(id(track))
                if source is None or source.get("array") is not None:
                    continue
                if sf is None:
                    raise RuntimeError("soundfile is not available")
                handles[id(track)] = sf.SoundFile(source["path"], mode="r")

            with self._lock:
                frame_position = self._position

            end_frame = max(durations.values(), default=frame_position)
            while not self._producer_stop.is_set() and frame_position < end_frame:
                count = min(self.block_size, end_frame - frame_position)
                mix = np.zeros((self.block_size, 2), dtype=np.float32)
                valid_frames = 0

                with self._lock:
                    active_tracks = list(self._tracks)
                solo_exists = any(
                    bool(getattr(track, "is_solo", False))
                    for track in active_tracks
                )

                for track in active_tracks:
                    track_id = id(track)
                    source = sources.get(track_id)
                    if source is None:
                        continue
                    duration = durations[track_id]
                    if frame_position >= duration:
                        continue

                    track_count = min(count, duration - frame_position)
                    valid_frames = max(valid_frames, track_count)
                    audio = self._read_source_block(
                        source,
                        handles.get(track_id),
                        frame_position,
                        track_count,
                    )
                    if not self._track_is_active(track, solo_exists):
                        continue
                    if len(audio) == 0:
                        continue

                    gain = float(np.clip(getattr(track, "volume", 1.0), 0.0, 1.0))
                    pan = float(np.clip(getattr(track, "pan", 0.0), -1.0, 1.0))
                    left_gain, right_gain = self._pan_gains(pan)
                    audio_count = min(len(audio), track_count)
                    mix[:audio_count, 0] += audio[:audio_count, 0] * gain * left_gain
                    mix[:audio_count, 1] += audio[:audio_count, 1] * gain * right_gain

                if valid_frames <= 0:
                    break
                np.clip(mix, -1.0, 1.0, out=mix)
                item = (frame_position, mix, valid_frames)
                while not self._producer_stop.is_set():
                    try:
                        self._queue.put(item, timeout=0.1)
                        self._producer_ready.set()
                        break
                    except queue.Full:
                        continue
                frame_position += count

        except Exception as exc:
            self._producer_error = str(exc)
        finally:
            for handle in handles.values():
                try:
                    handle.close()
                except Exception:
                    pass
            self._producer_done.set()
            self._producer_ready.set()

    def _read_source_block(
        self,
        source: Dict[str, Any],
        handle: Any,
        output_start: int,
        output_count: int,
    ) -> np.ndarray:
        """Read and resample only the requested output block."""
        source_rate = int(source["sample_rate"])
        # Match the legacy whole-file resampler: its output frame count was
        # round(source_frames * target_rate / source_rate), then linspace()
        # mapped each output index across the full source-frame interval.
        target_frames = max(
            1,
            int(round(int(source["frames"]) * self.sample_rate / source_rate)),
        )
        ratio = int(source["frames"]) / float(target_frames)
        first_source_frame = int(math.floor(output_start * ratio))
        last_source_position = (output_start + output_count - 1) * ratio
        required_frames = max(
            1,
            int(math.floor(last_source_position)) - first_source_frame + 2,
        )

        array = source.get("array")
        if array is not None:
            raw = array[
                first_source_frame:first_source_frame + required_frames
            ]
        else:
            if handle is None:
                raise RuntimeError("audio file handle is not open")
            handle.seek(first_source_frame)
            raw = handle.read(
                frames=required_frames,
                dtype="float32",
                always_2d=True,
            )

        if len(raw) == 0:
            return np.zeros((0, 2), dtype=np.float32)
        stereo = self._to_stereo(np.asarray(raw, dtype=np.float32))
        positions = (
            (output_start + np.arange(output_count, dtype=np.float64)) * ratio
            - first_source_frame
        )
        valid = positions <= (len(stereo) - 1)
        result = np.zeros((output_count, 2), dtype=np.float32)
        if source_rate == self.sample_rate:
            indices = positions[valid].astype(np.int64)
            result[valid] = stereo[indices]
            if np.any(~valid):
                result[~valid] = stereo[-1]
        else:
            source_indices = np.arange(len(stereo), dtype=np.float64)
            for channel in range(2):
                # Match the previous whole-file np.interp behavior at the
                # final partial resampling interval by holding the last sample.
                result[:, channel] = np.interp(
                    positions,
                    source_indices,
                    stereo[:, channel],
                    left=0.0,
                    right=float(stereo[-1, channel]),
                ).astype(np.float32)
        return result

    def _audio_callback(
        self,
        outdata: np.ndarray,
        frames: int,
        _time: Any,
        status: Any,
    ) -> None:
        """Real-time callback: copy queued blocks only; never read files or mix."""
        status_underflow = False
        if status:
            self._callback_status_count += 1
            try:
                status_underflow = bool(status.output_underflow)
            except AttributeError:
                # Keep a generic status count for backend-specific status types.
                status_underflow = True
            if status_underflow:
                self._output_underflow_count += 1

        outdata.fill(0)
        written = 0
        while written < frames:
            if self._pending_block is None:
                try:
                    self._pending_block = self._queue.get_nowait()
                    self._pending_offset = 0
                except queue.Empty:
                    if self._producer_done.is_set():
                        if sd is not None:
                            raise sd.CallbackStop()
                        return
                    if not status_underflow:
                        self._output_underflow_count += 1
                    # Do not consume timeline frames while data is unavailable.
                    # The next callback retries the same source position after
                    # the producer has filled the queue, avoiding skipped audio.
                    return

            block_start, block, valid_frames = self._pending_block
            block_position = block_start + self._pending_offset
            with self._lock:
                current_position = self._position

            if self._pending_offset >= valid_frames:
                self._pending_block = None
                self._pending_offset = 0
                continue

            if block_position < current_position:
                skipped = min(
                    valid_frames - self._pending_offset,
                    current_position - block_position,
                )
                self._pending_offset += skipped
                if self._pending_offset >= valid_frames:
                    self._pending_block = None
                    self._pending_offset = 0
                continue

            if block_position > current_position:
                silence = min(frames - written, block_position - current_position)
                written += silence
                with self._lock:
                    self._position += silence
                continue

            available = valid_frames - self._pending_offset
            count = min(frames - written, available)
            outdata[written:written + count] = block[
                self._pending_offset:self._pending_offset + count
            ]
            written += count
            self._pending_offset += count
            with self._lock:
                self._position += count

            if self._pending_offset >= valid_frames:
                self._pending_block = None
                self._pending_offset = 0

        if (
            self._producer_done.is_set()
            and self._pending_block is None
            and self._queue.empty()
            and sd is not None
        ):
            raise sd.CallbackStop()

    def get_latency(self) -> float:
        """Return the output stream's reported latency in seconds."""
        if self._stream is not None:
            return float(self._stream.latency)
        return 0.0

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
        """Compatibility helper used by existing callers and tests."""
        if source_rate == self.sample_rate:
            return data
        if source_rate <= 0:
            raise ValueError("Invalid source sample rate")
        if len(data) == 0:
            return np.zeros((0, data.shape[1] if data.ndim > 1 else 1), dtype=np.float32)

        target_len = max(
            1,
            int(round(len(data) * self.sample_rate / source_rate)),
        )
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
