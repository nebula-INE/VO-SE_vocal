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
import multiprocessing
import os
from typing import Any, Dict, List, Optional, cast
from copy import deepcopy

from PySide6.QtWidgets import QFileDialog, QMessageBox

from modules.data.data_models import NoteEvent
from modules.data.ust_parser import UstParser, UstConverter

logger = logging.getLogger(__name__)

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
        return {
            "tracks": deepcopy(list(getattr(self, "tracks", []) or [])),
            "current_track_idx": int(getattr(self, "current_track_idx", 0)),
            "tempo": tempo,
            "graph_tempo": float(getattr(graph, "tempo", tempo)),
            "graph_parameters": deepcopy(
                getattr(graph, "all_parameters", {}) or {}
            ),
            "current_playback_time": float(getattr(self, "current_playback_time", 0.0)),
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
        self.update_scrollbar_range() if callable(
            getattr(self, "update_scrollbar_range", None)
        ) else None

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

    def load_ust_file(self: Any, file_path: str) -> bool:
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
            parser = UstParser()
            project = parser.load(file_path)
            note_dicts = UstConverter.to_note_dicts(project)

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

            # NoteEvent に変換してタイムラインに設定
            notes: List[NoteEvent] = []
            for d in note_dicts:
                try:
                    # UST 拡張フィールドは NoteEvent.from_dict() では無視されるが
                    # _ust_* キーを note 属性として後から設定する
                    note = NoteEvent.from_dict(d)

                    # 先行発声・オーバーラップが UST で明示されている場合のみ設定
                    if d.get("pre_utterance") is not None:
                        note.pre_utterance = d["pre_utterance"]
                    if d.get("overlap") is not None:
                        note.overlap = d["overlap"]

                    # UST 拡張フィールドをそのまま属性として保持
                    for k, v in d.items():
                        if k.startswith("_ust_"):
                            setattr(note, k, v)

                    notes.append(note)
                except Exception as exc:
                    logger.warning("NoteEvent 変換失敗: %s / %s", exc, d)

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
        """
        ファイルダイアログを開き、拡張子に応じて適切なローダーを呼び出す。
        対応形式: .ust, .vsqx, .mid, .json
        """
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "プロジェクトを開く",
            "",
            "対応ファイル (*.ust *.vsqx *.mid *.json);;"
            "UTAU プロジェクト (*.ust);;"
            "Vocaloid プロジェクト (*.vsqx);;"
            "MIDI ファイル (*.mid *.midi);;"
            "VO-SE プロジェクト (*.json)",
        )

        if not file_path:
            return

        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".ust":
            self.load_ust_file(file_path)
        elif ext == ".vsqx":
            self._load_vsqx(file_path)
        elif ext in (".mid", ".midi"):
            self.load_midi_file_from_path(file_path)
        elif ext == ".json":
            self.load_json_project(file_path)
        else:
            QMessageBox.warning(self, "非対応形式", f"対応していない形式です: {ext}")

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

    def load_json_project(self: Any, file_path: str) -> bool:
        """旧単一トラックJSONと新マルチトラックVOSE/JSONの両方を読み込む。"""
        try:
            before_state = self._capture_project_edit_state()
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            tempo = float(data.get("tempo", 120.0))
            raw_tracks = data.get("tracks")

            if isinstance(raw_tracks, list) and raw_tracks:
                existing_tracks = list(getattr(self, "tracks", []) or [])
                track_cls = type(existing_tracks[0]) if existing_tracks else None
                loaded_tracks = []

                for index, raw in enumerate(raw_tracks):
                    if not isinstance(raw, dict) or track_cls is None:
                        continue
                    track = track_cls(
                        str(raw.get("name", f"Track {index + 1}")),
                        str(raw.get("type", "vocal")),
                    )
                    track.notes = [
                        NoteEvent.from_dict(d)
                        for d in raw.get("notes", [])
                        if isinstance(d, dict)
                    ]
                    track.audio_path = str(
                        raw.get("audio_path", raw.get("audio", "")) or ""
                    )
                    mixer = raw.get("mixer", {})
                    track.volume = float(raw.get("volume", mixer.get("vol", 1.0)))
                    track.pan = float(raw.get("pan", mixer.get("pan", 0.0)))
                    track.is_muted = bool(raw.get("is_muted", False))
                    track.is_solo = bool(raw.get("is_solo", False))
                    if "engine_type" in raw:
                        track.engine_type = str(raw["engine_type"])
                    if "color_label" in raw:
                        track.color_label = str(raw["color_label"])
                    loaded_tracks.append(track)

                if not loaded_tracks:
                    raise ValueError("有効なトラックが見つかりません。")

                self.tracks = loaded_tracks
                requested_idx = int(data.get("current_track_idx", 0))
                self.current_track_idx = min(
                    max(requested_idx, 0),
                    len(self.tracks) - 1,
                )

                if hasattr(self, "refresh_track_list_ui"):
                    self.refresh_track_list_ui()

                # switch_track() は切替前のタイムラインを現在トラックへ退避する仕様なので、
                # 新規プロジェクト読込直後には使わない。読込済みデータを直接表示する。
                timeline = getattr(self, "timeline_widget", None)
                if timeline is not None:
                    timeline.set_notes(self.tracks[self.current_track_idx].notes)
                    timeline.update()

            else:
                # 旧1.3系JSONは現在のトラックへ読み込む。
                notes = [
                    NoteEvent.from_dict(d)
                    for d in data.get("notes", [])
                    if isinstance(d, dict)
                ]
                tracks = list(getattr(self, "tracks", []) or [])
                current_idx = int(getattr(self, "current_track_idx", 0))
                if tracks and 0 <= current_idx < len(tracks):
                    tracks[current_idx].notes = notes
                if getattr(self, "timeline_widget", None) is not None:
                    self.timeline_widget.set_notes(notes)

            timeline = getattr(self, "timeline_widget", None)
            if timeline is not None:
                timeline.tempo = tempo

            tempo_input = getattr(self, "tempo_input", None)
            if tempo_input is not None:
                tempo_input.setText(str(int(round(tempo))))

            self.current_playback_time = float(data.get("current_time", 0.0))
            set_time = getattr(self, "_set_transport_time", None)
            if callable(set_time):
                set_time(self.current_playback_time)

            note_count = len(getattr(timeline, "notes_list", []) or []) if timeline is not None else 0
            self.statusBar().showMessage(
                f"読み込み完了: {note_count} ノート ({os.path.basename(file_path)})",
                3000,
            )
            self._record_project_state_edit(
                before_state,
                self._capture_project_edit_state(),
                "JSONプロジェクト読み込み",
            )
            return True

        except Exception as exc:
            logger.exception("JSON/VOSE 読み込みエラー: %s", exc)
            QMessageBox.critical(
                self,
                "読み込みエラー",
                f"プロジェクトの読み込みに失敗しました:\n{exc}",
            )
            return False

    def load_midi_file_from_path(self: Any, file_path: str) -> bool:
        """MIDI ファイルを読み込んでタイムラインに設定する"""
        from modules.data.midi_manager import load_midi_file

        before_state = self._capture_project_edit_state()
        note_dicts = load_midi_file(file_path)
        if not note_dicts:
            self.statusBar().showMessage("MIDI: ノートを読み込めませんでした。")
            return False

        notes = [NoteEvent.from_dict(d) for d in note_dicts]

        tracks = list(getattr(self, "tracks", []) or [])
        current_idx = int(getattr(self, "current_track_idx", 0))
        if 0 <= current_idx < len(tracks):
            tracks[current_idx].notes = list(notes)

        if hasattr(self, "timeline_widget") and self.timeline_widget is not None:
            if hasattr(self.timeline_widget, "set_notes"):
                self.timeline_widget.set_notes(notes)
            elif hasattr(self.timeline_widget, "notes_list"):
                self.timeline_widget.notes_list = notes
                if hasattr(self.timeline_widget, "update"):
                    self.timeline_widget.update()

        self.statusBar().showMessage(
            f"MIDI 読み込み完了: {len(notes)} ノート ({os.path.basename(file_path)})"
        )
        self._record_project_state_edit(
            before_state,
            self._capture_project_edit_state(),
            "MIDI読み込み",
        )
        return True

    # ------------------------------------------------------------------
    # 内部: .vsqx 読み込み (現状は未実装)
    # ------------------------------------------------------------------

    def _load_vsqx(self: Any, file_path: str) -> None:
        """Vocaloid .vsqx 読み込み (未実装 — プレースホルダー)"""
        QMessageBox.information(
            self,
            "未対応形式",
            ".vsqx の読み込みは現在未実装です。\nUST または JSON 形式をお使いください。",
        )

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

    def import_voice_bank(self: Any, zip_path: str):
        """[LIVE] ZIP音源インストール完全版"""
        import shutil
        import zipfile
        from modules.gui.aural_engine import AuralAIEngine
        from modules.gui.shared import get_resource_path, DynamicsAIEngine

        extract_base_dir = get_resource_path("voices")
        os.makedirs(extract_base_dir, exist_ok=True)
        
        installed_name = None
        valid_files = [] 
        found_oto = False

        try:
            with zipfile.ZipFile(zip_path, 'r') as z:
                for info in z.infolist():
                    try:
                        filename = info.filename.encode('cp437').decode('cp932')
                    except Exception:
                        filename = info.filename
                    
                    if "__MACOSX" in filename or ".DS_Store" in filename:
                        continue
                    
                    valid_files.append((info, filename))
                    
                    if "oto.ini" in filename.lower():
                        found_oto = True
                        parts = filename.replace('\\', '/').strip('/').split('/')
                        if len(parts) > 1 and not installed_name:
                            installed_name = parts[-2]

                if not installed_name:
                    installed_name = os.path.splitext(os.path.basename(zip_path))[0]

                target_voice_dir = os.path.join(extract_base_dir, installed_name)
                
                if os.path.exists(target_voice_dir):
                    shutil.rmtree(target_voice_dir)
                os.makedirs(target_voice_dir, exist_ok=True)

                top_dirs = set()
                for _, fname in valid_files:
                    parts = fname.replace('\\', '/').strip('/').split('/')
                    if len(parts) > 1:
                        top_dirs.add(parts[0])
                    else:
                        top_dirs.add("")

                has_single_top_dir = len(top_dirs) == 1 and "" not in top_dirs
                single_top_dir = list(top_dirs)[0] if has_single_top_dir else ""

                for info, filename in valid_files:
                    normalized_fname = filename.replace('\\', '/').strip('/')
                    
                    if has_single_top_dir:
                        rel_path = normalized_fname[len(single_top_dir):].lstrip('/')
                    else:
                        rel_path = normalized_fname
                        
                    target_path = os.path.join(target_voice_dir, rel_path)
                    root_path = os.path.realpath(target_voice_dir)
                    target_real_path = os.path.realpath(target_path)
                    if os.path.commonpath([root_path, target_real_path]) != root_path:
                        raise ValueError(f"不正なZIPパスです: {filename}")
                    
                    if info.is_dir():
                        os.makedirs(target_path, exist_ok=True)
                        continue
                        
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with z.open(info) as source, open(target_path, "wb") as target:
                        shutil.copyfileobj(source, target)

            status_bar = self.statusBar()
            if not found_oto:
                if status_bar:
                    status_bar.showMessage(f"AI解析中: {installed_name} の原音設定を自動生成しています...", 0)
                if hasattr(self, 'generate_and_save_oto'):
                    self.generate_and_save_oto(target_voice_dir)

            aural_model = os.path.join(target_voice_dir, "aural_dynamics.onnx")
            std_model = os.path.join(target_voice_dir, "model.onnx")

            if os.path.exists(aural_model):
                self.dynamics_ai = AuralAIEngine() 
                if hasattr(self.dynamics_ai, 'load_model'):
                    cast(Any, self.dynamics_ai).load_model(aural_model)
                engine_msg = "上位Auralモデル"
            elif os.path.exists(std_model):
                self.dynamics_ai = DynamicsAIEngine()
                if hasattr(self.dynamics_ai, 'load_model'):
                    cast(Any, self.dynamics_ai).load_model(std_model)
                engine_msg = "標準Dynamicsモデル"
            else:
                self.dynamics_ai = AuralAIEngine() 
                engine_msg = "汎用Auralエンジン"

            v_manager = getattr(self, 'voice_manager', None)
            if v_manager and hasattr(v_manager, 'scan_utau_voices'):
                v_manager.scan_utau_voices()
            
            if hasattr(self, 'voice_gallery') and self.voice_gallery is not None:
                refresh_fn = getattr(self.voice_gallery, 'setup_gallery', getattr(self.voice_gallery, 'refresh_gallery', None))
                if refresh_fn:
                    refresh_fn()
                self.voice_gallery.update()
            
            msg = f"✅ '{installed_name}' インストール完了！ ({engine_msg})"
            if status_bar:
                status_bar.showMessage(msg, 5000)
            
            QMessageBox.information(
                self, 
                "導入成功", 
                f"音源 '{installed_name}' をインストールしました。\nエンジン: {engine_msg}\n\nキャラクター選択パネルから選択できます。"
            )

        except Exception as e:
            QMessageBox.critical(self, "導入エラー", f"インストール中にエラーが発生しました:\n{str(e)}")

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
                status_bar = self.statusBar()
                if status_bar:
                    status_bar.showMessage(f"音源を処理中: {os.path.basename(file_path)}")
                
                try:
                    zip_input_path = file_path
                    tmp_file_path = None
                    
                    if source_type == "url":
                        parsed = urlparse(file_path)
                        if parsed.scheme not in ("http", "https"):
                            raise ValueError(f"未対応のURLスキームです: {parsed.scheme}")
                        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                            tmp_file_path = tmp.name
                        urlretrieve(file_path, tmp_file_path)
                        zip_input_path = tmp_file_path

                    self.import_voice_bank(zip_input_path)
                    
                    if tmp_file_path and os.path.exists(tmp_file_path):
                        os.remove(tmp_file_path)

                except Exception as e:
                    if 'tmp_file_path' in locals() and tmp_file_path and os.path.exists(tmp_file_path):
                        os.remove(tmp_file_path)
                    QMessageBox.critical(self, "導入失敗", f"インストール中にエラーが発生しました:\n{str(e)}")

            elif file_lower.endswith(('.mid', '.midi')):
                if hasattr(self, 'load_file_from_path'):
                    self.load_file_from_path(file_path)
                
                status_bar = self.statusBar()
                if status_bar:
                    status_bar.showMessage(f"MIDIファイルを読み込みました: {os.path.basename(file_path)}")

            elif file_lower.endswith(('.json', '.ust')):
                if hasattr(self, 'load_file_from_path'):
                    self.load_file_from_path(file_path)
                
                status_bar = self.statusBar()
                if status_bar:
                    status_bar.showMessage(f"プロジェクトを読み込みました: {os.path.basename(file_path)}")

    # ======================================================================
    # 【従来実装】その他メソッド
    # ======================================================================

    def load_file_from_path(self: Any, filepath: str) -> bool:
        """ファイル拡張子に応じて、実際のプロジェクトローダーへ振り分ける。"""
        ext = os.path.splitext(filepath)[1].lower()
        try:
            if ext in (".mid", ".midi"):
                return bool(self.load_midi_file_from_path(filepath))
            if ext == ".ust":
                return bool(self.load_ust_file(filepath))
            if ext in (".json", ".vose"):
                return bool(self.load_json_project(filepath))
            if ext == ".vsqx":
                self._load_vsqx(filepath)
                return False
            if ext == ".ustx":
                self._parse_ustx(filepath)
                return False
            self.statusBar().showMessage(f"未対応のファイル形式です: {ext}", 3000)
            return False
        except Exception as exc:
            logger.exception("ファイル読み込みエラー: %s", exc)
            QMessageBox.critical(self, "読み込みエラー", f"ファイルの読み込みに失敗しました:\n{exc}")
            return False

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

            project_data = {
                "app_id": "VO_SE_Pro_2026",
                "version": "1.4.0",
                "project_name": os.path.splitext(os.path.basename(file_path))[0],
                "tempo": tempo,
                "current_track_idx": max(0, current_idx),
                "current_time": float(getattr(self, "current_playback_time", 0.0)),
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
        """[LIVE] 文字コード自動判別読み込み"""
        import chardet

        if not os.path.exists(filepath):
            print(f"エラー: ファイルが見つかりません: {filepath}")
            return None

        try:
            with open(filepath, 'rb') as f:
                raw_data = f.read()
        
            if not raw_data:
                return ""
            
            detected_encoding: Optional[str] = None
            try:
                detection_result = chardet.detect(raw_data)
                detected_encoding = detection_result.get('encoding')
                confidence = detection_result.get('confidence', 0)
            
                if confidence < 0.7:
                    detected_encoding = None
            except Exception as e:
                print(f"文字コード検出エラー: {e}")
                detected_encoding = None
        
            candidate_encodings = []
        
            if detected_encoding:
                candidate_encodings.append(detected_encoding)
        
            for enc in ['shift_jis', 'utf-8', 'utf-8-sig', 'cp932', 'euc-jp', 'iso-2022-jp']:
                if enc not in candidate_encodings:
                    candidate_encodings.append(enc)

            for encoding in candidate_encodings:
                try:
                    decoded_text = raw_data.decode(encoding, errors='replace')
                    print(f"ファイル読み込み成功: {filepath} ({encoding})")
                    return decoded_text
                
                except (UnicodeDecodeError, LookupError) :
                    continue

            print(f"警告: すべてのエンコーディングで失敗。cp932で強制デコード: {filepath}")
            return raw_data.decode('cp932', errors='replace')
        
        except Exception as e:
            print(f"ファイル読み込みエラー: {filepath} - {e}")
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
        """[LIVE] WAV出力 — 新シグネチャ (notes, parameters, file_path) 対応版"""
        from modules.data.licensing import LicenseManager

        tw = getattr(self, "timeline_widget", None)
        gw = getattr(self, "graph_editor_widget", None)
        engine = getattr(self, "vo_se_engine", None)

        if tw is None or gw is None or engine is None:
            QMessageBox.warning(
                self,
                "エラー",
                "書き出しに必要な初期化が完了していません。",
            )
            return

        notes = list(getattr(tw, "notes_list", []) or [])
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

        is_pro = LicenseManager.is_pro()
        mode_flag = 1 if is_pro else 0

        # graph_editor_widget からパラメータ辞書を組み立てる
        all_params = getattr(gw, "all_parameters", {}) or {}
        parameters: Dict[str, Any] = {
            "Pitch":   list(all_params.get("Pitch",   [])),
            "Gender":  list(all_params.get("Gender",  [])),
            "Tension": list(all_params.get("Tension", [])),
            "Breath":  list(all_params.get("Breath",  [])),
        }

        self.stop_and_clear_playback()
        status_bar = self.statusBar()
        if status_bar:
            status_bar.showMessage("レンダリング中...")

        # パッチ適用環境では v2 を優先 (VCV + Vibrato + Portamento 対応)
        v2_export_fn = getattr(engine, "export_to_wav_v2", None)
        v1_export_fn = getattr(engine, "export_to_wav", None)

        if v2_export_fn is None and v1_export_fn is None:
            QMessageBox.critical(
                self,
                "エラー",
                "エンジンに export_to_wav が実装されていません。",
            )
            return

        try:
            # v2 を優先。v2 は mode_flag を受け取れるため、Pro/Free の
            # レンダリングモードをファイル書き出しにも確実に渡す。
            if callable(v2_export_fn):
                result = v2_export_fn(
                    notes,
                    parameters,
                    file_path,
                    mode_flag=mode_flag,
                )
            elif callable(v1_export_fn):
                result = v1_export_fn(
                    notes,
                    parameters,
                    file_path,
                    mode_flag=mode_flag,
                )
            else:
                raise RuntimeError("エンジンのレンダリング関数を呼び出せません。")

            if result is None:
                QMessageBox.warning(
                    self,
                    "書き出し",
                    "レンダリング結果が空でした。",
                )
                return

            if status_bar:
                status_bar.showMessage(
                    f"エクスポート完了: {os.path.basename(file_path)}"
                )
            QMessageBox.information(
                self,
                "完了",
                f"レンダリングが完了しました！\nファイル: {file_path}",
            )

        except TypeError as exc:
            logger.exception("export_to_wav 呼び出し失敗: %s", exc)
            QMessageBox.critical(
                self,
                "エラー",
                "export_to_wav のシグネチャが一致しません。\n"
                "vo_se_engine_patch.apply_patch(VO_SE_Engine) "
                "が適用済みか確認してください。\n"
                f"詳細: {exc}",
            )
        except Exception as exc:
            logger.exception("書き出しエラー: %s", exc)
            QMessageBox.critical(
                self,
                "エラー",
                f"書き出し失敗: {exc}",
            )
            if status_bar:
                status_bar.showMessage("エラー発生")

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
        """[LIVE] MIDIエクスポート"""
        print("MIDIエクスポートを開始します...")

