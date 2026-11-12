"""Deterministic stereo master-bus effects used by desktop playback and mixdown.

The processor is deliberately downstream of the vocal renderer. An empty chain
is a strict no-op, preserving the existing mixer output bit-for-bit.
"""
from __future__ import annotations

import math
import threading
from typing import Any

import numpy as np
from scipy.signal import lfilter


EFFECT_DEFAULTS: dict[str, dict[str, float]] = {
    "compressor": {
        "threshold_db": -18.0,
        "ratio": 4.0,
        "attack_ms": 10.0,
        "release_ms": 100.0,
        "makeup_gain_db": 0.0,
    },
    "reverb": {
        "room_size": 0.45,
        "damping": 0.5,
        "wet": 0.12,
        "dry": 0.9,
        "width": 0.75,
    },
    "eq": {
        "frequency_hz": 1000.0,
        "gain_db": 0.0,
        "q": 1.0,
    },
}

EFFECT_RANGES: dict[str, dict[str, tuple[float, float]]] = {
    "compressor": {
        "threshold_db": (-60.0, 0.0),
        "ratio": (1.0, 20.0),
        "attack_ms": (1.0, 200.0),
        "release_ms": (10.0, 1000.0),
        "makeup_gain_db": (-12.0, 12.0),
    },
    "reverb": {
        "room_size": (0.0, 0.9),
        "damping": (0.0, 0.95),
        "wet": (0.0, 1.0),
        "dry": (0.0, 1.0),
        "width": (0.0, 1.0),
    },
    "eq": {
        "frequency_hz": (20.0, 20000.0),
        "gain_db": (-18.0, 18.0),
        "q": (0.2, 10.0),
    },
}


def normalize_effect_chain(chain: Any) -> list[dict[str, Any]]:
    """Validate and copy a serialized effect chain.

    Unknown effect types, duplicate IDs, non-finite numbers and values outside
    documented parameter ranges are rejected instead of silently altered.
    """
    if chain is None:
        return []
    if not isinstance(chain, list):
        raise ValueError("effect_chain は配列である必要があります。")
    if len(chain) > 16:
        raise ValueError("エフェクトは最大16個までです。")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(chain):
        if not isinstance(raw, dict):
            raise ValueError(f"エフェクト {index + 1} の形式が不正です。")
        raw_type = raw.get("type", "")
        if not isinstance(raw_type, str):
            raise ValueError(f"エフェクト {index + 1} の type は文字列である必要があります。")
        effect_type = raw_type.strip().lower()
        if effect_type not in EFFECT_DEFAULTS:
            raise ValueError(f"未対応のエフェクトです: {effect_type or '(empty)'}")
        raw_id = raw.get("id", f"effect_{index + 1}")
        if not isinstance(raw_id, str):
            raise ValueError(f"エフェクト {index + 1} のIDは文字列である必要があります。")
        effect_id = raw_id.strip()
        if not effect_id or len(effect_id) > 128 or effect_id in seen_ids:
            raise ValueError(f"エフェクト {index + 1} のIDが不正または重複しています。")
        seen_ids.add(effect_id)
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError(f"エフェクト {effect_id} の enabled は真偽値である必要があります。")

        raw_params = raw.get("params", {})
        if not isinstance(raw_params, dict):
            raise ValueError(f"エフェクト {effect_id} の params はオブジェクトである必要があります。")
        unknown = set(raw_params) - set(EFFECT_DEFAULTS[effect_type])
        if unknown:
            raise ValueError(
                f"エフェクト {effect_id} に未対応のパラメータがあります: {', '.join(sorted(unknown))}"
            )

        params: dict[str, float] = {}
        for name, default in EFFECT_DEFAULTS[effect_type].items():
            value = raw_params.get(name, default)
            try:
                numeric = float(value)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{effect_id}.{name} は数値である必要があります。") from exc
            if not math.isfinite(numeric):
                raise ValueError(f"{effect_id}.{name} に無効な数値があります。")
            minimum, maximum = EFFECT_RANGES[effect_type][name]
            if numeric < minimum or numeric > maximum:
                raise ValueError(
                    f"{effect_id}.{name} は {minimum:g}〜{maximum:g} の範囲で指定してください。"
                )
            params[name] = numeric

        normalized.append({
            "id": effect_id,
            "type": effect_type,
            "enabled": enabled,
            "params": params,
        })
    return normalized


def _db_to_linear(value_db: float) -> float:
    return float(10.0 ** (value_db / 20.0))


