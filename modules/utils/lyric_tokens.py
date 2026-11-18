"""Japanese lyric tokenization shared by Desktop and Web behavior."""

from __future__ import annotations

import re
from collections.abc import Sequence

_SMALL_KANA = set("ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮー")


def segment_japanese_characters(text: str) -> list[str]:
    """Split text into mora-like tokens, keeping small kana with the prior kana."""
    cleaned = re.sub(r"[\r\n\t]", "", text).strip()
    tokens: list[str] = []
    index = 0
    while index < len(cleaned):
        char = cleaned[index]
        if char.isspace():
            index += 1
            continue
        index += 1
        if index < len(cleaned) and cleaned[index] in _SMALL_KANA:
            char += cleaned[index]
            index += 1
        tokens.append(char)
    return tokens


def parse_lyric_tokens(
    raw_text: str,
    delimiter_mode: str = "auto",
) -> list[str]:
    """Match the Web editor's auto/character/space/line lyric splitting."""
    if delimiter_mode not in {"auto", "char", "space", "line"}:
        raise ValueError(f"Unsupported lyric delimiter mode: {delimiter_mode}")
    if not raw_text.strip():
        return []
    if delimiter_mode == "space":
        return [token for token in re.split(r"\s+", raw_text.strip()) if token]
    if delimiter_mode == "line":
        return [line.strip() for line in re.split(r"\r?\n", raw_text) if line.strip()]
    if delimiter_mode == "char":
        return segment_japanese_characters(raw_text)
    if re.search(r"\s+", raw_text.strip()):
        return [token for token in re.split(r"\s+", raw_text.strip()) if token]
    return segment_japanese_characters(raw_text)


def assign_lyrics(
    existing_lyrics: Sequence[str],
    tokens: Sequence[str],
    fill_mode: str = "keep",
) -> list[str]:
    """Assign tokens in timeline order, optionally repeating them to fill all notes."""
    if fill_mode not in {"keep", "loop"}:
        raise ValueError(f"Unsupported lyric fill mode: {fill_mode}")
    if not tokens:
        return list(existing_lyrics)
    if fill_mode == "loop":
        return [tokens[index % len(tokens)] for index in range(len(existing_lyrics))]
    return [
        tokens[index] if index < len(tokens) else lyric
        for index, lyric in enumerate(existing_lyrics)
    ]
