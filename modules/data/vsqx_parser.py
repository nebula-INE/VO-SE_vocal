"""VOCALOID 3/4 VSQX parser for VO-SE."""
from __future__ import annotations
import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import List, Optional, Tuple
from modules.data.data_models import NoteEvent

@dataclass(frozen=True)
class VsqxProject:
    project_name: str
    tempo: float
    notes: List[NoteEvent]

def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()

def _text(node: Optional[ET.Element], default: str = "") -> str:
    return default if node is None or node.text is None else node.text.strip()

def _direct(node: ET.Element, name: str) -> Optional[ET.Element]:
    wanted = name.lower()
    for child in list(node):
        if _local_name(child.tag) == wanted:
            return child
    return None

def _first(node: ET.Element, name: str) -> Optional[ET.Element]:
    wanted = name.lower()
    for child in node.iter():
        if _local_name(child.tag) == wanted:
            return child
    return None

def _int(node: Optional[ET.Element], default: int = 0) -> int:
    try:
        return int(round(float(_text(node))))
    except (TypeError, ValueError):
        return default

def _float(node: Optional[ET.Element], default: float = 0.0) -> float:
    try:
        return float(_text(node))
    except (TypeError, ValueError):
        return default

def _tempos(root: ET.Element) -> List[Tuple[int, float]]:
    master = _first(root, "mastertrack")
    events: List[Tuple[int, float]] = []
    if master is not None:
        for node in master.iter():
            if _local_name(node.tag) != "tempo":
                continue
            tick = max(0, _int(_direct(node, "posTick"), 0))
            raw = _float(_direct(node, "bpm"), 12000.0)
            bpm = raw / 100.0 if raw > 1000 else raw
            if bpm > 0:
                events.append((tick, bpm))
    if not events:
        return [(0, 120.0)]
    return sorted({tick: bpm for tick, bpm in events}.items())

def _tick_to_seconds(tick: int, tempos: List[Tuple[int, float]], resolution: int) -> float:
    seconds = 0.0
    previous = 0
    bpm = tempos[0][1] if tempos else 120.0
    for tempo_tick, next_bpm in tempos[1:]:
        if tempo_tick >= tick:
            break
        if tempo_tick > previous:
            seconds += (tempo_tick - previous) / resolution * 60.0 / bpm
            previous = tempo_tick
        bpm = next_bpm
    if tick > previous:
        seconds += (tick - previous) / resolution * 60.0 / bpm
    return seconds

def parse_vsqx(file_path: str) -> VsqxProject:
    if not os.path.isfile(file_path):
        raise FileNotFoundError(file_path)
    try:
        root = ET.parse(file_path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"VSQX XMLを解析できません: {exc}") from exc

    master = _first(root, "mastertrack")
    resolution = max(1, _int(_first(master, "resolution"), 480)) if master is not None else 480
    tempos = _tempos(root)
    project_name = "VOCALOID Project"
    track = _first(root, "vstrack")
    if track is not None:
        name = _direct(track, "name")
        if _text(name):
            project_name = _text(name)

    notes: List[NoteEvent] = []
    for part in root.iter():
        if _local_name(part.tag) != "musicalpart":
            continue
        part_tick = _int(_direct(part, "posTick"), 0)
        for note in part.iter():
            if _local_name(note.tag) != "note":
                continue
            pos = _int(_direct(note, "posTick"), 0)
            length = max(1, _int(_direct(note, "durTick"), 480))
            note_num = max(0, min(127, _int(_direct(note, "noteNum"), 60)))
            lyric = _text(_direct(note, "lyric"), "あ") or "あ"
            start_tick = max(0, part_tick + pos)
            start = _tick_to_seconds(start_tick, tempos, resolution)
            end = _tick_to_seconds(start_tick + length, tempos, resolution)
            notes.append(NoteEvent(note_number=note_num, start_time=start,
                                   duration=max(1e-6, end - start), lyric=lyric))

    # Accept simplified VSQX-like exports that omit musicalPart.
    if not notes:
        for note in root.iter():
            if _local_name(note.tag) != "note":
                continue
            pos = _int(_direct(note, "posTick"), 0)
            length = max(1, _int(_direct(note, "durTick"), 480))
            note_num = max(0, min(127, _int(_direct(note, "noteNum"), 60)))
            lyric = _text(_direct(note, "lyric"), "あ") or "あ"
            start = _tick_to_seconds(pos, tempos, resolution)
            end = _tick_to_seconds(pos + length, tempos, resolution)
            notes.append(NoteEvent(note_number=note_num, start_time=start,
                                   duration=max(1e-6, end - start), lyric=lyric))
    notes.sort(key=lambda n: n.start_time)
    return VsqxProject(project_name=project_name, tempo=tempos[0][1], notes=notes)
