"""UTAU P2P3-style automatic pitch shaping for the desktop editor.

This module only writes note pitch automation fields. It never changes the
synthesis engine, audio buffers, or WAV export path.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, List, Tuple


_RESTS = {
    "r", "r_", "r_0", "[r]", "息", "br", "pau", "sil", "吸", "吸気",
    "息吸い", "", " ", "　", "休", "休符", "・", "-", "ー", "~", "null",
    "っ", "ッ", "_っ", "_ッ", "っ_", "ッ_",
}


def _is_rest(lyric: str) -> bool:
    value = str(lyric or "").strip().lower()
    return (
        value in _RESTS
        or re.fullmatch(r"br[0-9]*", value) is not None
        or re.fullmatch(r"息[0-9]*", value) is not None
        or re.fullmatch(r"吸[0-9]*", value) is not None
        or re.fullmatch(r"[_]?[っッ]+[_]?", value) is not None
    )


def _round(value: float, digits: int = 2) -> float:
    factor = 10 ** digits
    return round(value * factor) / factor


def _generate_points(
    current_pitch: int,
    previous_pitch: int | None,
    duration_ms: float,
) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = []
    port_ms = min(max(50.0, 105.0), max(40.0, duration_ms * 0.45))

    if previous_pitch is not None and previous_pitch != current_pitch:
        delta = previous_pitch - current_pitch
        start_ms = -round(port_ms * 0.45)
        steps = 6
        for step in range(steps + 1):
            t = step / steps
            s_curve = 0.5 * (1.0 + math.cos(math.pi * t))
            points.append((
                start_ms + round(t * port_ms),
                _round(delta * s_curve),
            ))
        overshoot_end = start_ms + port_ms + 50.0
        overshoot = -1.0 * (1 if delta > 0 else -1) * min(0.18, max(0.08, abs(delta) * 0.035))
        if overshoot != 0 and overshoot_end < duration_ms - 15:
            points.append((round(start_ms + port_ms + 16), _round(overshoot)))
            points.append((round(overshoot_end), 0.0))
    elif previous_pitch is None and duration_ms >= 160:
        scoop = -0.30
        scoop_ms = min(55, round(duration_ms * 0.22))
        points.extend([
            (0.0, _round(scoop)),
            (round(scoop_ms * 0.5), _round(scoop * 0.35)),
            (float(scoop_ms), 0.0),
        ])
    else:
        points.append((0.0, 0.0))

    vibrato_depth = 0.32
    vibrato_rate = 5.5
    vibrato_start = max(port_ms + 30.0, duration_ms * 0.38)
    vibrato_end = max(vibrato_start + 40.0, duration_ms - 25.0)
    if vibrato_end - vibrato_start >= 150.0 and vibrato_depth > 0:
        mid_ms = round((points[-1][0] + vibrato_start) / 2.0)
        if mid_ms > points[-1][0] + 20:
            points.append((float(mid_ms), 0.0))
        points.append((round(vibrato_start), 0.0))
        vibrato_duration = vibrato_end - vibrato_start
        step_ms = max(16, round(1000.0 / (vibrato_rate * 8.0)))
        total_steps = math.floor(vibrato_duration / step_ms)
        attack_ms = min(160.0, vibrato_duration * 0.4)
        for index in range(1, total_steps + 1):
            offset = vibrato_start + index * step_ms
            elapsed = offset - vibrato_start
            envelope = elapsed / attack_ms if elapsed < attack_ms else 1.0
            phase = (2.0 * math.pi * vibrato_rate * elapsed) / 1000.0
            points.append((
                round(offset),
                _round(math.sin(phase) * vibrato_depth * envelope),
            ))
        points.append((round(duration_ms), 0.0))
    elif duration_ms > points[-1][0] + 10:
        points.append((round(duration_ms), 0.0))

    return sorted(points, key=lambda item: item[0])


def _serialize(points: Iterable[Tuple[float, float]]) -> tuple[str, str, str]:
    ordered = sorted((float(x), float(y)) for x, y in points)
    if not ordered:
        return "0;0", "", ""
    pbs = f"{round(ordered[0][0])};{_round(ordered[0][1] * 10)}"
    widths: List[str] = []
    heights: List[str] = []
    previous_ms = ordered[0][0]
    for offset_ms, semitone in ordered[1:]:
        widths.append(str(max(1, round(offset_ms - previous_ms))))
        heights.append(str(_round(semitone * 10)))
        previous_ms = offset_ms
    return pbs, ",".join(widths), ",".join(heights)


def apply_p2p3_autotune(notes: Iterable[Any], tempo_bpm: float) -> int:
    """Apply the Web editor's P2P3/soft vibrato profile to all notes in a track."""
    try:
        tempo = float(tempo_bpm)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Tempo must be a finite positive number") from exc
    if not math.isfinite(tempo) or tempo <= 0:
        raise ValueError("Tempo must be a finite positive number")

    ordered = sorted(
        list(notes),
        key=lambda note: float(getattr(note, "start_time", 0.0) or 0.0),
    )
    if not ordered:
        return 0

    timeline: List[dict[str, Any]] = []
    for note in ordered:
        start = float(getattr(note, "start_time", 0.0) or 0.0)
        duration = float(getattr(note, "duration", 0.0) or 0.0)
        if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration <= 0:
            raise ValueError("A note has invalid start time or duration")
        pitch = int(getattr(note, "note_number", 60))
        tick = round(start * tempo / 60.0 * 480.0)
        length = max(1, round(duration * tempo / 60.0 * 480.0))
        timeline.append({
            "note": note,
            "lyric": str(getattr(note, "lyric", getattr(note, "lyrics", "")) or ""),
            "pitch": pitch,
            "tick": tick,
            "length": length,
            "duration_ms": duration * 1000.0,
        })

    for index, item in enumerate(timeline):
        note = item["note"]
        if _is_rest(item["lyric"]):
            setattr(note, "_ust_pbs", "0;0")
            setattr(note, "_ust_pbw", "")
            setattr(note, "_ust_pby", "")
            continue

        previous = timeline[index - 1] if index > 0 else None
        previous_pitch: int | None = None
        if previous is not None and not _is_rest(previous["lyric"]):
            gap_ticks = item["tick"] - (previous["tick"] + previous["length"])
            if -120 <= gap_ticks <= 240:
                previous_pitch = previous["pitch"]

        points = _generate_points(
            item["pitch"], previous_pitch, item["duration_ms"]
        )
        pbs, pbw, pby = _serialize(points)
        setattr(note, "_ust_pbs", pbs)
        setattr(note, "_ust_pbw", pbw)
        setattr(note, "_ust_pby", pby)

    return len(timeline)
