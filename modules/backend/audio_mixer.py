"""Desktop callback-based multi-track audio mixer for VO-SE Vocal.

Audio files are read and mixed in a background producer thread. The PortAudio
callback only copies prebuilt blocks from a bounded queue, so long tracks do
not need to be decoded into memory all at once and disk I/O stays off the
real-time audio callback.
"""
from __future__ import annotations

import os
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

    SUPPORTED_BLOCK_SIZES = (64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768)
    SUPPORTED_SAMPLE_RATES = (44100, 48000, 88200, 96000)

    def __init__(
        self,
        sample_rate: int = 44100,
        block_size: int = 256,
        queue_blocks: int = 8,
        output_device: int | None = None,
    ) -> None:
        self.sample_rate = int(sample_rate)
        self.block_size = int(block_size)
        if self.sample_rate <= 0 or self.block_size <= 0:
            raise ValueError("sample_rate and block_size must be positive")

        self._lock = threading.RLock()
        self._transport_lock = threading.RLock()
        self._stream: Any = None
        self._tracks: list[Any] = []
        self._sources: Dict[int, _TrackSource] = {}
        self._position = 0
        self._track_total_frames = 0
        self._total_frames = 0
        self._effects = MasterEffectsProcessor(self.sample_rate)
        self._track_effects: Dict[int, MasterEffectsProcessor] = {}
        self.output_device = output_device

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
        self.output_device_host_api = ""
        self.output_default_samplerate = 0.0
        self.output_latency_seconds = 0.0
        self._output_channels = 2

    @property
    def is_playing(self) -> bool:
        stream = self._stream
        return bool(stream is not None and getattr(stream, "active", False))

    @property
    def position_sec(self) -> float:
        with self._lock:
            return self._position / float(self.sample_rate)

    def set_block_size(self, block_size: int) -> int:
        """Safely change PortAudio's block size, preserving active playback position."""
        try:
            requested_size = int(block_size)
        except (TypeError, ValueError) as exc:
            raise ValueError("block_size must be an integer") from exc
        if requested_size not in self.SUPPORTED_BLOCK_SIZES:
            raise ValueError(
                "Unsupported audio block size: "
                f"{requested_size}; expected one of {self.SUPPORTED_BLOCK_SIZES}"
            )

        with self._transport_lock:
            old_size = self.block_size
            if requested_size == old_size:
                return old_size

            was_playing = self.is_playing
            was_started = self._stream is not None or self._producer_thread is not None
            position_sec = self.position_sec

            # A PortAudio stream's block size is immutable after construction.
            # Stop its callback first, then join the producer before clearing queues.
            if was_started:
                self._shutdown_playback(reset_position=False)

            self.block_size = requested_size
            if was_playing:
                try:
                    self.play(position_sec)
                except Exception:
                    self.block_size = old_size
                    raise
            return requested_size

    def get_diagnostics(self) -> dict[str, Any]:
        """Return thread-safe playback diagnostics for the GUI and support logs."""
        with self._lock:
            return {
                "output_underflow_count": self.output_underflow_count,
                "output_overflow_count": self.output_overflow_count,
                "queue_underrun_count": self.queue_underrun_count,
                "last_callback_status": self.last_callback_status,
                "output_device_name": self.output_device_name,
                "output_device_host_api": self.output_device_host_api,
                "output_default_samplerate": self.output_default_samplerate,
                "output_latency_ms": self.output_latency_seconds * 1000.0,
                "output_channels": self._output_channels,
                "output_device_index": self.output_device,
                "sample_rate": self.sample_rate,
                "block_size": self.block_size,
                "enabled_track_effects": sum(
                    sum(1 for item in processor.get_chain() if item["enabled"])
                    for processor in self._track_effects.values()
                ),
                "track_effect_tail_frames": max(
                    (processor.tail_frames for processor in self._track_effects.values()),
                    default=0,
                ),
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
            info = (
                sd.query_devices(kind="output")
                if self.output_device is None
                else sd.query_devices(self.output_device, kind="output")
            )
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
        self.output_device_host_api = ""
        try:
            host_index = int(info.get("hostapi", -1))
            host_apis = sd.query_hostapis()
            if 0 <= host_index < len(host_apis):
                self.output_device_host_api = str(dict(host_apis[host_index]).get("name", ""))
        except (AttributeError, IndexError, TypeError, ValueError, OverflowError):
            self.output_device_host_api = ""
        try:
            self.output_default_samplerate = float(info.get("default_samplerate", 0.0) or 0.0)
        except (TypeError, ValueError, OverflowError):
            self.output_default_samplerate = 0.0
        # Stereo is preferred. A mono-only device is supported by downmixing
        # the prebuilt stereo block in the PortAudio callback.
        self._output_channels = 1 if max_channels == 1 else 2
        check_settings = getattr(sd, "check_output_settings", None)
        if callable(check_settings):
            try:
                check_settings(
                    device=self.output_device,
                    channels=self._output_channels,
                    dtype="float32",
                    samplerate=self.sample_rate,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"音声出力デバイス「{self.output_device_name}」は "
                    f"{self.sample_rate} Hz / {self._output_channels}ch に対応していません: {exc}"
                ) from exc

    @staticmethod
    def list_output_devices() -> list[dict[str, Any]]:
        """Return output-capable PortAudio devices with their host API names."""
        if sd is None:
            return []
        try:
            devices = sd.query_devices()
            host_apis = sd.query_hostapis()
        except Exception:
            return []
        result: list[dict[str, Any]] = []
        for index, raw in enumerate(devices):
            info = dict(raw)
            if int(info.get("max_output_channels", 0) or 0) <= 0:
                continue
            host_index = int(info.get("hostapi", -1))
            host_name = ""
            if 0 <= host_index < len(host_apis):
                host_name = str(dict(host_apis[host_index]).get("name", ""))
            result.append({
                "index": index,
                "name": str(info.get("name", f"Output {index}")),
                "host_api": host_name,
                "channels": int(info.get("max_output_channels", 0) or 0),
                "default_samplerate": float(info.get("default_samplerate", 0.0) or 0.0),
            })
        return result

    @staticmethod
    def resolve_output_device(
        devices: Iterable[dict[str, Any]],
        preferred_index: int | None,
        preferred_name: str = "",
        preferred_host_api: str = "",
    ) -> int | None:
        """Resolve a persisted device by stable name/API before trusting its old index."""
        available = list(devices)
        if preferred_index is None:
            # An explicit OS-default preference must continue following OS changes.
            return None
        if preferred_name:
            matching = [
                device for device in available
                if str(device.get("name", "")) == preferred_name
                and (
                    not preferred_host_api
                    or str(device.get("host_api", "")) == preferred_host_api
                )
            ]
            if matching:
                try:
                    return int(matching[0]["index"])
                except (KeyError, TypeError, ValueError, OverflowError):
                    return None
            # Do not silently route to a different device after a named output was removed.
            return None
        if preferred_index is None:
            return None
        for device in available:
            try:
                if int(device.get("index", -1)) == int(preferred_index):
                    return int(preferred_index)
            except (TypeError, ValueError, OverflowError):
                continue
        return None

    def _configure_track_effects(self, tracks: Iterable[Any]) -> None:
        """Create or update one independent processor for each project track."""
        previous = self._track_effects
        processors: Dict[int, MasterEffectsProcessor] = {}
        for track in tracks:
            track_id = id(track)
            chain = normalize_effect_chain(getattr(track, "effects", []) or [])
            processor = previous.get(track_id)
            if processor is None:
                processor = MasterEffectsProcessor(self.sample_rate, chain)
            else:
                processor.set_chain(chain, preserve_state=True)
            processors[track_id] = processor
        self._track_effects = processors

    def _calculate_track_total_frames(
        self,
        sources: Dict[int, _TrackSource],
    ) -> int:
        total = 0
        for track_id, source in sources.items():
            processor = self._track_effects.get(track_id)
            tail = processor.tail_frames if processor is not None else 0
            total = max(total, source.output_frames + tail)
        return total

    def set_track_effect_chain(
        self,
        track: Any,
        chain: Any,
        *,
        preserve_state: bool = True,
    ) -> list[dict[str, Any]]:
        """Replace one track's effect chain without changing the master bus."""
        normalized = normalize_effect_chain(chain)
        with self._lock:
            track.effects = normalized
            processor = self._track_effects.get(id(track))
            if processor is None:
                processor = MasterEffectsProcessor(self.sample_rate, normalized)
                self._track_effects[id(track)] = processor
            else:
                processor.set_chain(normalized, preserve_state=preserve_state)
            self._track_total_frames = self._calculate_track_total_frames(self._sources)
            self._total_frames = self._track_total_frames + self._effects.tail_frames
            self._position = min(self._position, self._total_frames)
        return normalized

    def get_track_effect_chain(self, track: Any) -> list[dict[str, Any]]:
        """Return a detached effect-chain snapshot for a single track."""
        with self._lock:
            processor = self._track_effects.get(id(track))
            return processor.get_chain() if processor is not None else []

    def _reset_effect_histories(self) -> None:
        self._effects.reset()
        for processor in self._track_effects.values():
            processor.reset()

    def configure_output(
        self,
        *,
        output_device: int | None,
        sample_rate: int,
        block_size: int,
    ) -> dict[str, Any]:
        """Safely reconfigure PortAudio while preserving transport position."""
        try:
            requested_rate = int(sample_rate)
            requested_block = int(block_size)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("sample_rate and block_size must be integers") from exc
        if requested_rate not in self.SUPPORTED_SAMPLE_RATES:
            raise ValueError(
                f"Unsupported sample rate: {requested_rate}; "
                f"expected one of {self.SUPPORTED_SAMPLE_RATES}"
            )
        if requested_block not in self.SUPPORTED_BLOCK_SIZES:
            raise ValueError(
                f"Unsupported audio block size: {requested_block}; "
                f"expected one of {self.SUPPORTED_BLOCK_SIZES}"
            )
        if output_device is not None:
            if isinstance(output_device, bool):
                raise ValueError("output_device must be a device index or None")
            try:
                output_device = int(output_device)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError("output_device must be a device index or None") from exc
            if output_device < 0:
                raise ValueError("output_device must be a non-negative device index")

        with self._transport_lock:
            was_playing = self.is_playing
            was_started = self._stream is not None or self._producer_thread is not None
            position_sec = self.position_sec
            old_state = {
                "sample_rate": self.sample_rate,
                "block_size": self.block_size,
                "output_device": self.output_device,
                "effects": self._effects,
                "track_effects": self._track_effects,
                "sources": self._sources,
                "track_total_frames": self._track_total_frames,
                "total_frames": self._total_frames,
                "position": self._position,
                "device_name": self.output_device_name,
                "device_host_api": self.output_device_host_api,
                "default_rate": self.output_default_samplerate,
                "latency": self.output_latency_seconds,
                "channels": self._output_channels,
            }
            if was_started:
                self._shutdown_playback(reset_position=False)

            try:
                rate_changed = requested_rate != self.sample_rate
                self.sample_rate = requested_rate
                self.block_size = requested_block
                self.output_device = output_device
                self.output_latency_seconds = 0.0
                if rate_changed:
                    master_chain = old_state["effects"].get_chain()
                    self._effects = MasterEffectsProcessor(self.sample_rate, master_chain)
                    self._track_effects = {}
                    if self._tracks:
                        self.set_tracks(self._tracks)
                    else:
                        self._sources = {}
                        self._track_total_frames = 0
                        self._total_frames = self._effects.tail_frames
                else:
                    self._track_total_frames = self._calculate_track_total_frames(self._sources)
                    self._total_frames = self._track_total_frames + self._effects.tail_frames
                self._position = min(
                    max(0, int(round(position_sec * self.sample_rate))),
                    self._total_frames,
                )
                self._probe_output_device()
                if was_playing:
                    self.play(position_sec)
                return self.get_diagnostics()
            except Exception:
                if self._stream is not None or self._producer_thread is not None:
                    try:
                        self._shutdown_playback(reset_position=False)
                    except Exception:
                        pass
                self.sample_rate = old_state["sample_rate"]
                self.block_size = old_state["block_size"]
                self.output_device = old_state["output_device"]
                self._effects = old_state["effects"]
                self._track_effects = old_state["track_effects"]
                self._sources = old_state["sources"]
                self._track_total_frames = old_state["track_total_frames"]
                self._total_frames = old_state["total_frames"]
                self._position = old_state["position"]
                self.output_device_name = old_state["device_name"]
                self.output_device_host_api = old_state["device_host_api"]
                self.output_default_samplerate = old_state["default_rate"]
                self.output_latency_seconds = old_state["latency"]
                self._output_channels = old_state["channels"]
                if was_playing:
                    try:
                        self.play(position_sec)
                    except Exception:
                        pass
                raise

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
            self._configure_track_effects(new_tracks)
            self._tracks = new_tracks
            self._sources = sources
            self._track_total_frames = self._calculate_track_total_frames(sources)
            self._total_frames = self._track_total_frames + self._effects.tail_frames
            self.last_load_errors = load_errors
            self.last_playback_errors = []

    def update_tracks(self, tracks: Iterable[Any]) -> None:
        """Update mute/solo/volume/pan state without reloading audio files."""
        new_tracks = list(tracks)
        new_ids = {id(track) for track in new_tracks}
        with self._lock:
            self._configure_track_effects(new_tracks)
            self._tracks = new_tracks
            self._sources = {
                track_id: source
                for track_id, source in self._sources.items()
                if track_id in new_ids
            }
            self._track_total_frames = self._calculate_track_total_frames(self._sources)
            self._total_frames = self._track_total_frames + self._effects.tail_frames

    def restore_tracks_preserving_sources(self, tracks: Iterable[Any]) -> None:
        """Rebind cloned project-history tracks to their already-loaded audio sources.

        Undo/Redo deep-copies project track containers. Rebinding by resolved audio
        path keeps those history restores from losing their source readers or
        leaving effect processors attached to the previous track objects.
        """
        new_tracks = list(tracks)
        with self._lock:
            available: Dict[str, list[_TrackSource]] = {}
            for source in self._sources.values():
                key = os.path.normcase(os.path.abspath(source.path))
                available.setdefault(key, []).append(source)

            rebound: Dict[int, _TrackSource] = {}
            processors: Dict[int, MasterEffectsProcessor] = {}
            for track in new_tracks:
                track_id = id(track)
                chain = normalize_effect_chain(getattr(track, "effects", []) or [])
                processors[track_id] = MasterEffectsProcessor(self.sample_rate, chain)
                track_path = self._track_path(track)
                if not track_path:
                    continue
                key = os.path.normcase(os.path.abspath(track_path))
                candidates = available.get(key)
                if not candidates:
                    continue
                source = candidates.pop(0)
                rebound[track_id] = _TrackSource(
                    track=track,
                    path=source.path,
                    frames=source.frames,
                    sample_rate=source.sample_rate,
                    output_frames=source.output_frames,
                    data=source.data,
                )

            self._tracks = new_tracks
            self._sources = rebound
            self._track_effects = processors
            self._track_total_frames = self._calculate_track_total_frames(rebound)
            self._total_frames = self._track_total_frames + self._effects.tail_frames
            self._position = min(self._position, self._total_frames)

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
            # The producer renders blocks ahead of the device callback. A pause or
            # stream restart discards queued blocks, so the processor's state may be
            # ahead of the audible position. Reset at every new producer segment to
            # avoid resuming with stale compressor/reverb/ EQ history.
            self._reset_effect_histories()
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
                device=self.output_device,
                dtype="float32",
                blocksize=self.block_size,
                callback=self._audio_callback,
            )
            self._stream = stream
            stream.start()
            raw_latency = getattr(stream, "latency", 0.0)
            if isinstance(raw_latency, (tuple, list)):
                latency_values = [
                    float(value) for value in raw_latency
                    if value is not None and float(value) >= 0.0
                ]
                self.output_latency_seconds = max(latency_values, default=0.0)
            else:
                try:
                    self.output_latency_seconds = max(0.0, float(raw_latency or 0.0))
                except (TypeError, ValueError, OverflowError):
                    self.output_latency_seconds = 0.0
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
            self._track_effects = {id(track): MasterEffectsProcessor(self.sample_rate)}
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
            self._reset_effect_histories()
        if was_playing:
            self.play()

    def stop(self) -> None:
        """Stop playback and reset the transport and effect histories."""
        self._shutdown_playback(reset_position=True)
        with self._lock:
            self._reset_effect_histories()

    def _shutdown_playback(self, reset_position: bool) -> None:
        self._producer_stop.set()
        stream = self._stream
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                # stop() can report an already-stopped device; close() below
                # is still attempted and is the authoritative release step.
                pass
            try:
                stream.close()
            except Exception as exc:
                # Keep the stream referenced if close failed: its callback may
                # still be running and dereference this mixer.
                self._stream = stream
                raise RuntimeError(
                    f"Audio stream could not be closed safely: {exc}"
                ) from exc

            # PortAudio stream state properties are not queried after close:
            # some backends reject any access once their native handle is null.
            # A successful close guarantees the callback has been detached.
            self._stream = None

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
        """Mix independent track chains first, then the shared master bus."""
        with self._lock:
            tracks = list(self._tracks)
            active_sources = dict(self._sources) if sources is None else sources
            track_effects = dict(self._track_effects)
            effects = self._effects
        active_readers = readers or {}
        failed = failed_sources if failed_sources is not None else set()
        mix = np.zeros((frames, 2), dtype=np.float32)
        solo_exists = any(bool(getattr(track, "is_solo", False)) for track in tracks)

        for track in tracks:
            track_id = id(track)
            source = active_sources.get(track_id)
            if source is None or track_id in failed:
                continue

            track_processor = track_effects.get(track_id)
            track_tail = track_processor.tail_frames if track_processor is not None else 0
            if start_frame >= source.output_frames + track_tail:
                continue
            if not self._track_is_active(track, solo_exists):
                continue

            block = np.zeros((frames, 2), dtype=np.float32)
            if start_frame < source.output_frames:
                count = min(frames, source.output_frames - start_frame)
                try:
                    if source.data is not None:
                        chunk = source.data[start_frame : start_frame + count]
                    else:
                        reader = active_readers.get(track_id)
                        if reader is None:
                            continue
                        chunk = self._read_file_block(reader, source, start_frame, count)
                    if len(chunk) == 0:
                        continue
                    block[:len(chunk)] = self._to_stereo(chunk)
                except Exception as exc:
                    if failed_sources is not None:
                        failed_sources.add(track_id)
                    self._record_playback_error(f"{source.path}: {exc}")
                    continue

            # Feed silence after a source ends so the track's own reverb can ring out.
            track_audio = track_processor.process(block) if track_processor is not None else block
            gain = float(np.clip(getattr(track, "volume", 1.0), 0.0, 1.0))
            pan = float(np.clip(getattr(track, "pan", 0.0), -1.0, 1.0))
            left_gain, right_gain = self._pan_gains(pan)
            mix[:, 0] += track_audio[:, 0] * gain * left_gain
            mix[:, 1] += track_audio[:, 1] * gain * right_gain

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
