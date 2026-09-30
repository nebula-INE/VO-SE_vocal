"""UTAU prefix.map pitch/suffix selection shared by desktop resolvers.

The legacy prefix.map convention maps a lower MIDI boundary to a prefix and/or
suffix.  The mapping remains optional: when no prefix.map exists, aliases are
left unchanged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence


_NOTE_TO_SEMITONE = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


@dataclass(frozen=True)
class PrefixMapEntry:
    boundary: int
    prefix: str
    suffix: str


def note_name_to_midi(value: str) -> Optional[int]:
    match = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d+)", value.strip())
    if not match:
        return None
    semitone = _NOTE_TO_SEMITONE[match.group(1).upper()]
    if match.group(2) == "#":
        semitone += 1
    elif match.group(2) == "b":
        semitone -= 1
    octave = int(match.group(3))
    return (octave + 1) * 12 + semitone


def parse_prefix_map(text: str) -> list[PrefixMapEntry]:
    entries: list[PrefixMapEntry] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        # prefix.map is tab-delimited, but accept arbitrary whitespace as a fallback.\n        parts = line.split("\t") if "\t" in line else re.split(r"\s+", line)
        if len(parts) < 2:
            continue
        boundary = note_name_to_midi(parts[0])
        if boundary is None:
            continue
        # Classic prefix.map stores prefix and suffix as the remaining columns.
        prefix = parts[1] if parts[1] != "-" else ""
        suffix = parts[2] if len(parts) >= 3 and parts[2] != "-" else ""
        if len(parts) == 2:
            # Most banks use the second column as the suffix, e.g. C4 -> _C4.
            suffix = prefix
            prefix = ""
        entries.append(PrefixMapEntry(boundary, prefix, suffix))
    entries.sort(key=lambda item: item.boundary)
    return entries


def select_prefix_map(entries: Sequence[PrefixMapEntry], note_num: int) -> Optional[PrefixMapEntry]:
    if not entries:
        return None
    tone = max(0, min(127, int(round(note_num))))
    selected: Optional[PrefixMapEntry] = None
    for entry in entries:
        if entry.boundary > tone:
            break
        selected = entry
    # A prefix.map row describes the lower boundary of a subbank.
    # Notes below the first boundary are outside every declared subbank and
    # must not be forced into the first mapping.
    return selected


def map_alias(alias: str, note_num: int, entries: Sequence[PrefixMapEntry]) -> str:
    mapping = select_prefix_map(entries, note_num)
    if mapping is None:
        return alias

    # Do not double-apply a suffix/prefix already explicitly present in the lyric.
    mapped = alias
    if mapping.prefix and not mapped.startswith(mapping.prefix):
        mapped = mapping.prefix + mapped
    if mapping.suffix and not mapped.endswith(mapping.suffix):
        mapped = mapped + mapping.suffix
    return mapped
