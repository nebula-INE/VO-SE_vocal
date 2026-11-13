"""Desktop callback-based multi-track audio mixer for VO-SE Vocal.

Audio files are read and mixed in a background producer thread. The PortAudio
callback only copies prebuilt blocks from a bounded queue, so long tracks do
not need to be decoded into memory all at once and disk I/O stays off the
real-time audio callback.
"""
from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Dict, Iterable

import numpy as np

from modules.audio.master_effects import MasterEffectsProcessor, normalize_effect_chain

try:
    import sounddevice as sd
except Exception:
    sd = None

try:
    import soundfile as sf
except Exception:
    sf = None


@dataclass(frozen=True)
class _TrackSource:
    track: Any
    path: str
    frames: int
    sample_rate: int
    output_frames: int
    data: np.ndarray | None = None


class AudioMixer:
    """Callback-based stereo mixer with bounded, background audio streaming."""

    def __init__(
        self,
        sample_rate: int = 44100,
        block_size: int = 1024,
        queue_blocks: int = 8,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.block_size = int(block_size)
        if self.sample_rate <= 0 or self.block_size <= 0:
            raise ValueError("sample_rate and block_size must be positive")

        self._lock = threading.RLock()
        self._stream: Any = None
        self._tracks: list[Any] = []
        self._sources: Dict[int, _TrackSource] = {}
        self._position = 0
        self._track_total_frames = 0
        self._total_frames = 0
        self._effects = MasterEffectsProcessor(self.sample_rate)

        self._blocks: queue.Queue[np.ndarray] = queue.Queue(
            maxsize=max(2, int(queue_blocks))
        )
        self._producer_stop = threading.Event()
        self._producer_done = threading.Event()
        self._producer_ready = threading.Event()
        self._producer_thread: threading.Thread | None = None
        self._callback_pending: np.ndarray | None = None
        self._callback_pending_offset = 0

        self.last_load_errors: list[str] = []
        self.last_playback_errors: list[str] = []
        self.output_underflow_count = 0
        self.output_overflow_count = 0
        self.queue_underrun_count = 0
        self.last_callback_status = ""
        self.output_device_name = ""
        self.output_default_samplerate = 0.0
        self._output_channels = 2

    @property
    def is_playing(self) -> bool:
        stream = self._stream
        return bool(stream is not None and getattr(stream, "active", False))

    @property
    def position_sec(self) -> float:
        with self._lock:
            return self._position / float(self.sample_rate)

    def get_diagnostics(self) -> dict[str, Any]:
        """Return thread-safe playback diagnostics for the GUI and support logs."""
        with self._lock:
            return {
                "output_underflow_count": self.output_underflow_count,
                "output_overflow_count": self.output_overflow_count,
                "queue_underrun_count": self.queue_underrun_count,
                "last_callback_status": self.last_callback_status,
                "output_device_name": self.output_device_name,
                "output_default_samplerate": self.output_default_samplerate,
                "output_channels": self._output_channels,
                "last_load_errors": list(self.last_load_errors),
                "last_playback_errors": list(self.last_playback_errors),
                "queued_blocks": self._blocks.qsize(),
                "enabled_effects": sum(1 for item in self._effects.get_chain() if item["enabled"]),
                "effect_tail_frames": self._effects.tail_frames,
            }

    def _probe_output_device(self) -> None:
        """Resolve the OS default output device and validate its channel support."""
        if sd is None:
            raise RuntimeError("sounddevice is not available")
        try:
            info = sd.query_devices(kind="output")
        except Exception as exc:
            raise RuntimeError(
                "既定の音声出力デバイスを取得できません。OSのサウンド設定で"
                "出力先を選択し、デバイスが接続されていることを確認してください。 "
                f"詳細: {exc}"
            ) from exc

        if not isinstance(info, dict):
            try:
                info = dict(info)
            except Exception as exc:
                raise RuntimeError(f"音声出力デバイス情報が不正です: {exc}") from exc

        try:
            max_channels = int(info.get("max_output_channels", 0))
        except (TypeError, ValueError, OverflowError):
            max_channels = 0
        if max_channels <= 0:
            name = str(info.get("name", "Unknown output device"))
            raise RuntimeError(
                f"音声出力デバイス「{name}」は出力に対応していません。"
                "OSのサウンド設定で別の出力先を選択してください。"
            )

        self.output_device_name = str(info.get("name", "Default output device"))
        try:
            self.output_default_samplerate = float(info.get("default_samplerate", 0.0) or 0.0)
        except (TypeError, ValueError, OverflowError):
            self.output_default_samplerate = 0.0
        # Stereo is preferred. A mono-only device is supported by downmixing
        # the prebuilt stereo block in the PortAudio callback.
        self._output_channels = 1 if max_channels == 1 else 2

    def set_tracks(self, tracks: Iterable[Any]) -> None:
        """Validate track headers and prepare metadata without decoding whole files."""
        if sf is None:
            raise RuntimeError("soundfile is required for desktop audio mixing")

        new_tracks = list(tracks)
        sources: Dict[int, _TrackSource] = {}
        load_errors: list[str] = []

        for track in new_tracks:
            path = self._track_path(track)
            if not path:
                continue
            try:
                info = sf.info(path)
                source_frames = int(info.frames)
                source_rate = int(info.samplerate)
                channels = int(info.channels)
                if source_frames <= 0:
                    raise ValueError("empty audio")
                if source_rate <= 0 or channels <= 0:
                    raise ValueError("invalid audio metadata")
                output_frames = max(
                    1,
                    int(round(source_frames * self.sample_rate / source_rate)),
                )
                sources[id(track)] = _TrackSource(
                    track=track,
                    path=path,
                    frames=source_frames,
                    sample_rate=source_rate,
                    output_frames=output_frames,
                )
            except Exception as exc:
                load_errors.append(f"{path}: {exc}")

        if not sources:
            details = "; ".join(load_errors) if load_errors else "no audio paths"
            raise RuntimeError(f"No playable audio tracks ({details})")

        with self._lock:
            self._tracks = new_tracks
            self._sources = sources
            self._track_total_frames = max(
                source.output_frames for source in sources.values()
            )
            self._total_frames = self._track_total_frames + self._effects.tail_frames
            self.last_load_errors = load_errors
            self.last_playback_errors = []

    def update_tracks(self, tracks: Iterable[Any]) -> None:
        """Update mute/solo/volume/pan state without reloading audio files."""
        new_tracks = list(tracks)
        new_ids = {id(track) for track in new_tracks}
        with self._lock:
            self._tracks = new_tracks
            self._sources = {
                track_id: source
                for track_id, source in self._sources.items()
                if track_id in new_ids
            }
            self._track_total_frames = max(
                (source.output_frames for source in self._sources.values()),
                default=0,
            )
            self._total_frames = self._track_total_frames + self._effects.tail_frames

    def set_effect_chain(self, chain: Any, *, preserve_state: bool = False) -> None:
        """Replace master parameters without touching tracks or the vocal renderer.

        Live parameter drags preserve reverb and compressor histories for matching
        effect IDs; loading a different project or seeking can request a reset.
        """
        normalized = normalize_effect_chain(chain)
        with self._lock:
            self._effects.set_chain(normalized, preserve_state=preserve_state)
            self._total_frames = self._track_total_frames + self._effects.tail_frames
            self._position = min(self._position, self._total_frames)

    def get_effect_chain(self) -> list[dict[str, Any]]:
        """Return a detached snapshot of the current master chain."""
        with self._lock:
            return self._effects.get_chain()

    def play(self, start_sec: float | None = None) -> None:
        """Start playback at start_sec, or resume from the current position."""
        if sd is None:
            raise RuntimeError("sounddevice is not available")
        self._probe_output_device()

        self._shutdown_playback(reset_position=False)
        with self._lock:
            if start_sec is not None or self._position == 0:
                self._effects.reset()
            if not self._sources:
                raise RuntimeError("No audio tracks are loaded")
            if start_sec is not None:
                requested = max(0, int(float(start_sec) * self.sample_rate))
                self._position = min(requested, self._total_frames)
            start_frame = self._position

        self._reset_stream_buffers()
        self._producer_stop.clear()
        self._producer_done.clear()
        self._producer_ready.clear()
        producer = threading.Thread(
            target=self._audio_producer_loop,
            args=(start_frame,),
            name="VO-SE-AudioProducer",
            daemon=True,
        )
        self._producer_thread = producer
        producer.start()
        if not self._producer_ready.wait(timeout=5.0):
            self._shutdown_playback(reset_position=False)
            raise RuntimeError("Audio stream prebuffering did not complete")

        try:
            stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=self._output_channels,
                dtype="float32",
                blocksize=self.block_size,
                callback=self._audio_callback,
            )
            self._stream = stream
            stream.start()
        except Exception as exc:
            try:
                self._shutdown_playback(reset_position=False)
            except Exception:
                # Preserve the original device/stream creation failure for the UI.
                pass
            raise RuntimeError(
                f"音声出力デバイス「{self.output_device_name}」を"
                f"{self.sample_rate} Hz / {self._output_channels}ch で開始できません: {exc}"
            ) from exc

    def play_buffer(
        self,
        audio: np.ndarray,
        sample_rate: int | None = None,
        start_sec: float = 0.0,
    ) -> None:
        """Play an explicitly supplied in-memory buffer through the same mixer."""
        if sd is None:
            raise RuntimeError("sounddevice is not available")

        data = np.asarray(audio, dtype=np.float32)
        if data.ndim == 1:
            data = data[:, None]
        if data.ndim != 2 or data.shape[0] == 0 or data.shape[1] == 0:
            raise ValueError("audio buffer is empty or has an invalid shape")

        source_rate = int(sample_rate or self.sample_rate)
        data = self._resample(data, source_rate)
        stereo = self._to_stereo(data)

        class _BufferTrack:
            volume = 1.0
            pan = 0.0
            is_muted = False
            is_solo = False

        track = _BufferTrack()
        with self._lock:
            self._tracks = [track]
            self._sources = {
                id(track): _TrackSource(
                    track=track,
                    path="",
                    frames=len(stereo),
                    sample_rate=self.sample_rate,
                    output_frames=len(stereo),
                    data=stereo,
                )
            }
            self._track_total_frames = len(stereo)
            self._total_frames = self._track_total_frames + self._effects.tail_frames
            self.last_load_errors = []
            self.last_playback_errors = []
        self.play(start_sec)

    def play_file(self, file_path: str, start_sec: float = 0.0) -> None:
        """Load metadata for and stream one rendered file through the mixer."""

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
        """Pause and preserve the current output position for a later play()."""
        self._shutdown_playback(reset_position=False)

    def set_position(self, position_ms: int) -> None:
        """Seek; when currently playing, restart the producer at the new position."""
        was_playing = self.is_playing
        if was_playing:
            self.pause()
        with self._lock:
            self._position = min(
                max(0, int(float(position_ms) * self.sample_rate / 1000.0)),
                self._total_frames,
            )
            self._effects.reset()
        if was_playing:
            self.play()

    def stop(self) -> None:
        """Stop playback and reset the transport and effect histories."""
        self._shutdown_playback(reset_position=True)
        with self._lock:
            self._effects.reset()

    def _shutdown_playback(self, reset_position: bool) -> None:
        self._producer_stop.set()
        stream = self._stream
        self._stream = None
        stream_error: Exception | None = None
        if stream is not None:
            try:
                stream.stop()
            except Exception as exc:
                stream_error = exc
            try:
                stream.close()
            except Exception as exc:
                if stream_error is None:
                    stream_error = exc

        producer = self._producer_thread
        if producer is not None and producer is not threading.current_thread():
            producer.join(timeout=5.0)
            if producer.is_alive():
                raise RuntimeError(
                    "Audio producer did not stop safely; playback was not restarted"
                )
        self._producer_thread = None
        self._reset_stream_buffers()
        self._producer_done.set()

        with self._lock:
            if reset_position:
                self._position = 0
        if stream_error is not None:
            raise RuntimeError(f"Audio stream shutdown failed: {stream_error}") from stream_error

    def _reset_stream_buffers(self) -> None:
        while True:
            try:
                self._blocks.get_nowait()
            except queue.Empty:
                break
        self._callback_pending = None
        self._callback_pending_offset = 0

    def _audio_producer_loop(self, start_frame: int) -> None:
        """Read and mix bounded blocks off the PortAudio callback thread."""
        with self._lock:
            sources = dict(self._sources)
            total_frames = self._total_frames
        readers: Dict[int, Any] = {}
        failed_sources: set[int] = set()

        try:
            if sf is None:
                raise RuntimeError("soundfile is not available")
            for track_id, source in sources.items():
                if source.data is not None:
                    continue
                try:
                    readers[track_id] = sf.SoundFile(source.path, mode="r")
                except Exception as exc:
                    failed_sources.add(track_id)
                    self._record_playback_error(f"{source.path}: {exc}")

            cursor = start_frame
            while cursor < total_frames and not self._producer_stop.is_set():
                block = self._mix_block(
                    cursor,
                    self.block_size,
                    sources,
                    readers,
                    failed_sources,
                )
                while not self._producer_stop.is_set():
                    try:
                        self._blocks.put(block, timeout=0.1)
                        self._producer_ready.set()
                        break
                    except queue.Full:
                        continue
                cursor += self.block_size
        except Exception as exc:
            self._record_playback_error(str(exc))
        finally:
            for reader in readers.values():
                try:
                    reader.close()
                except Exception as exc:
                    self._record_playback_error(f"Audio reader close failed: {exc}")
            self._producer_done.set()
            self._producer_ready.set()

    def _mix_block(
        self,
        start_frame: int,
        frames: int,
        sources: Dict[int, _TrackSource] | None = None,
        readers: Dict[int, Any] | None = None,
        failed_sources: set[int] | None = None,
    ) -> np.ndarray:
        """Mix one output block from file-backed or in-memory track sources."""
        with self._lock:
            tracks = list(self._tracks)
            active_sources = dict(self._sources) if sources is None else sources
            effects = self._effects
        active_readers = readers or {}
        failed = failed_sources if failed_sources is not None else set()
        mix = np.zeros((frames, 2), dtype=np.float32)
        solo_exists = any(
            bool(getattr(track, "is_solo", False)) for track in tracks
        )

        for track in tracks:
            track_id = id(track)
            source = active_sources.get(track_id)
            if source is None or track_id in failed:
                continue
            if start_frame >= source.output_frames:
                continue

            count = min(frames, source.output_frames - start_frame)
            if not self._track_is_active(track, solo_exists):
                # Still preserve this track's duration in the producer timeline.
                continue

            try:
                if source.data is not None:
                    chunk = source.data[start_frame : start_frame + count]
                else:
                    reader = active_readers.get(track_id)
                    if reader is None:
                        continue
                    chunk = self._read_file_block(reader, source, start_frame, count)
            except Exception as exc:
                if failed_sources is not None:
                    failed_sources.add(track_id)
                self._record_playback_error(f"{source.path}: {exc}")
                continue

            if len(chunk) == 0:
                continue
            gain = float(np.clip(getattr(track, "volume", 1.0), 0.0, 1.0))
            pan = float(np.clip(getattr(track, "pan", 0.0), -1.0, 1.0))
            left_gain, right_gain = self._pan_gains(pan)
            mix[: len(chunk), 0] += chunk[:, 0] * gain * left_gain
            mix[: len(chunk), 1] += chunk[:, 1] * gain * right_gain

        processed = effects.process(mix)
        np.clip(processed, -1.0, 1.0, out=processed)
        return processed

    def _read_file_block(
        self,
        reader: Any,
        source: _TrackSource,
        start_frame: int,
        frames: int,
    ) -> np.ndarray:
        """Seek/read only the source frames needed for one output block."""
        if frames <= 0:
            return np.zeros((0, 2), dtype=np.float32)
        output_indices = start_frame + np.arange(frames, dtype=np.float64)
        source_positions = output_indices * (
            float(source.frames) / float(source.output_frames)
        )
        left_indices = np.floor(source_positions).astype(np.int64)
        right_indices = np.minimum(left_indices + 1, source.frames - 1)
        fraction = (source_positions - left_indices).astype(np.float32)

        source_start = int(left_indices[0])
        source_end = int(right_indices[-1]) + 1
        reader.seek(source_start)
        raw = reader.read(
            source_end - source_start,
            dtype="float32",
            always_2d=True,
        )
        required = source_end - source_start
        if len(raw) < required:
            raise RuntimeError(
                f"audio file ended early (expected {required} frames, got {len(raw)})"
            )

        left = raw[left_indices - source_start]
        right = raw[right_indices - source_start]
        interpolated = left + (right - left) * fraction[:, None]
        return self._to_stereo(interpolated)

    def _audio_callback(
        self,
        outdata: np.ndarray,
        frames: int,
        _time: Any,
        status: Any,
    ) -> None:
        """Real-time callback: copy from the bounded queue and record status flags."""
        if status:
            underflow = bool(getattr(status, "output_underflow", False))
            overflow = bool(getattr(status, "output_overflow", False))
            with self._lock:
                self.last_callback_status = str(status)
                if underflow:
                    self.output_underflow_count += 1
                if overflow:
                    self.output_overflow_count += 1

        outdata.fill(0)
        written = 0
        while written < frames:
            if self._callback_pending is None:
                try:
                    self._callback_pending = self._blocks.get_nowait()
                    self._callback_pending_offset = 0
                except queue.Empty:
                    if not self._producer_done.is_set():
                        with self._lock:
                            self.queue_underrun_count += 1
                    break

            pending = self._callback_pending
            if pending is None:
                break
            available = len(pending) - self._callback_pending_offset
            count = min(frames - written, available)
            if count <= 0:
                self._callback_pending = None
                self._callback_pending_offset = 0
                continue

            offset = self._callback_pending_offset
            segment = pending[offset : offset + count]
            target = outdata[written : written + count]
            output_channels = target.shape[1] if target.ndim == 2 else 1
            if output_channels == 1:
                # A mono-only output device receives a center downmix.
                target[:, 0] = segment[:, 0]
                target[:, 0] += segment[:, 1]
                target[:, 0] *= 0.5
            else:
                copied_channels = min(output_channels, 2)
                target[:, :copied_channels] = segment[:, :copied_channels]
            written += count
            self._callback_pending_offset += count
            if self._callback_pending_offset >= len(pending):
                self._callback_pending = None
                self._callback_pending_offset = 0

        with self._lock:
            self._position = min(self._position + frames, self._total_frames)

        if (
            self._producer_done.is_set()
            and self._blocks.empty()
            and self._callback_pending is None
            and sd is not None
        ):
            raise sd.CallbackStop()

    def _record_playback_error(self, message: str) -> None:
        with self._lock:
            self.last_playback_errors.append(str(message))

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
        """Linear resampling helper for explicitly supplied in-memory buffers."""
        if source_rate == self.sample_rate:
            return data
        if source_rate <= 0:
            raise ValueError("Invalid source sample rate")

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
