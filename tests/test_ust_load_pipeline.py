from __future__ import annotations

from types import MethodType, SimpleNamespace

from modules.data.prefix_map import map_alias, parse_prefix_map
from modules.gui.mixins.project_io_mixin import (
    ProjectIOMixin,
    _ExternalProjectLoadWorker,
)


class _StatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, message, *_args):
        self.messages.append(str(message))


class _Timeline:
    def __init__(self):
        self.notes_list = []
        self.tempo = 120.0
        self.updated = 0

    def set_notes(self, notes):
        self.notes_list = list(notes)

    def update(self):
        self.updated += 1


def _make_window():
    window = SimpleNamespace(
        timeline_widget=_Timeline(),
        graph_editor_widget=None,
        tracks=[SimpleNamespace(notes=[])],
        current_track_idx=0,
        history=None,
        vo_se_engine=None,
        current_playback_time=0.0,
        current_voice_id="NONE",
        current_voice="未選択",
        current_project_path="",
        _project_load_worker=None,
    )
    status_bar = _StatusBar()
    window.statusBar = lambda: status_bar

    for name in (
        "_capture_project_edit_state",
        "_record_project_state_edit",
        "_apply_project_tempo",
        "load_ust_file",
    ):
        setattr(window, name, MethodType(getattr(ProjectIOMixin, name), window))

    window._on_external_project_load_failed = MethodType(
        lambda _self, path, message: (_ for _ in ()).throw(
            AssertionError(f"UST load failed for {path}: {message}")
        ),
        window,
    )
    return window, status_bar


def _write_ust(path):
    # cp932 is the encoding used by many existing UTAU projects.
    path.write_bytes(
        (
            "[#VERSION]\n"
            "UST Version 1.2\n"
            "[#SETTING]\n"
            "Tempo=120.000\n"
            "[#0000]\n"
            "Length=480\n"
            "Lyric=あ\n"
            "NoteNum=60\n"
            "[#0001]\n"
            "Length=240\n"
            "Lyric=+\n"
            "NoteNum=60\n"
            "[#0002]\n"
            "Length=360\n"
            "Lyric=R\n"
            "NoteNum=60\n"
            "[#0003]\n"
            "Length=480\n"
            "Lyric=い\n"
            "NoteNum=64\n"
            "[#TRACKEND]\n"
        ).encode("cp932")
    )


def test_async_ust_load_updates_timeline_without_rest_or_tie_nodes(tmp_path):
    path = tmp_path / "timeline-roundtrip.ust"
    _write_ust(path)

    worker = _ExternalProjectLoadWorker(str(path))
    completed = []
    failed = []
    worker.signals.completed.connect(
        lambda file_path, payload: completed.append((file_path, payload))
    )
    worker.signals.failed.connect(
        lambda file_path, message: failed.append((file_path, message))
    )

    # Run the same worker entry point used by QThreadPool, then apply its
    # payload through the GUI-side completion handler.
    worker.run()

    assert worker.done.is_set()
    assert failed == []
    assert len(completed) == 1

    window, status_bar = _make_window()
    window._on_external_project_loaded = MethodType(
        ProjectIOMixin._on_external_project_loaded, window
    )
    window._on_external_project_loaded(*completed[0])

    displayed = window.timeline_widget.notes_list
    assert len(displayed) == 2
    assert [note.lyric for note in displayed] == ["あ", "い"]
    assert [note.note_number for note in displayed] == [60, 64]
    assert abs(displayed[0].start_time - 0.0) < 1e-9
    assert abs(displayed[0].duration - 0.75) < 1e-9
    assert abs(displayed[1].start_time - 1.125) < 1e-9
    assert abs(displayed[1].duration - 0.5) < 1e-9
    assert len(displayed[0]._ust_tie_sections) == 1
    assert window.tracks[0].notes == displayed
    assert window.timeline_widget.tempo == 120.0
    assert "UST 読み込み完了: 2 ノート" in status_bar.messages[-1]


def test_prefix_map_parser_keeps_empty_tab_column_and_selects_pitch_layer():
    entries = parse_prefix_map(
        "C1\t\t_C4\n"
        "F4\t\t_F4\n"
    )

    assert len(entries) == 2
    assert entries[0].prefix == ""
    assert entries[0].suffix == "_C4"
    assert entries[1].prefix == ""
    assert entries[1].suffix == "_F4"
    assert map_alias("あ", 60, entries) == "あ_C4"
    assert map_alias("あ", 65, entries) == "あ_F4"


def test_prefix_map_parser_preserves_legacy_two_column_suffix():
    entries = parse_prefix_map("C1\t_C4\nF4\t_F4\n")

    assert len(entries) == 2
    assert entries[0].prefix == ""
    assert entries[0].suffix == "_C4"
    assert map_alias("あ", 60, entries) == "あ_C4"
    assert map_alias("あ", 65, entries) == "あ_F4"


def test_native_prefix_map_parser_keeps_empty_tab_fields_like_python():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1] / "include" / "OtoDatabase.h"
    ).read_text(encoding="utf-8")
    parser = source.split("void loadPrefixMap", 1)[1].split(
        "juce::String applyPrefixMap", 1
    )[0]

    # Keep this parity guard tied to the production C++ parser, not only the
    # Python helper: the empty middle field in C1<TAB><TAB>_C4 is significant.
    assert 'rawLine.indexOf ("\\t")' in parser
    assert "rawLine.indexOfChar (firstTab + 1, '\\t')" in parser
    assert "suffix = rawLine.substring (firstTab + 1).trim();" in parser
    assert "prefix = rawLine.substring (firstTab + 1, secondTab).trim();" in parser
    assert 'prefixMap[boundary] = { prefix == "-" ? juce::String() : prefix' in parser
