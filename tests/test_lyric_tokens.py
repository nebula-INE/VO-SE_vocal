from modules.utils.lyric_tokens import assign_lyrics, parse_lyric_tokens


def test_auto_mode_splits_space_delimited_lyrics():
    assert parse_lyric_tokens("さ く ら\nひ ら く") == ["さ", "く", "ら", "ひ", "ら", "く"]


def test_auto_mode_segments_japanese_morae():
    assert parse_lyric_tokens("きゃくしゅー") == ["きゃ", "く", "しゅー"]


def test_character_mode_keeps_small_kana_with_previous_character():
    assert parse_lyric_tokens("てぃふぁ", "char") == ["てぃ", "ふぁ"]


def test_line_mode_assigns_one_token_per_line():
    assert parse_lyric_tokens("あ\n\nい\r\nう", "line") == ["あ", "い", "う"]


def test_space_mode_keeps_each_whitespace_delimited_token():
    assert parse_lyric_tokens("hello   world\nagain", "space") == ["hello", "world", "again"]


def test_fill_modes_match_web_batch_lyric_behavior():
    existing = ["旧1", "旧2", "旧3", "旧4"]
    assert assign_lyrics(existing, ["新1", "新2"]) == ["新1", "新2", "旧3", "旧4"]
    assert assign_lyrics(existing, ["ら", "ら"], "loop") == ["ら", "ら", "ら", "ら"]


def test_invalid_modes_are_rejected():
    import pytest

    with pytest.raises(ValueError):
        parse_lyric_tokens("あ", "unknown")
    with pytest.raises(ValueError):
        assign_lyrics(["あ"], ["い"], "unknown")