class MasterEffectsProcessor:
    """Stateful stereo processor. Call process() sequentially on one stream."""

    _REVERB_LEFT_DELAYS = (0.0297, 0.0371, 0.0411, 0.0437)
    _REVERB_RIGHT_DELAYS = (0.0313, 0.0397, 0.0439, 0.0471)

    def __init__(self, sample_rate: int, chain: Any = None) -> None:
        self.sample_rate = int(sample_rate)
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        self.chain: list[dict[str, Any]] = []
        self._states: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()
        self.set_chain(chain or [])

    def set_chain(self, chain: Any, *, preserve_state: bool = False) -> None:
        """Validate a new chain and optionally retain state for matching effects."""
        normalized = normalize_effect_chain(chain)
        with self._lock:
            previous_types = {
                item["id"]: item["type"] for item in self.chain
            }
            previous_states = self._states
            new_states: dict[str, dict[str, Any]] = {}
            for effect in normalized:
                effect_id = effect["id"]
                effect_type = effect["type"]
                if preserve_state and previous_types.get(effect_id) == effect_type:
                    new_states[effect_id] = previous_states[effect_id]
                elif effect_type == "compressor":
                    new_states[effect_id] = {"envelope": 0.0}
                elif effect_type == "eq":
                    new_states[effect_id] = {
                        "zi": np.zeros((2, 2), dtype=np.float64)
                    }
                elif effect_type == "reverb":
                    new_states[effect_id] = self._new_reverb_state(effect["params"])
            self.chain = normalized
            self._states = new_states

    def get_chain(self) -> list[dict[str, Any]]:
        """Return a validated detached copy of the current chain."""
        with self._lock:
            return normalize_effect_chain(self.chain)

    def reset(self) -> None:
        """Clear delay/envelope histories, for a new transport start or seek."""
        with self._lock:
            current = [dict(item) for item in self.chain]
            self.set_chain(current, preserve_state=False)

    @property
    def tail_frames(self) -> int:
        with self._lock:
            tail = 0
            max_delay = max(self._REVERB_LEFT_DELAYS + self._REVERB_RIGHT_DELAYS)
            for effect in self.chain:
                if effect["type"] != "reverb" or not effect["enabled"]:
                    continue
                params = effect["params"]
                if params["wet"] <= 0.0:
                    continue
                feedback = 0.18 + params["room_size"] * 0.62
                decay_seconds = max_delay * math.log(0.001) / math.log(feedback)
                # Serial reverbs have additive decay windows; retain the complete
                # tail rather than truncating the later stages at the longest one.
                tail += int(min(4.0, decay_seconds) * self.sample_rate)
            return tail

    def process(self, audio: np.ndarray) -> np.ndarray:
        """Apply the enabled chain to a float stereo block.

        The caller owns final peak clipping. With no enabled effects, the exact
        input array is returned without copying or arithmetic.
        """
        with self._lock:
            if not any(item["enabled"] for item in self.chain):
                return audio
            if audio.ndim != 2 or audio.shape[1] != 2:
                raise ValueError("Master effects require a two-channel audio block.")
            if len(audio) == 0:
                return audio

            output = np.asarray(audio, dtype=np.float32).copy()
            for effect in self.chain:
                if not effect["enabled"]:
                    continue
                effect_type = effect["type"]
                params = effect["params"]
                state = self._states[effect["id"]]
                if effect_type == "compressor":
                    output = self._process_compressor(output, params, state)
                elif effect_type == "reverb":
                    output = self._process_reverb(output, params, state)
                elif effect_type == "eq":
                    output = self._process_eq(output, params, state)
            return output

    def _process_compressor(
        self, audio: np.ndarray, params: dict[str, float], state: dict[str, Any]
    ) -> np.ndarray:
        detector = np.max(np.abs(audio), axis=1).astype(np.float64, copy=False)
        attack = math.exp(-1.0 / (self.sample_rate * params["attack_ms"] / 1000.0))
        release = math.exp(-1.0 / (self.sample_rate * params["release_ms"] / 1000.0))
        envelope = float(state["envelope"])
        smoothed = np.empty(len(detector), dtype=np.float64)
        # The linked-stereo detector preserves the image while using separate
        # attack and release constants without block-boundary discontinuities.
        for index, level in enumerate(detector):
            coefficient = attack if level > envelope else release
            envelope = coefficient * envelope + (1.0 - coefficient) * float(level)
            smoothed[index] = envelope
        state["envelope"] = envelope

        level_db = 20.0 * np.log10(np.maximum(smoothed, 1e-9))
        above = level_db > params["threshold_db"]
        gain_db = np.zeros_like(level_db)
        gain_db[above] = (
            params["threshold_db"]
            + (level_db[above] - params["threshold_db"]) / params["ratio"]
            - level_db[above]
        )
        gain_db += params["makeup_gain_db"]
        gain = np.power(10.0, gain_db / 20.0).astype(np.float32)
        return audio * gain[:, None]

    def _new_reverb_state(self, params: dict[str, float]) -> dict[str, Any]:
        delay_frames = []
        for channel, delays in enumerate((self._REVERB_LEFT_DELAYS, self._REVERB_RIGHT_DELAYS)):
            channel_delays = []
            for seconds in delays:
                delay = max(1, int(round(seconds * self.sample_rate)))
                channel_delays.append({
                    "delay": delay,
                    "position": 0,
                    "input_ring": np.zeros(delay, dtype=np.float64),
                    "output_ring": np.zeros(delay, dtype=np.float64),
                    "damping_zi": np.zeros(1, dtype=np.float64),
                })
            delay_frames.append(channel_delays)
        return {"combs": delay_frames}

    def _process_reverb(
        self, audio: np.ndarray, params: dict[str, float], state: dict[str, Any]
    ) -> np.ndarray:
        feedback = 0.18 + params["room_size"] * 0.62
        damping = params["damping"] * 0.9
        wet_signal = np.zeros_like(audio, dtype=np.float64)
        for channel in range(2):
            channel_wet = np.zeros(len(audio), dtype=np.float64)
            for comb in state["combs"][channel]:
                echoed = self._process_feedback_comb(
                    audio[:, channel].astype(np.float64, copy=False),
                    comb,
                    feedback,
                )
                damped, damping_zi = lfilter(
                    [1.0 - damping], [1.0, -damping], echoed,
                    zi=comb["damping_zi"],
                )
                comb["damping_zi"] = damping_zi
                channel_wet += damped / 4.0
            wet_signal[:, channel] = channel_wet

        mid = 0.5 * (wet_signal[:, 0] + wet_signal[:, 1])
        side = 0.5 * (wet_signal[:, 0] - wet_signal[:, 1]) * params["width"]
        wet_signal[:, 0] = mid + side
        wet_signal[:, 1] = mid - side
        result = (
            audio.astype(np.float64, copy=False) * params["dry"]
            + wet_signal * params["wet"]
        )
        return result.astype(np.float32, copy=False)

    @staticmethod
    def _process_feedback_comb(
        samples: np.ndarray, state: dict[str, Any], feedback: float
    ) -> np.ndarray:
        """Run a delayed feedback comb in O(frames), not O(frames * delay)."""
        delay = int(state["delay"])
        position = int(state["position"])
        input_ring = state["input_ring"]
        output_ring = state["output_ring"]
        output = np.empty(len(samples), dtype=np.float64)

        # Each vector segment is at most one delay long, so a ring slot is read
        # before it is overwritten. This keeps long offline blocks efficient.
        for offset in range(0, len(samples), delay):
            count = min(delay, len(samples) - offset)
            indices = (position + np.arange(count)) % delay
            delayed_input = input_ring[indices].copy()
            delayed_output = output_ring[indices].copy()
            result = delayed_input + feedback * delayed_output
            input_ring[indices] = samples[offset : offset + count]
            output_ring[indices] = result
            output[offset : offset + count] = result
            position = (position + count) % delay

        state["position"] = position
        return output

    def _process_eq(
        self, audio: np.ndarray, params: dict[str, float], state: dict[str, Any]
    ) -> np.ndarray:
        frequency = min(params["frequency_hz"], self.sample_rate * 0.49)
        omega = 2.0 * math.pi * frequency / self.sample_rate
        cosine = math.cos(omega)
        alpha = math.sin(omega) / (2.0 * params["q"])
        amplitude = 10.0 ** (params["gain_db"] / 40.0)

        b0 = 1.0 + alpha * amplitude
        b1 = -2.0 * cosine
        b2 = 1.0 - alpha * amplitude
        a0 = 1.0 + alpha / amplitude
        a1 = -2.0 * cosine
        a2 = 1.0 - alpha / amplitude
        numerator = np.asarray([b0, b1, b2], dtype=np.float64) / a0
        denominator = np.asarray([1.0, a1 / a0, a2 / a0], dtype=np.float64)

        result = np.empty_like(audio)
        for channel in range(2):
            filtered, zi = lfilter(
                numerator,
                denominator,
                audio[:, channel].astype(np.float64, copy=False),
                zi=state["zi"][channel],
            )
            state["zi"][channel] = zi
            result[:, channel] = filtered.astype(np.float32, copy=False)
        return result
