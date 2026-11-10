# modules/gui/mixins/project_io_mixin.py
"""
VO-SE Vocal — プロジェクト入出力ミックスイン (完全版)

変更点 (vs 旧実装):
  [FIX-1] load_ust_file(): mido 経由 MIDI 処理を廃止し UstParser に完全移行
           → Flags / Vibrato / Modulation / Intensity / Portamento が失われない
  [FIX-2] import_external_project(): .ust 拡張子を検出して load_ust_file() を呼ぶ
  [FIX-3] save_file_dialog_and_save_midi(): プロジェクトを JSON 保存（従来通り）
  [FIX-4] load_json_project(): JSON から NoteEvent を復元し timeline_widget に設定
  [NEW-1] export_as_ust(): 現在のプロジェクトを UTAU .ust 形式で書き出す
"""
from __future__ import annotations

import json
import logging
import math
import multiprocessing
import os
import threading
from typing import Any, Dict, List, Optional, cast
from copy import copy, deepcopy

from PySide6.QtCore import QObject, QRunnable, QThread, QThreadPool, Signal, Slot
from PySide6.QtWidgets import QFileDialog, QMessageBox

from modules.data.data_models import NoteEvent, PitchEvent
from modules.data.ust_parser import UstParser, UstConverter
from modules.data.vsqx_parser import parse_vsqx

logger = logging.getLogger(__name__)


def _clone_track_collection(tracks: List[Any]) -> List[Any]:
    """Clone track containers and note lists without recursively copying note trees."""
    snapshots = []
    for track in tracks:
        try:
            track_snapshot = copy(track)
            notes = getattr(track, "notes", None)
            if isinstance(notes, list):
                track_snapshot.notes = list(notes)
            snapshots.append(track_snapshot)
        except Exception:
            logger.exception("トラックの軽量コピーに失敗。deepcopyへフォールバック")
            snapshots.append(deepcopy(track))
    return snapshots


def _prepare_project_note(note_data: Dict[str, Any], description: str) -> NoteEvent:
    """Convert and validate a serialized note without accessing any GUI object."""
    if not isinstance(note_data, dict):
        raise ValueError(f"{description} の形式が不正です。")
    note = NoteEvent.from_dict(note_data)
    try:
        raw_note_number = float(note.note_number)
        note_number = int(raw_note_number)
        start_time = float(note.start_time)
        duration = float(note.duration)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{description} の音程・開始位置・長さが不正です。") from exc

    if (
        not math.isfinite(raw_note_number)
        or not raw_note_number.is_integer()
        or not 0 <= note_number <= 127
        or not math.isfinite(start_time)
        or start_time < 0.0
        or not math.isfinite(duration)
        or duration < 0.0
    ):
        raise ValueError(f"{description} の音程・開始位置・長さが範囲外です。")

    note.note_number = note_number
    note.start_time = start_time
    note.duration = duration
    for field_name in (
        "velocity", "vibrato_depth", "vibrato_rate", "formant_shift",
        "onset", "pre_utterance", "overlap",
    ):
        value = getattr(note, field_name, None)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                f"{description} の {field_name} が数値ではありません。"
            ) from exc
        if not math.isfinite(number):
            raise ValueError(
                f"{description} の {field_name} に無効な数値があります。"
            )
    return note


def _prepare_ust_notes(note_dicts: List[Dict[str, Any]]) -> List[NoteEvent]:
    """Convert UST note dictionaries into models before returning to the GUI thread."""
    notes: List[NoteEvent] = []
    for index, data in enumerate(note_dicts):
        try:
            note = NoteEvent.from_dict(data)
            if data.get("pre_utterance") is not None:
                note.pre_utterance = data["pre_utterance"]
            if data.get("overlap") is not None:
                note.overlap = data["overlap"]
            for key, value in data.items():
                if key.startswith("_ust_"):
                    setattr(note, key, value)
            notes.append(note)
        except Exception as exc:
            logger.warning("UST NoteEvent 変換失敗: %s / note=%d", exc, index + 1)
    return notes


def _prepare_midi_note_tracks(note_dicts: List[Dict[str, Any]]) -> Dict[str, Any]:
    notes_by_track: Dict[int, List[NoteEvent]] = {}
    names_by_track: Dict[int, str] = {}
    for note_data in note_dicts:
        track_index = int(note_data.get("_midi_track_index", 0))
        note = NoteEvent.from_dict(note_data)
        notes_by_track.setdefault(track_index, []).append(note)
        name = str(note_data.get("_midi_track_name", "") or "")
        if name:
            names_by_track[track_index] = name
    return {
        "notes_by_track": notes_by_track,
        "names_by_track": names_by_track,
        "note_count": len(note_dicts),
    }


def _prepare_json_note_tracks(data: Dict[str, Any]) -> Dict[str, Any]:
    raw_tracks = data.get("tracks")
    if raw_tracks is not None and not isinstance(raw_tracks, list):
        raise ValueError("tracks は配列である必要があります。")
    if isinstance(raw_tracks, list) and raw_tracks:
        prepared_tracks: List[List[NoteEvent]] = []
        for track_index, raw_track in enumerate(raw_tracks):
            if not isinstance(raw_track, dict):
                raise ValueError(f"Track {track_index + 1} の形式が不正です。")
            raw_notes = raw_track.get("notes", [])
            if not isinstance(raw_notes, list):
                raise ValueError(
                    f"Track {track_index + 1} の notes は配列である必要があります。"
                )
            prepared_tracks.append([
                _prepare_project_note(note_data, f"Track {track_index + 1} / Note {note_index + 1}")
                for note_index, note_data in enumerate(raw_notes)
            ])
        return {"prepared_note_tracks": prepared_tracks, "prepared_legacy_notes": None}

    raw_notes = data.get("notes", [])
    if not isinstance(raw_notes, list):
        raise ValueError("notes は配列である必要があります。")
    prepared_legacy = [
        _prepare_project_note(note_data, f"Note {note_index + 1}")
        for note_index, note_data in enumerate(raw_notes)
    ]
    return {"prepared_note_tracks": None, "prepared_legacy_notes": prepared_legacy}


def _parse_external_project_file(file_path: str) -> Dict[str, Any]:
    """Parse an external project file without touching GUI objects."""
    extension = os.path.splitext(file_path)[1].lower()
    if extension == ".ust":
        project = UstParser().load(file_path)
        note_dicts = UstConverter.to_note_dicts(project)
        return {
            "format": "ust",
            "project": project,
            "note_dicts": note_dicts,
            "prepared_notes": _prepare_ust_notes(note_dicts),
        }
    if extension == ".vsqx":
        return {"format": "vsqx", "project": parse_vsqx(file_path)}
    if extension in {".mid", ".midi"}:
        from modules.data.midi_manager import load_midi_file

        notes = load_midi_file(file_path)
        if notes is None:
            raise ValueError("MIDIファイルを解析できませんでした。")
        return {"format": "midi", "notes": notes, **_prepare_midi_note_tracks(notes)}
    if extension in {".json", ".vose"}:
        with open(file_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("プロジェクトJSONのルートはオブジェクトである必要があります。")
        prepared = _prepare_json_note_tracks(data)
        return {"format": "json", "data": data, **prepared}
    raise ValueError(f"対応していない形式です: {extension}")


class _VoiceBankImportWorker(QThread):
    """Download, safely install and inspect a voicebank without blocking the GUI."""

    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, manager: Any, source_path: str, source_type: str = "local", parent=None):
        super().__init__(parent)
        self.manager = manager
        self.source_path = source_path
        self.source_type = source_type
        self.done = threading.Event()

    def run(self) -> None:
        temporary_path = None
        try:
            zip_path = self.source_path
            if self.source_type == "url":
                from urllib.parse import urlparse
                from urllib.request import urlretrieve
                import tempfile

                parsed = urlparse(self.source_path)
                if parsed.scheme not in ("http", "https"):
                    raise ValueError(f"未対応のURLスキームです: {parsed.scheme}")
                with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as handle:
                    temporary_path = handle.name
                urlretrieve(self.source_path, temporary_path)
                zip_path = temporary_path

            install = getattr(self.manager, "install_voice_from_zip", None)
            if not callable(install):
                raise RuntimeError("音源マネージャーにZIPインストール機能がありません。")
            installed_name = install(zip_path)
            if not installed_name:
                raise RuntimeError("音源のインストール結果を取得できませんでした。")

            # install_voice_from_zip() already refreshes VoiceManager.voices.
            # Avoid running the full recursive scan a second time here.
            voices = dict(getattr(self.manager, "voices", {}) or {})
            voice_record = voices.get(str(installed_name))
            if isinstance(voice_record, dict):
                voice_path = str(voice_record.get("path", "") or "")
            else:
                voice_path = str(voice_record or "")
            if not voice_path or not os.path.isdir(voice_path):
                scan = getattr(self.manager, "scan_voices", None)
                if callable(scan):
                    scanned = scan()
                    if isinstance(scanned, dict):
                        voices = dict(scanned)
                voice_record = voices.get(str(installed_name))
                if isinstance(voice_record, dict):
                    voice_path = str(voice_record.get("path", "") or "")
                else:
                    voice_path = str(voice_record or "")
            if not voice_path or not os.path.isdir(voice_path):
                raise RuntimeError(
                    f"インストール済み音源の保存先が見つかりません: {installed_name}"
                )

            # Optional ONNX model initialization can also be expensive. Keep it
            # off the GUI thread and only pass the prepared Python object back.
            from modules.gui.aural_engine import AuralAIEngine
            from modules.gui.shared import DynamicsAIEngine

            aural_model = os.path.join(voice_path, "aural_dynamics.onnx")
            standard_model = os.path.join(voice_path, "model.onnx")
            if os.path.isfile(aural_model):
                dynamics_ai = AuralAIEngine()
                load_model = getattr(dynamics_ai, "load_model", None)
                if callable(load_model):
                    load_model(aural_model)
                engine_message = "上位Auralモデル"
            elif os.path.isfile(standard_model):
                dynamics_ai = DynamicsAIEngine()
                load_model = getattr(dynamics_ai, "load_model", None)
                if callable(load_model):
                    load_model(standard_model)
                engine_message = "標準Dynamicsモデル"
            else:
                dynamics_ai = AuralAIEngine()
                engine_message = "汎用Auralエンジン"

            self.completed.emit({
                "name": str(installed_name),
                "voice_path": voice_path,
                "voices": voices,
                "dynamics_ai": dynamics_ai,
                "engine_message": engine_message,
            })
        except Exception as exc:
            logger.exception("音源ZIPのバックグラウンドインストールに失敗")
            self.failed.emit(str(exc) or exc.__class__.__name__)
        finally:
            if temporary_path:
                try:
                    if os.path.exists(temporary_path):
                        os.remove(temporary_path)
                except OSError:
                    logger.warning("一時ZIPを削除できません: %s", temporary_path)
            self.done.set()


