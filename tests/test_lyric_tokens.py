import json
from pathlib import Path

import pytest

from modules.utils.lyric_tokens import assign_lyrics, parse_lyric_tokens


CASES = json.loads(
    (Path(__file__).parents[1] / "specs" / "lyric_tokenization_cases.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_python_lyric_tokenization_matches_shared_web_contract(case):
    assert parse_lyric_tokens(case["text"], case["mode"]) == case["tokens"]


def test_fill_modes_match_web_batch_lyric_behavior():
    existing = ["旧1", "旧2", "旧3", "旧4"]
    assert assign_lyrics(existing, ["新1", "新2"]) == ["新1", "新2", "旧3", "旧4"]
    assert assign_lyrics(existing, ["ら", "ら"], "loop") == ["ら", "ら", "ら", "ら"]


def test_invalid_modes_are_rejected():
    with pytest.raises(ValueError):
        parse_lyric_tokens("あ", "unknown")
    with pytest.raises(ValueError):
        assign_lyrics(["あ"], ["い"], "unknown")
