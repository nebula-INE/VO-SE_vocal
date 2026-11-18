"""Desktop-side project interchange helpers for SVP and VSQX formats.

The helpers operate on neutral note dictionaries so parsing/export never touches
Qt widgets or the audio renderer.
"""
from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from typing import Any, Dict, Iterable, List

from modules.data.data_models import NoteEvent

SVP_TICK_SCALE = 306250  # Synthesizer V beat-length units per VO-SE quarter-note tick.


def _finite_number(value: Any, field: str, *, minimum: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise ValueError(f"{field} is outside the supported range")
    return number


def parse_svp_json(text: str) -> Dict[str, Any]:
    """Parse a Synthesizer V .svp JSON document into VO-SE NoteEvents."""
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid SVP JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("SVP root must be a JSON object")

    time_data = data.get("time") or {}
    tempo_entries = time_data.get("tempo", []) if isinstance(time_data, dict) else []
    tempo = 120.0
    if isinstance(tempo_entries, list) and tempo_entries:
        first = tempo_entries[0]
        if isinstance(first, dict) and first.get("bpm") is not None:
            tempo = _finite_number(first["bpm"], "SVP tempo", minimum=1.0)
    if tempo <= 0.0 or tempo > 1000.0:
        raise ValueError("SVP tempo must be between 1 and 1000 BPM")

    tracks = data.get("tracks", [])
    if not isinstance(tracks, list):
        raise ValueError("SVP tracks must be an array")

    project_name = "Synthesizer V Project"
    if tracks and isinstance(tracks[0], dict):
        project_name = str(tracks[0].get("name") or project_name)

    parsed: List[tuple[int, NoteEvent]] = []
    for track_index, track in enumerate(tracks):
        if not isinstance(track, dict):
            continue
        groups: List[Dict[str, Any]] = []
        main_group = track.get("mainGroup")
        if isinstance(main_group, dict):
            groups.append(main_group)
        raw_groups = track.get("groups")
        if isinstance(raw_groups, list):
            groups.extend(group for group in raw_groups if isinstance(group, dict))

        for group_index, group in enumerate(groups):
            raw_notes = group.get("notes", [])
            if not isinstance(raw_notes, list):
                continue
            for note_index, raw_note in enumerate(raw_notes):
                if not isinstance(raw_note, dict):
                    continue
                onset_raw = _finite_number(raw_note.get("onset", 0), "SVP note onset", minimum=0)
                duration_raw = _finite_number(raw_note.get("duration", 480), "SVP note duration", minimum=1)
                # SVP stores beat-length values at 147,000,000 units per quarter note.
                if onset_raw > 1_000_000 or duration_raw > 1_000_000:
                    onset = int(round(onset_raw / SVP_TICK_SCALE))
                    duration = int(round(duration_raw / SVP_TICK_SCALE))
                else:
                    onset = int(round(onset_raw))
                    duration = int(round(duration_raw))
                duration = max(1, duration)
                pitch = int(round(_finite_number(raw_note.get("pitch", 60), "SVP note pitch")))
                if not 0 <= pitch <= 127:
                    raise ValueError("SVP note pitch must be in the MIDI range 0..127")
                lyric = str(raw_note.get("lyrics") or raw_note.get("lyric") or "あ").strip() or "あ"
                start_seconds = onset * 60.0 / (tempo * 480.0)
                duration_seconds = duration * 60.0 / (tempo * 480.0)
                note = NoteEvent(
                    note_number=pitch,
                    start_time=start_seconds,
                    duration=duration_seconds,
                    lyric=lyric,
                )
                setattr(note, "_svp_tick", onset)
                setattr(note, "_svp_length", duration)
                parsed.append((onset, note))

    parsed.sort(key=lambda item: item[0])
    if not parsed:
        raise ValueError("SVP contains no usable vocal notes")
    return {
        "project_name": project_name,
        "tempo": tempo,
        "notes": [note for _, note in parsed],
    }


def _note_records(notes: Iterable[Any], tempo: float) -> List[Dict[str, Any]]:
    bpm = _finite_number(tempo, "tempo", minimum=1.0)
    if bpm > 1000.0:
        raise ValueError("tempo must not exceed 1000 BPM")
    records: List[Dict[str, Any]] = []
    for index, note in enumerate(notes):
        start = _finite_number(getattr(note, "start_time", 0.0), f"note {index + 1} start", minimum=0.0)
        duration = _finite_number(getattr(note, "duration", 0.0), f"note {index + 1} duration", minimum=0.0)
        pitch = int(round(_finite_number(getattr(note, "note_number", 60), f"note {index + 1} pitch")))
        if not 0 <= pitch <= 127:
            raise ValueError(f"note {index + 1} pitch is outside MIDI range")
        lyric = str(getattr(note, "lyric", getattr(note, "lyrics", "あ")) or "あ")
        tick = int(round(start * bpm / 60.0 * 480.0))
        length = max(1, int(round(duration * bpm / 60.0 * 480.0)))
        records.append({"tick": tick, "length": length, "pitch": pitch, "lyric": lyric})
    records.sort(key=lambda item: item["tick"])
    return records


def export_svp_json(project_name: str, tempo: float, notes: Iterable[Any]) -> str:
    """Serialize one active VO-SE vocal track as a Synthesizer V .svp document."""
    bpm = _finite_number(tempo, "tempo", minimum=1.0)
    records = _note_records(notes, bpm)
    if not records:
        raise ValueError("There are no notes to export")
    svp_notes = [
        {
            "onset": item["tick"] * SVP_TICK_SCALE,
            "duration": item["length"] * SVP_TICK_SCALE,
            "lyrics": item["lyric"],
            "phonemes": _phoneme(item["lyric"]),
            "pitch": item["pitch"],
            "attributes": {
                "tF0Left": 0,
                "tF0Right": 0,
                "dF0Left": 0,
                "dF0Right": 0,
            },
        }
        for item in records
    ]
    payload = {
        "version": 1,
        "time": {
            "meter": [{"index": 0, "numerator": 4, "denominator": 4}],
            "tempo": [{"position": 0, "bpm": bpm}],
        },
        "library": [],
        "tracks": [
            {
                "name": str(project_name or "VO-SE Vocal"),
                "dispColor": "ff5865f2",
                "mainGroup": {
                    "name": "main",
                    "parameters": {
                        name: {"mode": "linear", "points": []}
                        for name in (
                            "pitchDelta", "vibratoEnv", "loudness", "tension",
                            "breathiness", "voicing", "gender",
                        )
                    },
                    "notes": svp_notes,
                },
            }
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


_KANA_PHONEMES = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "k a", "き": "k i", "く": "k u", "け": "k e", "こ": "k o",
    "さ": "s a", "し": "s i", "す": "s u", "せ": "s e", "そ": "s o",
    "た": "t a", "ち": "tS i", "つ": "ts u", "て": "t e", "と": "t o",
    "な": "n a", "に": "n i", "ぬ": "n u", "ね": "n e", "の": "n o",
    "は": "h a", "ひ": "h i", "ふ": "p u", "へ": "h e", "ほ": "h o",
    "ま": "m a", "み": "m i", "む": "m u", "め": "m e", "も": "m o",
    "や": "j a", "ゆ": "j u", "よ": "j o",
    "ら": "r a", "り": "r i", "る": "r u", "れ": "r e", "ろ": "r o",
    "わ": "w a", "を": "o", "ん": "N",
    "が": "g a", "ぎ": "g i", "ぐ": "g u", "げ": "g e", "ご": "g o",
    "ざ": "z a", "じ": "dZ i", "ず": "z u", "ぜ": "z e", "ぞ": "z o",
    "だ": "d a", "ぢ": "dZ i", "づ": "z u", "で": "d e", "ど": "d o",
    "ば": "b a", "び": "b i", "ぶ": "b u", "べ": "b e", "ぼ": "b o",
    "ぱ": "p a", "ぴ": "p i", "ぷ": "p u", "ぺ": "p e", "ぽ": "p o",
    "きゃ": "k 'a", "きゅ": "k 'u", "きょ": "k 'o",
    "しゃ": "S a", "しゅ": "S u", "しょ": "S o",
    "ちゃ": "tS a", "ちゅ": "tS u", "ちょ": "tS o",
    "にゃ": "n 'a", "にゅ": "n 'u", "にょ": "n 'o",
    "ひゃ": "h 'a", "ひゅ": "h 'u", "ひょ": "h 'o",
    "みゃ": "m 'a", "みゅ": "m 'u", "みょ": "m 'o",
    "りゃ": "r 'a", "りゅ": "r 'u", "りょ": "r 'o",
    "ぎゃ": "g 'a", "ぎゅ": "g 'u", "ぎょ": "g 'o",
    "じゃ": "dZ a", "じゅ": "dZ u", "じょ": "dZ o",
    "びゃ": "b 'a", "びゅ": "b 'u", "びょ": "b 'o",
    "ぴゃ": "p 'a", "ぴゅ": "p 'u", "ぴょ": "p 'o",
    "っ": "cl", "ッ": "cl",
}


def _phoneme(lyric: str) -> str:
    if lyric in _KANA_PHONEMES:
        return _KANA_PHONEMES[lyric]
    return lyric or "a"


def export_vsqx_xml(project_name: str, tempo: float, notes: Iterable[Any]) -> str:
    """Serialize one active VO-SE vocal track as a VOCALOID4-compatible VSQX."""
    bpm = _finite_number(tempo, "tempo", minimum=1.0)
    records = _note_records(notes, bpm)
    if not records:
        raise ValueError("There are no notes to export")

    ns = "http://www.yamaha.co.jp/vocaloid/schema/vsq4/"
    xsi = "http://www.w3.org/2001/XMLSchema-instance"
    ET.register_namespace("", ns)
    ET.register_namespace("xsi", xsi)

    def element(parent: ET.Element, tag: str, value: Any | None = None) -> ET.Element:
        node = ET.SubElement(parent, f"{{{ns}}}{tag}")
        if value is not None:
            node.text = str(value)
        return node

    root = ET.Element(
        f"{{{ns}}}vsq4",
        {f"{{{xsi}}}schemaLocation": f"{ns} vsq4.xsd"},
    )
    element(root, "vender", "Yamaha corporation")
    element(root, "version", "4.0.0.0")
    style = element(root, "stylePlugin")
    element(style, "stylePluginID", "36015A33-875C-409B-8332-9D227C0E73B9")
    element(style, "version", "4.0.0.0")
    element(style, "stylePluginName", "VOCALOID4 Default Style")

    master = element(root, "masterTrack")
    element(master, "seqName", project_name or "VO-SE Vocal")
    element(master, "comment", "Exported from VO-SE Vocal Studio")
    element(master, "resolution", 480)
    signature = element(master, "timeSig")
    element(signature, "m", 0)
    element(signature, "nu", 4)
    element(signature, "de", 4)
    tempo_node = element(master, "tempo")
    element(tempo_node, "tick", 0)
    element(tempo_node, "bpm", int(round(bpm * 100)))

    track = element(root, "vsTrack")
    element(track, "tNo", 0)
    element(track, "name", project_name or "Vocal 1")
    element(track, "comment", "Main Track")
    part = element(track, "musicalPart")
    element(part, "posTick", 0)
    total_ticks = max(3840, max(item["tick"] + item["length"] for item in records)) + 1920
    element(part, "playTime", total_ticks)
    element(part, "name", "Vocal Part")
    element(part, "comment", "")
    part_style_plugin = element(part, "stylePlugin")
    element(part_style_plugin, "stylePluginID", "36015A33-875C-409B-8332-9D227C0E73B9")
    element(part_style_plugin, "version", "4.0.0.0")
    element(part_style_plugin, "stylePluginName", "VOCALOID4 Default Style")
    part_style = element(part, "partStyle")
    for key, value in (
        ("accent", 50), ("bendDep", 8), ("bendLen", 0), ("decay", 50),
        ("fallPort", 0), ("opening", 127), ("risePort", 0),
        ("vibLen", 0), ("vibType", 0),
    ):
        ET.SubElement(part_style, f"{{{ns}}}attr", {"id": key}).text = str(value)
    singer = element(part, "singer")
    element(singer, "posTick", 0)
    element(singer, "bank", 0)
    element(singer, "program", 0)

    for item in records:
        note = element(part, "note")
        element(note, "posTick", item["tick"])
        element(note, "durTick", item["length"])
        element(note, "noteNum", item["pitch"])
        element(note, "velocity", 64)
        element(note, "lyric", item["lyric"])
        element(note, "phn", _phoneme(item["lyric"]))
        style_node = element(note, "noteStyle")
        for key, value in (
            ("accent", 50), ("bendDep", 8), ("bendLen", 0), ("decay", 50),
            ("fallPort", 0), ("opening", 127), ("risePort", 0),
            ("vibLen", 0), ("vibType", 0),
        ):
            ET.SubElement(style_node, f"{{{ns}}}attr", {"id": key}).text = str(value)
    element(part, "plane", 0)

    return '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n' + ET.tostring(
        root, encoding="unicode", xml_declaration=False
    )