class _ExternalProjectLoadSignals(QObject):
    completed = Signal(str, object)
    failed = Signal(str, str)


class _ExternalProjectLoadWorker(QRunnable):
    """Run potentially expensive parsing outside the Qt GUI thread."""

    def __init__(self, file_path: str) -> None:
        super().__init__()
        self.file_path = file_path
        self.signals = _ExternalProjectLoadSignals()
        self.done = threading.Event()

    def run(self) -> None:
        try:
            payload = _parse_external_project_file(self.file_path)
            self.signals.completed.emit(self.file_path, payload)
        except Exception as exc:
            logger.exception("外部プロジェクトのバックグラウンド解析に失敗: %s", self.file_path)
            self.signals.failed.emit(self.file_path, str(exc))
        finally:
            self.done.set()


# UST 書き出し時のデフォルト解像度 (ticks/beat)
_TICKS_PER_BEAT = 480


class ProjectIOMixin:
    """
    MainWindow に mix-in して使うファイル入出力クラス。
    self は MainWindow インスタンスとして扱われるが、
    型チェックの複雑さを避けるため self: Any でアノテートする。
    """

    # ------------------------------------------------------------------
    # プロジェクト全体のUndo/Redo
    # ------------------------------------------------------------------

    def _capture_project_edit_state(self: Any) -> Dict[str, Any]:
        """ファイル読み込み前後のプロジェクト状態を履歴用に取得する。"""
        timeline = getattr(self, "timeline_widget", None)
        graph = getattr(self, "graph_editor_widget", None)
        tempo = float(getattr(timeline, "tempo", 120.0))
        track_snapshots = _clone_track_collection(
            list(getattr(self, "tracks", []) or [])
        )

        raw_graph_parameters = getattr(graph, "all_parameters", {}) or {}
        graph_parameter_snapshot = {
            name: list(values) if isinstance(values, list) else copy(values)
            for name, values in raw_graph_parameters.items()
        } if isinstance(raw_graph_parameters, dict) else deepcopy(raw_graph_parameters)

        return {
            "tracks": track_snapshots,
            "current_track_idx": int(getattr(self, "current_track_idx", 0)),
            "tempo": tempo,
            "graph_tempo": float(getattr(graph, "tempo", tempo)),
            "graph_parameters": graph_parameter_snapshot,
            "current_playback_time": float(getattr(self, "current_playback_time", 0.0)),
            # Desktop currently uses one active singer for all vocal tracks.
            # Persist that identity so reopening a project restores the same voice.
            "current_voice_id": str(getattr(self, "current_voice_id", "NONE") or "NONE"),
            "current_voice": str(getattr(self, "current_voice", "未選択") or "未選択"),
        }

    def _restore_project_edit_state(self: Any, state: Dict[str, Any]) -> None:
        """ファイル読み込みのUndo/Redoでプロジェクト状態を完全に復元する。"""
        self.tracks = deepcopy(state.get("tracks", []))
        if self.tracks:
            requested_idx = int(state.get("current_track_idx", 0))
            self.current_track_idx = min(max(requested_idx, 0), len(self.tracks) - 1)
        else:
            self.current_track_idx = 0

        tempo = float(state.get("tempo", 120.0))
        timeline = getattr(self, "timeline_widget", None)
        if timeline is not None:
            timeline.tempo = tempo
            notes = self.tracks[self.current_track_idx].notes if self.tracks else []
            if hasattr(timeline, "set_notes"):
                timeline.set_notes(notes)
            else:
                timeline.notes_list = list(notes)
            timeline.update()

        graph = getattr(self, "graph_editor_widget", None)
        if graph is not None:
            graph.tempo = float(state.get("graph_tempo", tempo))
            graph_parameters = deepcopy(state.get("graph_parameters", {}))
            if hasattr(graph, "_restore_parameters_snapshot"):
                graph._restore_parameters_snapshot(graph_parameters)
            else:
                graph.all_parameters = graph_parameters
                if hasattr(graph, "parameters_changed"):
                    graph.parameters_changed.emit(graph.all_parameters)
            graph.update()

        tempo_input = getattr(self, "tempo_input", None)
        if tempo_input is not None:
            tempo_input.blockSignals(True)
            tempo_input.setText(str(tempo))
            tempo_input.blockSignals(False)

        # Undo/Redo 後も合成エンジン側のテンポを UI と一致させる。
        engine = getattr(self, "vo_se_engine", None)
        set_tempo = getattr(engine, "set_tempo", None)
        if callable(set_tempo):
            set_tempo(tempo)

        self.current_playback_time = float(state.get("current_playback_time", 0.0))
        set_time = getattr(self, "_set_transport_time", None)
        if callable(set_time):
            set_time(self.current_playback_time)

        refresh = getattr(self, "refresh_track_list_ui", None)
        if callable(refresh):
            refresh()
        sync_strips = getattr(self, "_sync_track_strips", None)
        if callable(sync_strips):
            sync_strips()

        voice_id = state.get("current_voice_id")
        voice_name = state.get("current_voice", "未選択")
        if not self._apply_project_voice_selection(voice_id, voice_name):
            status_bar = getattr(self, "statusBar", lambda: None)()
            if status_bar is not None:
                status_bar.showMessage(
                    "保存されていた音源が見つからないため、現在の音源を維持しました。",
                    5000,
                )

        self.update_scrollbar_range() if callable(
            getattr(self, "update_scrollbar_range", None)
        ) else None

    def _apply_project_voice_selection(
        self: Any,
        voice_id: Any,
        display_name: Any = "",
    ) -> bool:
        """Restore a project singer only when its stable gallery ID is available."""
        if voice_id is None:
            # Older project files did not store singer identity.
            return True
        if not isinstance(voice_id, str):
            return False

        name = display_name if isinstance(display_name, str) else ""
        if voice_id.strip() in {"", "NONE"}:
            self.current_voice_id = "NONE"
            self.current_voice = name or "未選択"
            gallery = getattr(self, "voice_gallery", None)
            cards = getattr(gallery, "cards", {}) if gallery is not None else {}
            for card in cards.values():
                setter = getattr(card, "set_selected", None)
                if callable(setter):
                    setter(False)
            combo = getattr(self, "character_selector", None)
            if combo is not None:
                combo.blockSignals(True)
                combo.setCurrentIndex(-1)
                combo.blockSignals(False)
            return True

        gallery = getattr(self, "voice_gallery", None)
        cards = getattr(gallery, "cards", {}) if gallery is not None else {}
        card = cards.get(voice_id) if isinstance(cards, dict) else None
        if card is not None and not bool(getattr(card, "is_recruiting", False)):
            card_name = str(getattr(card, "display_name", name) or name)
            select_card = getattr(gallery, "on_card_clicked", None)
            if callable(select_card):
                select_card(card_name, voice_id)
                return True

        combo = getattr(self, "character_selector", None)
        if combo is not None:
            index = combo.findData(voice_id)
            if index >= 0:
                resolved_name = str(combo.itemText(index))
                on_voice_changed = getattr(self, "on_voice_changed", None)
                if callable(on_voice_changed):
                    on_voice_changed(resolved_name, voice_id)
                    combo.blockSignals(True)
                    combo.setCurrentIndex(index)
                    combo.blockSignals(False)
                    return True
        return False

    def _record_project_state_edit(
        self: Any,
        before_state: Dict[str, Any],
        after_state: Dict[str, Any],
        description: str,
    ) -> None:
        """既に適用済みのプロジェクト変更をUndo履歴へ登録する。"""
        if before_state == after_state:
            return
        history = getattr(self, "history", None)
        if history is None:
            return

        from modules.gui.main_window import EditCommand

        history.push(EditCommand(
            lambda: self._restore_project_edit_state(after_state),
            lambda: self._restore_project_edit_state(before_state),
            description,
        ))

    # ------------------------------------------------------------------
    # UST 読み込み
    # ------------------------------------------------------------------

    def load_ust_file(
        self: Any,
        file_path: str,
        parsed_project: Any = None,
        parsed_note_dicts: Optional[List[Dict[str, Any]]] = None,
        prepared_notes: Optional[List[NoteEvent]] = None,
    ) -> bool:
        """
        .ust ファイルをネイティブパーサーで読み込み、
        タイムラインに反映する。

        Args:
            file_path: .ust ファイルのパス

        Returns:
            True なら成功
        """
        try:
            before_state = self._capture_project_edit_state()
            project = parsed_project if parsed_project is not None else UstParser().load(file_path)
            note_dicts = (
                parsed_note_dicts
                if parsed_note_dicts is not None
                else UstConverter.to_note_dicts(project)
            )

            # UST の VoiceDir を解決し、実在する音源なら読み込み時点で
            # エンジンの音源ライブラリを切り替える。%VOICE% のような
            # UTAU側プレースホルダや存在しないパスは現在の音源を維持する。
            voice_dir = str(project.voice_dir or "").strip()
            if voice_dir and voice_dir.upper() not in {"%VOICE%", "%VOICE"}:
                if not os.path.isabs(voice_dir):
                    voice_dir = os.path.abspath(
                        os.path.join(os.path.dirname(os.path.abspath(file_path)), voice_dir)
                    )
                if os.path.isdir(voice_dir):
                    engine = getattr(self, "vo_se_engine", None)
                    set_voice_library = getattr(engine, "set_voice_library", None)
                    if callable(set_voice_library):
                        try:
                            set_voice_library(voice_dir)
                            logger.info("UST VoiceDir を音源ライブラリへ適用: %s", voice_dir)
                        except Exception as exc:
                            logger.warning("UST VoiceDir の適用に失敗: %s", exc)
                    else:
                        logger.debug("vo_se_engine.set_voice_library が利用できないため VoiceDir を無視")
                else:
                    logger.warning("UST VoiceDir が存在しません: %s", voice_dir)

            if not note_dicts:
                self.statusBar().showMessage("UST: ノートが見つかりませんでした。")
                return False

            # Worker で変換済みならそのまま使用し、旧呼び出しは互換変換する。
            if prepared_notes is not None:
                notes = list(prepared_notes)
            else:
                notes = _prepare_ust_notes(note_dicts)


            # 現在のトラックにも実データを同期する。
            tracks = list(getattr(self, "tracks", []) or [])
            current_idx = int(getattr(self, "current_track_idx", 0))
            if 0 <= current_idx < len(tracks):
                tracks[current_idx].notes = list(notes)

            # テンポの適用
            if hasattr(self, "timeline_widget") and self.timeline_widget is not None:
                self.timeline_widget.tempo = project.tempo
                if hasattr(self.timeline_widget, "set_notes"):
                    self.timeline_widget.set_notes(notes)
                elif hasattr(self.timeline_widget, "notes_list"):
                    self.timeline_widget.notes_list = notes
                    if hasattr(self.timeline_widget, "update"):
                        self.timeline_widget.update()

            # テンポスピンボックスがある場合は更新
            if hasattr(self, "tempo_spinbox") and self.tempo_spinbox is not None:
                self.tempo_spinbox.setValue(int(project.tempo))

            self.statusBar().showMessage(
                f"UST 読み込み完了: {len(notes)} ノート / Tempo {project.tempo:.1f} BPM"
            )
            logger.info(
                "UST ロード: %d ノート, Tempo=%.1f (%s)",
                len(notes), project.tempo, os.path.basename(file_path)
            )
            self._record_project_state_edit(
                before_state,
                self._capture_project_edit_state(),
                "UST読み込み",
            )
            return True

        except FileNotFoundError:
            self.statusBar().showMessage(f"ファイルが見つかりません: {file_path}")
            return False
        except Exception as exc:
            logger.exception("UST 読み込みエラー: %s", exc)
            QMessageBox.critical(self, "読み込みエラー", f"UST の読み込みに失敗しました:\n{exc}")
            return False

    # ------------------------------------------------------------------
    # 外部プロジェクト読み込みのディスパッチャー
    # ------------------------------------------------------------------

    def import_external_project(self: Any) -> None:
        """ファイル選択後、解析をワーカースレッドで実行してからUIへ反映する。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "プロジェクトを開く",
            "",
            "対応ファイル (*.ust *.vsqx *.mid *.midi *.json *.vose);;"
            "UTAU プロジェクト (*.ust);;"
            "Vocaloid プロジェクト (*.vsqx);;"
            "MIDI ファイル (*.mid *.midi);;"
            "VO-SE プロジェクト (*.vose *.json);;"
            "JSON ファイル (*.json)",
        )
        if file_path:
            self.load_external_project_async(file_path)

    def load_external_project_async(self: Any, file_path: str) -> bool:
        """Load a known project path asynchronously; suitable for menus and drag/drop."""
        if not file_path:
            return False
        extension = os.path.splitext(file_path)[1].lower()
        if extension not in {".ust", ".vsqx", ".mid", ".midi", ".json", ".vose"}:
            self.statusBar().showMessage(
                f"未対応のファイル形式です: {extension}", 4000
            )
            return False

        worker = getattr(self, "_project_load_worker", None)
        if worker is not None:
            self.statusBar().showMessage(
                "別のファイルを読み込み中です。完了してから次のファイルを開いてください。",
                4000,
            )
            return False

        self._project_load_worker = _ExternalProjectLoadWorker(file_path)
        self._project_load_worker.signals.completed.connect(
            self._on_external_project_loaded
        )
        self._project_load_worker.signals.failed.connect(
            self._on_external_project_load_failed
        )
        self.statusBar().showMessage(
            f"ファイルを解析中: {os.path.basename(file_path)}", 0
        )
        QThreadPool.globalInstance().start(self._project_load_worker)
        return True

    @Slot(str, object)
    def _on_external_project_loaded(self: Any, file_path: str, payload: object) -> None:
        self._project_load_worker = None
        if not isinstance(payload, dict):
            self._on_external_project_load_failed(
                file_path, "ファイル解析結果が不正です。"
            )
            return

        file_format = payload.get("format")
        try:
            if file_format == "ust":
                self.load_ust_file(
                    file_path,
                    parsed_project=payload.get("project"),
                    parsed_note_dicts=payload.get("note_dicts"),
                    prepared_notes=payload.get("prepared_notes"),
                )
            elif file_format == "vsqx":
                self._load_vsqx(
                    file_path,
                    parsed_project=payload.get("project"),
                )
            elif file_format == "midi":
                self.load_midi_file_from_path(
                    file_path,
                    parsed_notes=payload.get("notes"),
                    prepared_note_tracks=payload,
                )
            elif file_format == "json":
                self.load_json_project(
                    file_path,
                    parsed_data=payload.get("data"),
                    prepared_note_tracks=payload.get("prepared_note_tracks"),
                    prepared_legacy_notes=payload.get("prepared_legacy_notes"),
                )
            else:
                raise ValueError(f"不明なファイル形式です: {file_format}")
        except Exception as exc:
            logger.exception("解析済みプロジェクトの反映に失敗: %s", file_path)
            self._on_external_project_load_failed(file_path, str(exc))

    @Slot(str, str)
    def _on_external_project_load_failed(
        self: Any, file_path: str, error_message: str
    ) -> None:
        self._project_load_worker = None
        logger.error("プロジェクト読み込み失敗 (%s): %s", file_path, error_message)
        self.statusBar().showMessage(
            f"読み込み失敗: {os.path.basename(file_path)}", 6000
        )
        QMessageBox.critical(
            self,
            "読み込みエラー",
            f"{os.path.basename(file_path)} を読み込めませんでした。\\n{error_message}",
        )

    # ------------------------------------------------------------------
    # UST 書き出し
    # ------------------------------------------------------------------

    def export_as_ust(self: Any) -> None:
        """
        現在のプロジェクトを .ust 形式で書き出す。
        ビブラート・強度・フラグは NoteEvent の _ust_* 拡張フィールドから復元する。

        [テンポ対応]
        各ノートに `_ust_tempo` 属性があればその値を優先し、無ければ
        timeline_widget.tempo を使う。テンポが変化する位置には Tempo= 行を
        挿入し、ticks もそのノート自身のテンポで逆算する。
        """
        if not hasattr(self, "timeline_widget") or self.timeline_widget is None:
            return

        notes_list = getattr(self.timeline_widget, "notes_list", [])
        if not notes_list:
            self.statusBar().showMessage("書き出すノートがありません。")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "UST として書き出す",
            "output.ust",
            "UTAU プロジェクト (*.ust)",
        )
        if not file_path:
            return

        try:
            default_tempo = float(
                getattr(getattr(self, "timeline_widget", None), "tempo", 120.0)
            )

            # ---- ヘッダー [#SETTING] の Tempo 決定 ----
            # UTAU の慣例に合わせ、最初のノートの _ust_tempo を優先採用する。
            # (_ust_tempo が無いノートしか無い場合は timeline_widget.tempo)
            first_note_tempo = float(
                getattr(notes_list[0], "_ust_tempo", None) or default_tempo
            )

            lines: List[str] = [
                "[#VERSION]",
                "UST Version 1.2",
                "[#SETTING]",
                f"Tempo={first_note_tempo:.2f}",
                "Tracks=1",
                "ProjectName=VO-SE Export",
                "VoiceDir=%voice%",
                "OutFile=output.wav",
                "CacheDir=cache",
                "Tool1=utau.exe",
                "Mode2=True",
                "",
            ]

            # 直前に出力した Tempo= を追跡（同じ値を繰り返さないため）
            current_tempo: Optional[float] = first_note_tempo

            for i, note in enumerate(notes_list):
                # ---- このノート自身のテンポを決定 ----
                note_tempo = float(
                    getattr(note, "_ust_tempo", None) or default_tempo
                )

                # ---- そのノートのテンポで秒 → ticks へ逆変換 ----
                # 各ノートは読み込み時に「そのノートのテンポ」で秒に変換されているので、
                # 逆変換も同じテンポを使う必要がある。
                duration_sec = float(getattr(note, "duration", 0.5))
                beats = duration_sec / (60.0 / note_tempo)
                ticks = max(1, int(round(beats * _TICKS_PER_BEAT)))

                note_num = int(getattr(note, "note_number", 60))
                lyric    = str(getattr(note, "lyric", "あ"))

                section_id = f"{i:04X}"
                lines += [
                    f"[#{section_id}]",
                    f"Length={ticks}",
                    f"Lyric={lyric}",
                    f"NoteNum={note_num}",
                ]

                # ---- テンポ変化の位置に Tempo= を挿入 ----
                # UTAU はこの行があればそのノートから先のテンポを上書きする。
                # 同値が続く場合は省略し、変化点のみ出力する。
                if note_tempo != current_tempo:
                    lines.append(f"Tempo={note_tempo:.2f}")
                    current_tempo = note_tempo

                # ---- 先行発声・オーバーラップ ----
                pre_ms = float(getattr(note, "pre_utterance", 0.0))
                ov_ms  = float(getattr(note, "overlap",       0.0))
                if pre_ms != 0.0:
                    lines.append(f"PreUtterance={pre_ms:.3f}")
                if ov_ms != 0.0:
                    lines.append(f"VoiceOverlap={ov_ms:.3f}")

                # ---- 強度・モジュレーション ----
                intensity  = float(getattr(note, "_ust_intensity",  100.0))
                modulation = float(getattr(note, "_ust_modulation", 0.0))
                lines += [f"Intensity={intensity:.0f}", f"Modulation={modulation:.0f}"]

                # ---- フラグ ----
                flags = str(getattr(note, "_ust_flags", ""))
                if flags:
                    lines.append(f"Flags={flags}")

                # ---- ビブラート ----
                vib_dict = getattr(note, "_ust_vibrato", None)
                if isinstance(vib_dict, dict):
                    vbr_vals = [
                        vib_dict.get("length",   0),
                        vib_dict.get("cycle",  160),
                        vib_dict.get("depth",   35),
                        vib_dict.get("fade_in", 20),
                        vib_dict.get("fade_out",20),
                        vib_dict.get("phase",    0),
                        vib_dict.get("height",   0),
                    ]
                    lines.append("VBR=" + ",".join(str(v) for v in vbr_vals))

                # ---- ポルタメント ----
                for attr, key in [
                    ("_ust_pbs", "PBS"),
                    ("_ust_pbw", "PBW"),
                    ("_ust_pby", "PBY"),
                    ("_ust_pbm", "PBM"),
                ]:
                    val = str(getattr(note, attr, ""))
                    if val:
                        lines.append(f"{key}={val}")

                lines.append("")

            lines += ["[#TRACKEND]", ""]

            # Shift-JIS で書き出し（UTAU 互換）
            with open(file_path, "w", encoding="cp932", errors="replace") as f:
                f.write("\r\n".join(lines))

            self.statusBar().showMessage(
                f"UST 書き出し完了: {os.path.basename(file_path)}"
            )
            logger.info(
                "UST 書き出し完了: %s (%d ノート / default_tempo=%.1f)",
                file_path, len(notes_list), default_tempo,
            )

        except Exception as exc:
            logger.exception("UST 書き出しエラー: %s", exc)
            QMessageBox.critical(
                self, "書き出しエラー", f"UST の書き出しに失敗しました:\n{exc}"
            )

    # ------------------------------------------------------------------
    # JSON プロジェクト保存・読み込み (従来通り)
    # ------------------------------------------------------------------

    def save_file_dialog_and_save_midi(self: Any) -> bool:
        """互換用保存エントリポイント。正式なマルチトラック保存へ委譲する。"""
        return bool(self.on_save_project_clicked())

    def load_json_project(
        self: Any,
        file_path: str,
        parsed_data: Optional[Dict[str, Any]] = None,
        prepared_note_tracks: Optional[List[List[NoteEvent]]] = None,
        prepared_legacy_notes: Optional[List[NoteEvent]] = None,
    ) -> bool:
        """旧単一トラックJSONと新マルチトラックVOSE/JSONを検証してから反映する。"""
        import math

        before_state = None
        commit_started = False
        try:
            if parsed_data is None:
                with open(file_path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            else:
                data = parsed_data
            if not isinstance(data, dict):
                raise ValueError("プロジェクトJSONのルートはオブジェクトである必要があります。")

            tempo = float(data.get("tempo", 120.0))
            current_time = float(data.get("current_time", 0.0))
            saved_voice_id = data.get("current_voice_id")
            if saved_voice_id is not None and not isinstance(saved_voice_id, str):
                raise ValueError("current_voice_id は文字列である必要があります。")
            saved_voice_name = data.get("current_voice", "")
            if not isinstance(saved_voice_name, str):
                saved_voice_name = ""
            if not math.isfinite(tempo) or tempo <= 0.0:
                raise ValueError("プロジェクトのテンポが不正です。")
            if not math.isfinite(current_time) or current_time < 0.0:
                raise ValueError("プロジェクトの再生位置が不正です。")

            # すべてのトラック・ノート・オートメーションをUI変更前に構築する。
            def parse_project_note(note_data: Dict[str, Any], description: str) -> NoteEvent:
                note = NoteEvent.from_dict(note_data)
                try:
                    raw_note_number = float(note.note_number)
                    note_number = int(raw_note_number)
                    start_time = float(note.start_time)
                    duration = float(note.duration)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError(f"{description} の音程・開始位置・長さが不正です。") from exc

                if (
                    not math.isfinite(raw_note_number)
                    or not raw_note_number.is_integer()
                    or not 0 <= note_number <= 127
                    or not math.isfinite(start_time)
                    or start_time < 0.0
                    or not math.isfinite(duration)
                    or duration < 0.0
                ):
                    raise ValueError(f"{description} の音程・開始位置・長さが範囲外です。")

                note.note_number = note_number
                note.start_time = start_time
                note.duration = duration
                for field_name in (
                    "velocity",
                    "vibrato_depth",
                    "vibrato_rate",
                    "formant_shift",
                    "onset",
                    "pre_utterance",
                    "overlap",
                ):
                    value = getattr(note, field_name, None)
                    if value is None:
                        continue
                    try:
                        number = float(value)
                    except (TypeError, ValueError, OverflowError) as exc:
                        raise ValueError(
                            f"{description} の {field_name} が数値ではありません。"
                        ) from exc
                    if not math.isfinite(number):
                        raise ValueError(
                            f"{description} の {field_name} に無効な数値があります。"
                        )

                return note

            existing_tracks = list(getattr(self, "tracks", []) or [])
            if not existing_tracks:
                raise ValueError("読み込み先のトラックがありません。")
            track_cls = type(existing_tracks[0])
            raw_tracks = data.get("tracks")
            if raw_tracks is not None and not isinstance(raw_tracks, list):
                raise ValueError("tracks は配列である必要があります。")

            if isinstance(raw_tracks, list) and raw_tracks:
                loaded_tracks = []
                for index, raw in enumerate(raw_tracks):
                    if not isinstance(raw, dict):
                        raise ValueError(f"Track {index + 1} の形式が不正です。")
                    raw_notes = raw.get("notes", [])
                    if not isinstance(raw_notes, list):
                        raise ValueError(f"Track {index + 1} の notes は配列である必要があります。")
                    if prepared_note_tracks is not None:
                        if index >= len(prepared_note_tracks):
                            raise ValueError("事前解析されたトラックノート数が一致しません。")
                        notes = list(prepared_note_tracks[index])
                    else:
                        notes = []
                        for note_index, note_data in enumerate(raw_notes):
                            if not isinstance(note_data, dict):
                                raise ValueError(
                                    f"Track {index + 1} / Note {note_index + 1} の形式が不正です。"
                                )
                            notes.append(
                                parse_project_note(
                                    note_data,
                                    f"Track {index + 1} / Note {note_index + 1}",
                                )
                            )

                    mixer = raw.get("mixer", {})
                    if not isinstance(mixer, dict):
                        raise ValueError(f"Track {index + 1} の mixer 設定が不正です。")
                    volume = float(raw.get("volume", mixer.get("vol", 1.0)))
                    pan = float(raw.get("pan", mixer.get("pan", 0.0)))
                    if not math.isfinite(volume) or not math.isfinite(pan):
                        raise ValueError(f"Track {index + 1} の音量・パン設定が不正です。")

                    track = track_cls(
                        str(raw.get("name", f"Track {index + 1}")),
                        str(raw.get("type", "vocal")),
                    )
                    track.notes = notes
                    audio_path = str(
                        raw.get("audio_path", raw.get("audio", "")) or ""
                    )
                    if audio_path and not os.path.isabs(audio_path):
                        audio_path = os.path.abspath(
                            os.path.join(os.path.dirname(os.path.abspath(file_path)), audio_path)
                        )
                    track.audio_path = audio_path
                    track.volume = volume
                    track.pan = pan
                    track.is_muted = bool(raw.get("is_muted", False))
                    track.is_solo = bool(raw.get("is_solo", False))
                    if "engine_type" in raw:
                        track.engine_type = str(raw["engine_type"])
                    if "color_label" in raw:
                        track.color_label = str(raw["color_label"])
                    loaded_tracks.append(track)

                requested_idx = int(data.get("current_track_idx", 0))
                new_current_idx = min(max(requested_idx, 0), len(loaded_tracks) - 1)
            else:
                # 旧1.3系JSON、または tracks=[] のJSONは現在のトラックへ読み込む。
                raw_notes = data.get("notes", [])
                if not isinstance(raw_notes, list):
                    raise ValueError("notes は配列である必要があります。")
                if prepared_legacy_notes is not None:
                    notes = list(prepared_legacy_notes)
                else:
                    notes = []
                    for note_index, note_data in enumerate(raw_notes):
                        if not isinstance(note_data, dict):
                            raise ValueError(f"Note {note_index + 1} の形式が不正です。")
                        notes.append(
                            parse_project_note(note_data, f"Note {note_index + 1}")
                        )

                loaded_tracks = _clone_track_collection(existing_tracks)
                requested_idx = int(getattr(self, "current_track_idx", 0))
                new_current_idx = min(max(requested_idx, 0), len(loaded_tracks) - 1)
                loaded_tracks[new_current_idx].notes = notes

            restored_parameters = None
            raw_parameters = data.get("parameters")
            if raw_parameters is not None:
                if not isinstance(raw_parameters, dict):
                    raise ValueError("parameters はオブジェクトである必要があります。")
                restored_parameters = {}
                for name in ("Pitch", "Gender", "Tension", "Breath"):
                    raw_events = raw_parameters.get(name, [])
                    if not isinstance(raw_events, list):
                        raise ValueError(f"{name} オートメーションは配列である必要があります。")
                    events = []
                    for event_index, event in enumerate(raw_events):
                        if not isinstance(event, dict) or "time" not in event or "value" not in event:
                            raise ValueError(
                                f"{name} オートメーション {event_index + 1} の形式が不正です。"
                            )
                        event_time = float(event["time"])
                        event_value = float(event["value"])
                        if not math.isfinite(event_time) or not math.isfinite(event_value):
                            raise ValueError(
                                f"{name} オートメーション {event_index + 1} に不正な数値があります。"
                            )
                        events.append(PitchEvent.from_dict(event))
                    restored_parameters[name] = events

            # ここまで成功した場合のみ既存の状態を保存し、UI/プロジェクトを更新する。
            before_state = self._capture_project_edit_state()
            commit_started = True
            self.tracks = loaded_tracks
            self.current_track_idx = new_current_idx

            timeline = getattr(self, "timeline_widget", None)
            active_notes = self.tracks[self.current_track_idx].notes
            if timeline is not None:
                timeline.tempo = tempo
                if hasattr(timeline, "set_notes"):
                    timeline.set_notes(active_notes)
                else:
                    timeline.notes_list = list(active_notes)
                timeline.update()

            graph = getattr(self, "graph_editor_widget", None)
            if graph is not None:
                graph.tempo = tempo
                if restored_parameters is not None:
                    if hasattr(graph, "_restore_parameters_snapshot"):
                        graph._restore_parameters_snapshot(restored_parameters)
                    else:
                        graph.all_parameters = restored_parameters
                        if hasattr(graph, "parameters_changed"):
                            graph.parameters_changed.emit(graph.all_parameters)
                graph.update()

            tempo_input = getattr(self, "tempo_input", None)
            if tempo_input is not None:
                tempo_input.blockSignals(True)
                tempo_input.setText(str(tempo))
                tempo_input.blockSignals(False)

            self.current_playback_time = current_time
            set_time = getattr(self, "_set_transport_time", None)
            if callable(set_time):
                set_time(current_time)

            engine = getattr(self, "vo_se_engine", None)
            set_tempo = getattr(engine, "set_tempo", None)
            if callable(set_tempo):
                set_tempo(tempo)

            refresh = getattr(self, "refresh_track_list_ui", None)
            if callable(refresh):
                refresh()
            sync_strips = getattr(self, "_sync_track_strips", None)
            if callable(sync_strips):
                sync_strips()

            voice_restored = self._apply_project_voice_selection(
                saved_voice_id,
                saved_voice_name,
            )
            note_count = len(active_notes)
            status_message = (
                f"読み込み完了: {note_count} ノート ({os.path.basename(file_path)})"
            )
            if not voice_restored:
                status_message += " / 保存時の音源が未検出のため現在の音源を維持"
            self.statusBar().showMessage(status_message, 5000 if not voice_restored else 3000)
            self._record_project_state_edit(
                before_state,
                self._capture_project_edit_state(),
                "JSONプロジェクト読み込み",
            )
            return True

        except Exception as exc:
            if commit_started and before_state is not None:
                try:
                    self._restore_project_edit_state(before_state)
                except Exception:
                    logger.exception("JSON読込失敗後のプロジェクト復元にも失敗しました")
            logger.exception("JSON/VOSE 読み込みエラー: %s", exc)
            QMessageBox.critical(
                self,
                "読み込みエラー",
                f"プロジェクトの読み込みに失敗しました:\n{exc}",
            )
            return False

    def load_midi_file_from_path(
        self: Any,
        file_path: str,
        parsed_notes: Optional[List[Dict[str, Any]]] = None,
        prepared_note_tracks: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """MIDIを読み込み、元ファイルのトラック分割をデスクトッププロジェクトへ復元する。"""
        from modules.data.midi_manager import load_midi_file

        try:
            before_state = self._capture_project_edit_state()
            note_dicts = (
                parsed_notes
                if parsed_notes is not None
                else load_midi_file(file_path)
            )
            if not note_dicts and prepared_note_tracks is None:
                self.statusBar().showMessage("MIDI: ノートを読み込めませんでした。")
                return False

            if prepared_note_tracks is not None:
                notes_by_track = prepared_note_tracks.get("notes_by_track", {})
                names_by_track = prepared_note_tracks.get("names_by_track", {})
            else:
                notes_by_track: Dict[int, List[NoteEvent]] = {}
                names_by_track: Dict[int, str] = {}
                for note_data in note_dicts:
                    track_index = int(note_data.get("_midi_track_index", 0))
                    note = NoteEvent.from_dict(note_data)
                    notes_by_track.setdefault(track_index, []).append(note)
                    track_name = str(note_data.get("_midi_track_name", "") or "")
                    if track_name:
                        names_by_track[track_index] = track_name

            source_track_indices = sorted(notes_by_track)
            if not source_track_indices:
                self.statusBar().showMessage("MIDI: ノートを読み込めませんでした。")
                return False

            tracks = list(getattr(self, "tracks", []) or [])
            current_idx = int(getattr(self, "current_track_idx", 0))
            if not tracks or not (0 <= current_idx < len(tracks)):
                raise ValueError("読み込み先のトラックがありません。")

            target_track = tracks[current_idx]
            first_source_index = source_track_indices[0]
            target_track.notes = list(notes_by_track[first_source_index])
            target_track.name = names_by_track.get(
                first_source_index,
                f"{os.path.splitext(os.path.basename(file_path))[0]} - Track {first_source_index + 1}",
            )

            track_class = type(target_track)
            for source_index in source_track_indices[1:]:
                new_track = track_class(
                    names_by_track.get(
                        source_index,
                        f"{os.path.splitext(os.path.basename(file_path))[0]} - Track {source_index + 1}",
                    ),
                    "vocal",
                )
                new_track.notes = list(notes_by_track[source_index])
                tracks.append(new_track)

            self.tracks = tracks
            timeline = getattr(self, "timeline_widget", None)
            active_notes = list(notes_by_track[first_source_index])
            if timeline is not None:
                if hasattr(timeline, "set_notes"):
                    timeline.set_notes(active_notes)
                else:
                    timeline.notes_list = active_notes
                    if hasattr(timeline, "update"):
                        timeline.update()

            refresh = getattr(self, "refresh_track_list_ui", None)
            if callable(refresh):
                refresh()
            self.statusBar().showMessage(
                f"MIDI 読み込み完了: {len(note_dicts)} ノート / {len(source_track_indices)} トラック "
                f"({os.path.basename(file_path)})"
            )
            self._record_project_state_edit(
                before_state,
                self._capture_project_edit_state(),
                "MIDI読み込み",
            )
            return True

        except Exception as exc:
            logger.exception("MIDI 読み込みエラー: %s", exc)
            QMessageBox.critical(
                self,
                "読み込みエラー",
                f"MIDI の読み込みに失敗しました:\\n{exc}",
            )
            return False

    # ------------------------------------------------------------------
    # 内部: .vsqx 読み込み
    # ------------------------------------------------------------------

    def _load_vsqx(self: Any, file_path: str, parsed_project: Any = None) -> bool:
        """VOCALOID 3/4 .vsqx を読み込み、現在のトラックへ反映する。"""
        try:
            before_state = self._capture_project_edit_state()
            project = parsed_project if parsed_project is not None else parse_vsqx(file_path)
            if not project.notes:
                raise ValueError("VSQX に有効なノートがありません。")
            tracks = list(getattr(self, "tracks", []) or [])
            current_idx = int(getattr(self, "current_track_idx", 0))
            if not tracks or not (0 <= current_idx < len(tracks)):
                raise ValueError("読み込み先のトラックがありません。")
            tracks[current_idx].notes = list(project.notes)
            self.tracks = tracks
            timeline = getattr(self, "timeline_widget", None)
            if timeline is not None:
                timeline.tempo = project.tempo
                if hasattr(timeline, "set_notes"):
                    timeline.set_notes(project.notes)
                else:
                    timeline.notes_list = list(project.notes)
                    timeline.update()
            tempo_input = getattr(self, "tempo_input", None)
            if tempo_input is not None:
                tempo_input.blockSignals(True)
                tempo_input.setText(str(int(round(project.tempo))))
                tempo_input.blockSignals(False)
            graph = getattr(self, "graph_editor_widget", None)
            if graph is not None:
                graph.tempo = project.tempo
                graph.update()
            engine = getattr(self, "vo_se_engine", None)
            set_tempo = getattr(engine, "set_tempo", None)
            if callable(set_tempo):
                set_tempo(project.tempo)
            refresh = getattr(self, "refresh_track_list_ui", None)
            if callable(refresh):
                refresh()
            self.statusBar().showMessage(
                f"VSQX 読み込み完了: {len(project.notes)} ノート / Tempo {project.tempo:.2f} BPM"
            )
            self._record_project_state_edit(before_state, self._capture_project_edit_state(), "VSQX読み込み")
            return True
        except FileNotFoundError:
            self.statusBar().showMessage(f"ファイルが見つかりません: {file_path}")
            return False
        except Exception as exc:
            logger.exception("VSQX 読み込みエラー: %s", exc)
            QMessageBox.critical(self, "読み込みエラー", f"VSQX の読み込みに失敗しました:\\n{exc}")
            return False

    # oto.ini 書き出し（AutoOtoEngine の結果を保存する用途）
    def save_oto_ini(self: Any, voice_dir: str, oto_data: List[Dict[str, Any]]) -> bool:
        """
        oto.ini を Shift-JIS で書き出す。

        Args:
            voice_dir: 書き出し先フォルダ
            oto_data:  {"alias": str, "filename": str, ...} の辞書リスト

        Returns:
            True なら成功
        """
        ini_path = os.path.join(voice_dir, "oto.ini")
        try:
            lines = []
            for entry in oto_data:
                filename      = entry.get("filename", "a.wav")
                alias         = entry.get("alias", "")
                left_blank    = entry.get("left_blank",    0.0)
                fixed_range   = entry.get("fixed_range",   0.0)
                right_blank   = entry.get("right_blank",   0.0)
                pre_utterance = entry.get("pre_utterance", 0.0)
                overlap       = entry.get("overlap",       0.0)
                lines.append(
                    f"{filename}={alias},{left_blank:.0f},{fixed_range:.0f},"
                    f"{right_blank:.0f},{pre_utterance:.0f},{overlap:.0f}"
                )
            with open(ini_path, "w", encoding="cp932", errors="replace") as f:
                f.write("\r\n".join(lines) + "\r\n")
            logger.info("oto.ini 書き出し完了: %s", ini_path)
            return True
        except Exception as exc:
            logger.exception("oto.ini 書き出しエラー: %s", exc)
            return False

    # ======================================================================
    # 【従来実装】音源インポート・oto.ini 生成
    # ======================================================================

    def import_voice_bank(
        self: Any,
        zip_path: str,
        source_type: str = "local",
    ) -> bool:
        """Start voicebank download/install/model inspection in a worker thread."""
        manager = getattr(self, "voice_manager", None)
        if manager is None:
            QMessageBox.critical(
                self,
                "導入エラー",
                "音源マネージャーが初期化されていません。",
            )
            return False

        active = getattr(self, "_voice_bank_import_worker", None)
        if active is not None:
            try:
                if active.isRunning():
                    self.statusBar().showMessage(
                        "別の音源を導入中です。完了してから再試行してください。", 4000
                    )
                    return False
            except RuntimeError:
                pass

        worker = _VoiceBankImportWorker(manager, zip_path, source_type, self)
        worker.completed.connect(self._on_voice_bank_import_completed)
        worker.failed.connect(self._on_voice_bank_import_failed)
        worker.finished.connect(self._on_voice_bank_import_thread_finished)
        worker.finished.connect(worker.deleteLater)
        self._voice_bank_import_worker = worker
        self.statusBar().showMessage(
            f"音源をバックグラウンドで導入中: {os.path.basename(zip_path)}", 0
        )
        worker.start()
        return True

    @Slot(object)
    def _on_voice_bank_import_completed(self: Any, result: object) -> None:
        self._voice_bank_import_worker = None
        if not isinstance(result, dict):
            self._on_voice_bank_import_failed("音源インストール結果が不正です。")
            return

        self.dynamics_ai = result.get("dynamics_ai")
        gallery = getattr(self, "voice_gallery", None)
        if gallery is not None:
            refresh_gallery = getattr(gallery, "setup_gallery", None)
            if callable(refresh_gallery):
                voices = result.get("voices")
                try:
                    refresh_gallery(voices=voices if isinstance(voices, dict) else None)
                except TypeError:
                    refresh_gallery()
                gallery.update()

        name = str(result.get("name", ""))
        engine_message = str(result.get("engine_message", "汎用Auralエンジン"))
        self.statusBar().showMessage(
            f"音源インストール完了: {name} ({engine_message})", 5000
        )
        QMessageBox.information(
            self,
            "導入成功",
            f"音源「{name}」をインストールしました。\nエンジン: {engine_message}",
        )

    @Slot(str)
    def _on_voice_bank_import_failed(self: Any, error_message: str) -> None:
        self._voice_bank_import_worker = None
        logger.error("ドラッグ＆ドロップ音源インストール失敗: %s", error_message)
        self.statusBar().showMessage("音源のインストールに失敗しました。", 6000)
        QMessageBox.critical(
            self,
            "導入エラー",
            f"インストール中にエラーが発生しました:\n{error_message}",
        )

    @Slot()
    def _on_voice_bank_import_thread_finished(self: Any) -> None:
        worker = getattr(self, "_voice_bank_import_worker", None)
        if worker is not None:
            try:
                if not worker.isRunning():
                    self._voice_bank_import_worker = None
            except RuntimeError:
                self._voice_bank_import_worker = None


    def generate_and_save_oto(self: Any, target_voice_dir: str, force_redo: bool = False) -> None:
        """
        [超高速化・並列処理版] WAV解析 → oto.ini生成
        - CPU全コアを使用した並列処理（ProcessPoolExecutor）
        - ダウンサンプリング＆ベクトル化FFTで演算を爆速化
        - 2回目以降はキャッシュから瞬時に復元（変更検知機能付き）
        """
        try:
            from modules.tools.batch_voice_optimizer import BatchVoiceOptimizer
        except ImportError:
            # 万一モジュールが存在しない場合のフォールバック（旧ロジック警告）
            print("⚠️ BatchVoiceOptimizer not found. Falling back to legacy single-core mode.")
            self._legacy_generate_oto(target_voice_dir)
            return

        # 1. ステータスバーの更新（UIフィードバック）
        status_bar = self.statusBar() if hasattr(self, "statusBar") else None
        if status_bar:
            status_bar.showMessage(
                f"⚡ 超高速原音解析中（{multiprocessing.cpu_count()}コア並列）...",
                0  # タイムアウトなし（永続表示）
            )

        # 2. オプティマイザーのインスタンス化（キャッシュディレクトリはデフォルトでOK）
        optimizer = BatchVoiceOptimizer(target_sr=16000)

        # 3. 並列バッチ実行（force_redo=Trueならキャッシュを無視して再解析）
        print(f"[VO-SE] 音源フォルダをスキャン中: {target_voice_dir}")
        results = optimizer.optimize_voice_bank(target_voice_dir, force_redo=force_redo)

        # 4. 結果の書き出し
        if results:
            BatchVoiceOptimizer.export_oto_ini(target_voice_dir, results)
            msg = f"✅ oto.ini 生成完了！ ({len(results)} エントリ / キャッシュ利用含む)"
            print(msg)
            if status_bar:
                status_bar.showMessage(msg, 5000)  # 5秒間表示
        else:
            msg = "⚠️ 解析対象のWAVファイルが見つからないか、全ての解析に失敗しました。"
            print(msg)
            if status_bar:
                status_bar.showMessage(msg, 5000)

    # ================================================================
    # 旧ロジックのフォールバック（緊急時・モジュール欠落時）
    # ================================================================
    def _legacy_generate_oto(self: Any, target_voice_dir: str) -> None:
        """旧来の逐次処理版（バックアップ用）"""
        from modules.gui.main_window import AutoOtoEngine

        analyzer = AutoOtoEngine(sample_rate=44100)
        oto_lines = []
        files = [f for f in os.listdir(target_voice_dir) if f.lower().endswith('.wav')]

        if not files:
            print("解析対象のWAVファイルが見つかりませんでした。")
            return

        print(f"Starting legacy analysis for {len(files)} files (single-core)...")
        for filename in files:
            file_path = os.path.join(target_voice_dir, filename)
            try:
                params = analyzer.analyze_wav(file_path)
                line = analyzer.generate_oto_text(filename, params)
                oto_lines.append(line)
            except Exception as e:
                print(f"Error analyzing {filename}: {e}")

        oto_path = os.path.join(target_voice_dir, "oto.ini")
        try:
            with open(oto_path, "w", encoding="cp932", errors="ignore") as f:
                f.write("\n".join(oto_lines))
            print(f"Successfully generated (legacy): {oto_path}")
        except Exception as e:
            print(f"Failed to write oto.ini: {e}")

    # ======================================================================
    # 【従来実装】ドラッグ&ドロップ
    # ======================================================================

    def dragEnterEvent(self: Any, event):
        """[LIVE] ドラッグ受け入れ判定"""
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self: Any, event):
        """[LIVE] ファイルドロップ処理"""
        from urllib.parse import urlparse
        from urllib.request import urlretrieve
        import tempfile

        mime_data = event.mimeData()
        if not mime_data.hasUrls():
            return
            
        file_items: List[Dict[str, str]] = []
        for url in mime_data.urls():
            local_path = url.toLocalFile()
            if local_path:
                file_items.append({"path": local_path, "source": "local"})
            else:
                raw_url = url.toString()
                if raw_url:
                    file_items.append({"path": raw_url, "source": "url"})

        for item in file_items:
            file_path = item["path"]
            source_type = item["source"]
            file_lower = file_path.lower()

            if file_lower.endswith(".zip") or (source_type == "url" and ".zip" in file_lower):
                self.import_voice_bank(file_path, source_type=source_type)

            elif file_lower.endswith((".mid", ".midi", ".json", ".vose", ".ust", ".vsqx")):
                if self.load_file_from_path(file_path):
                    status_bar = self.statusBar()
                    if status_bar:
                        status_bar.showMessage(
                            f"読み込みを開始しました: {os.path.basename(file_path)}", 3000
                        )

    # ======================================================================
    # 【従来実装】その他メソッド
    # ======================================================================

    def load_file_from_path(self: Any, filepath: str) -> bool:
        """Compatibility entry point that routes every supported file through the async loader."""
        return self.load_external_project_async(filepath)

    def _parse_midi(self: Any, filepath: str):
        """互換用MIDIローダー。正式経路は load_midi_file_from_path()."""
        return self.load_midi_file_from_path(filepath)

    def _parse_ustx(self: Any, filepath: str):
        """USTXは未実装であることを明示する。"""
        QMessageBox.information(
            self,
            "未対応形式",
            ".ustx の読み込みは現在未実装です。\nUST / MIDI / JSON / VO-SE プロジェクトをご利用ください。",
        )

    def save_project(self: Any):
        """マルチトラック対応のVO-SEプロジェクト保存。"""
        return bool(self.on_save_project_clicked())

    def on_save_project_clicked(self: Any) -> bool:
        """現在のプロジェクト全体を安全に保存する。"""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "プロジェクトを保存",
            "project.vose",
            "VO-SE Project (*.vose);;JSON Files (*.json);;All Files (*)",
        )
        if not file_path:
            return False

        tmp_file_path = f"{file_path}.tmp"
        try:
            tracks = list(getattr(self, "tracks", []) or [])
            timeline = getattr(self, "timeline_widget", None)
            current_idx = int(getattr(self, "current_track_idx", 0))

            if timeline is not None and 0 <= current_idx < len(tracks):
                tracks[current_idx].notes = list(getattr(timeline, "notes_list", []) or [])

            tempo = float(getattr(timeline, "tempo", 120.0)) if timeline is not None else 120.0
            serialized_tracks = []
            for track in tracks:
                serialized_tracks.append({
                    "name": str(getattr(track, "name", "Track")),
                    "type": str(getattr(track, "track_type", "vocal")),
                    "audio_path": str(getattr(track, "audio_path", "") or ""),
                    "volume": float(getattr(track, "volume", 1.0)),
                    "pan": float(getattr(track, "pan", 0.0)),
                    "is_muted": bool(getattr(track, "is_muted", False)),
                    "is_solo": bool(getattr(track, "is_solo", False)),
                    "engine_type": str(getattr(track, "engine_type", "Aural")),
                    "color_label": str(getattr(track, "color_label", "")),
                    "notes": [
                        n.to_dict() if hasattr(n, "to_dict") else dict(n)
                        for n in (getattr(track, "notes", []) or [])
                    ],
                })

            graph = getattr(self, "graph_editor_widget", None)
            all_parameters = getattr(graph, "all_parameters", {}) if graph is not None else {}
            serialized_parameters = {
                name: [
                    event.to_dict() if hasattr(event, "to_dict") else {
                        "time": float(getattr(event, "time", 0.0)),
                        "value": float(getattr(event, "value", 0.0)),
                    }
                    for event in (all_parameters.get(name, []) or [])
                ]
                for name in ("Pitch", "Gender", "Tension", "Breath")
            }

            current_voice_id = str(
                getattr(self, "current_voice_id", "NONE") or "NONE"
            )
            if current_voice_id == "NONE":
                current_voice_id = ""

            project_data = {
                "app_id": "VO_SE_Pro_2026",
                "version": "1.5.0",
                "project_name": os.path.splitext(os.path.basename(file_path))[0],
                "tempo": tempo,
                "current_track_idx": max(0, current_idx),
                "current_time": float(getattr(self, "current_playback_time", 0.0)),
                # Desktop currently has a single active singer shared by vocal tracks.
                # Empty string represents the unselected "NONE" state.
                "current_voice_id": current_voice_id,
                "current_voice": str(getattr(self, "current_voice", "未選択") or "未選択"),
                "parameters": serialized_parameters,
                "tracks": serialized_tracks,
            }

            with open(tmp_file_path, "w", encoding="utf-8") as f:
                json.dump(project_data, f, ensure_ascii=False, indent=2)
            os.replace(tmp_file_path, file_path)
            self.statusBar().showMessage(
                f"保存完了: {os.path.basename(file_path)}", 3000
            )
            return True

        except Exception as exc:
            if os.path.exists(tmp_file_path):
                try:
                    os.remove(tmp_file_path)
                except OSError:
                    pass
            logger.exception("プロジェクト保存エラー: %s", exc)
            QMessageBox.critical(
                self,
                "保存エラー",
                f"プロジェクトの保存に失敗しました:\n{exc}",
            )
            return False

    def export_analysis_to_oto_ini(self: Any):
        """[LIVE] 解析結果 → oto.ini"""
        import shutil
        target_dir = self.voice_manager.get_current_voice_path()
        if not target_dir: 
            return
        
        file_path = os.path.join(target_dir, "oto.ini")
        
        if os.path.exists(file_path):
            try:
                shutil.copy2(file_path, file_path + ".bak")
            except Exception as e:
                print(f"Backup Warning: {e}")

        oto_lines = []
        processed_keys = set()
        for note in self.timeline_widget.notes_list:
            if getattr(note, 'has_analysis', False) and note.lyrics not in processed_keys:
                line = f"{note.lyrics}.wav={note.lyrics},0,0,0,{note.pre_utterance},{note.overlap}"
                oto_lines.append(line)
                processed_keys.add(note.lyrics)

        try:
            content = "\n".join(oto_lines)
            with open(file_path, "w", encoding="cp932", errors="replace") as f:
                f.write(content)
            QMessageBox.information(self, "Global Standard Saved", "設定ファイル(oto.ini)を更新しました。")
        except Exception as e:
            QMessageBox.critical(self, "Write Error", f"保存に失敗しました:\n{e}")

    def read_file_safely(self: Any, filepath: str) -> Optional[str]:
        """文字コードを厳密に検証して読み込む。誤った文字コードでの文字化けを防ぐ。"""
        try:
            import chardet
        except ImportError:
            chardet = None

        if not os.path.isfile(filepath):
            logger.warning("ファイルが見つかりません: %s", filepath)
            return None

        try:
            with open(filepath, "rb") as handle:
                raw_data = handle.read()
            if not raw_data:
                return ""

            candidates: List[str] = []
            if raw_data.startswith(bytes((0xEF, 0xBB, 0xBF))):
                candidates.append("utf-8-sig")

            detected_encoding: Optional[str] = None
            confidence = 0.0
            if chardet is not None:
                try:
                    result = chardet.detect(raw_data)
                    detected_encoding = result.get("encoding")
                    confidence = float(result.get("confidence") or 0.0)
                except Exception:
                    detected_encoding = None

            normalized_detected = (detected_encoding.lower().replace("_", "-") if detected_encoding else "")
            known_text_encoding = normalized_detected.startswith("utf-") or normalized_detected in {"cp932", "shift-jis", "shift-jis-2004", "euc-jp", "iso-2022-jp"}
            if detected_encoding and confidence >= 0.80 and known_text_encoding:
                candidates.append(detected_encoding)

            candidates.extend([
                "utf-8-sig",
                "utf-8",
                "cp932",
                "shift_jis",
                "euc_jp",
                "iso2022_jp",
            ])

            tried = set()
            for encoding in candidates:
                normalized = encoding.lower().replace("-", "_")
                if normalized in tried:
                    continue
                tried.add(normalized)
                try:
                    decoded = raw_data.decode(encoding, errors="strict")
                except (UnicodeDecodeError, LookupError):
                    continue
                logger.debug("ファイル読み込み成功: %s (%s)", filepath, encoding)
                return decoded

            # Last-resort fallback is explicit and logged; successful strict
            # decoding is preferred so a wrong candidate cannot silently mojibake.
            logger.warning("文字コードを確定できず cp932 で置換デコードします: %s", filepath)
            return raw_data.decode("cp932", errors="replace")

        except OSError:
            logger.exception("ファイル読み込みエラー: %s", filepath)
            return None

    def get_safe_installed_name(self: Any, filename: str, zip_path: str) -> str:
        """[LIVE] パス解析"""
        player = cast(Any, getattr(self, 'player', None))
        if player is not None:
            if hasattr(player, 'stop'):
                player.stop()
        
        self.is_playing = False
        
        timeline = cast(Any, getattr(self, 'timeline_widget', None))
        if timeline is not None:
            if hasattr(timeline, 'set_current_time'):
                timeline.set_current_time(0.0)
            
        clean_path = os.path.normpath(filename)
        parts = [p for p in clean_path.split(os.sep) if p]
        
        if len(parts) >= 2:
            return str(parts[-2])
            
        return str(os.path.splitext(os.path.basename(zip_path))[0])

    def on_export_button_clicked(self: Any):
        """WAVを書き出す。長時間レンダリングはGUIを止めずにバックグラウンドで行う。"""
        from modules.data.licensing import LicenseManager

        timeline = getattr(self, "timeline_widget", None)
        graph = getattr(self, "graph_editor_widget", None)
        engine = getattr(self, "vo_se_engine", None)
        if timeline is None or graph is None or engine is None:
            QMessageBox.warning(
                self,
                "エラー",
                "書き出しに必要な初期化が完了していません。",
            )
            return

        notes = list(getattr(timeline, "notes_list", []) or [])
        if not notes:
            QMessageBox.warning(
                self,
                "エラー",
                "ノートがないため書き出しできません。",
            )
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "音声ファイルを保存",
            "output.wav",
            "WAV Files (*.wav)",
        )
        if not file_path:
            return

        all_params = getattr(graph, "all_parameters", {}) or {}
        parameters: Dict[str, Any] = {
            "Pitch": list(all_params.get("Pitch", [])),
            "Gender": list(all_params.get("Gender", [])),
            "Tension": list(all_params.get("Tension", [])),
            "Breath": list(all_params.get("Breath", [])),
        }

        playback_worker = getattr(self, "_playback_prepare_thread", None)
        mixer_worker = getattr(self, "_audio_mixer_command_thread", None)
        if (
            playback_worker is not None and playback_worker.isRunning()
        ) or (
            mixer_worker is not None and mixer_worker.isRunning()
        ):
            self.statusBar().showMessage(
                "再生準備・音声デバイス処理の完了後にWAVを書き出してください。", 5000
            )
            return

        stop_playback = getattr(self, "stop_and_clear_playback", None)
        if callable(stop_playback):
            stop_playback()

        start_render = getattr(self, "_start_async_render", None)
        if not callable(start_render):
            QMessageBox.critical(
                self,
                "エラー",
                "非同期レンダリング機能が初期化されていません。",
            )
            return

        started = start_render(
            notes,
            parameters,
            file_path,
            is_pro=LicenseManager.is_pro(),
            play_after_success=False,
        )
        if not started:
            logger.warning("WAV書き出しを開始できませんでした: %s", file_path)

    def parse_ust_dict_to_note(self: Any, d: Dict[str, Any], current_time_sec: float = 0.0, tempo: float = 120.0) -> Any:
        """[LIVE] UST辞書 → NoteEvent変換"""
        from dataclasses import dataclass
        import importlib

        @dataclass
        class _UstFallbackNoteEvent:
            lyrics: str
            note_number: int
            start_time: float
            duration: float

        try:
            model_mod = importlib.import_module("modules.data.data_models")
            NoteEventCls: Any = getattr(model_mod, "NoteEvent", _UstFallbackNoteEvent)
        except Exception:
            NoteEventCls = _UstFallbackNoteEvent

        try:
            length_ticks_str = d.get('Length', '480')
            note_num_str = d.get('NoteNum', '64')
            lyric = str(d.get('Lyric', 'あ'))

            length_ticks = int(length_ticks_str)
            note_num = int(note_num_str)

            duration_sec = (length_ticks / 480.0) * (60.0 / tempo)

            note = NoteEventCls(lyrics=lyric, note_number=note_num, start_time=current_time_sec, duration=duration_sec)

            setattr(note, 'length', length_ticks)
            setattr(note, 'lyric', lyric)
            setattr(note, 'note_num', note_num)

            for k, v in d.items():
                if k.startswith("_ust_"):
                    setattr(note, k, v)

            return note, current_time_sec + duration_sec

        except (ValueError, TypeError) as e:
            print(f"DEBUG: UST Parse Error in note: {e}")
            # 壊れたノートをダミーの無音ノートとして混入させると、
            # タイムライン上の位置や後続ノートの時間計算を壊すため、
            # 呼び出し側で無視できる None を返す。
            return None, current_time_sec

    # MainWindow 側の実装を MRO で隠さないため、ここにはダミー実装を置かない。

    def export_to_midi_file(self: Any):
        """現在のボーカルトラックを標準MIDIファイルへ書き出す。"""
        timeline = getattr(self, "timeline_widget", None)
        if timeline is None:
            QMessageBox.warning(self, "MIDI書き出し", "タイムラインが初期化されていません。")
            return False

        tracks = list(getattr(self, "tracks", []) or [])
        current_index = int(getattr(self, "current_track_idx", 0))
        export_tracks: List[Dict[str, Any]] = []
        for index, track in enumerate(tracks):
            if str(getattr(track, "track_type", "vocal")).lower() != "vocal":
                continue
            notes = (
                list(getattr(timeline, "notes_list", []) or [])
                if index == current_index
                else list(getattr(track, "notes", []) or [])
            )
            if notes:
                export_tracks.append({
                    "name": str(getattr(track, "name", f"Vocal {index + 1}")),
                    "notes": notes,
                })

        # Compatibility with older single-track sessions without a track model.
        if not export_tracks:
            notes = list(getattr(timeline, "notes_list", []) or [])
            if notes:
                export_tracks.append({"name": "Vocal", "notes": notes})
        if not export_tracks:
            self.statusBar().showMessage("MIDIに書き出すノートがありません。", 4000)
            return False

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "MIDIファイルを書き出す",
            "output.mid",
            "MIDI Files (*.mid *.midi)",
        )
        if not file_path:
            return False
        if not file_path.lower().endswith((".mid", ".midi")):
            file_path += ".mid"

        try:
            from modules.data.midi_manager import save_midi_file

            tempo = float(getattr(timeline, "tempo", 120.0))
            save_midi_file(file_path, export_tracks, tempo=tempo)
            self.statusBar().showMessage(
                f"MIDI書き出し完了: {os.path.basename(file_path)}", 5000
            )
            return True
        except Exception as exc:
            logger.exception("MIDI書き出しエラー: %s", exc)
            QMessageBox.critical(
                self,
                "MIDI書き出しエラー",
                f"MIDIファイルの書き出しに失敗しました:\\n{exc}",
            )
            return False
