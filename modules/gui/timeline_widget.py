import json
import logging
import os
import ctypes
import wave
import bisect
import numpy as np
from datetime import datetime
from typing import List, Dict, Any, Optional, Protocol, runtime_checkable, cast

from PySide6.QtWidgets import (QWidget, QApplication, QInputDialog, QLineEdit,
                               QMainWindow, QMenu)
from PySide6.QtCore import Qt, QRect, QRectF, Signal, Slot, QPoint, QPointF, QSize
from PySide6.QtGui import (QPainter, QPen, QBrush, QColor, QFont, QAction, QContextMenuEvent,
                            QLinearGradient, QPaintEvent, QMouseEvent, QKeyEvent, QWheelEvent,
                            QPixmap)  # [OPT] QPixmap追加

logger = logging.getLogger(__name__)

# ============================================================
# 1. データモデル
# ============================================================

@runtime_checkable
class NoteEventProtocol(Protocol):
    start_time: float
    duration: float
    note_number: int
    lyrics: str
    is_selected: bool
    phoneme: str
    onset: float
    overlap: float
    pre_utterance: float
    has_analysis: bool
    def to_dict(self) -> Dict[str, Any]: ...


class _FallbackNoteEvent:
    def __init__(self, note_number: int, start_time: float, 
                 duration: float, lyric: str = "あ") -> None:  #順序統一
        self.note_number: int = note_number
        self.start_time: float = start_time
        self.duration: float = duration
        self.lyric: str = lyric  # 
        self.lyrics: str = lyric  # 
        self.is_selected: bool = False
        self.phoneme: str = ""
        self.onset: float = 0.0
        self.overlap: float = 0.0
        self.pre_utterance: float = 0.0
        self.has_analysis: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "note_number": self.note_number,
            "start_time": self.start_time,
            "duration": self.duration,
            "lyric": self.lyric,
            "phoneme": self.phoneme,
            "onset": self.onset,
            "overlap": self.overlap,
            "pre_utterance": self.pre_utterance,
        }


try:
    import modules.data.data_models as _data_models
    NoteEventClass: Any = getattr(_data_models, "NoteEvent", _FallbackNoteEvent)
except Exception:
    NoteEventClass = _FallbackNoteEvent


# ============================================================
# 2. Janome Tokenizer
# ============================================================

class _FallbackTokenizer:
    def tokenize(self, text: str) -> List[Any]:
        return []


try:
    from janome.tokenizer import Tokenizer as _JanomeTokenizer
    _TOKENIZER_CLASS: Any = _JanomeTokenizer
except Exception:
    _TOKENIZER_CLASS = _FallbackTokenizer


# ============================================================
# 3. TimelineWidget
# ============================================================

class TimelineWidget(QWidget):
    """
    VO-SE Pro: メインタイムライン（ピアノロール）

    最適化一覧:
    [OPT-1] グリッドキャッシュ: _grid_pixmap にグリッドを一度だけ描画し、
            スクロール・ズーム変更時のみ再生成する。
            毎フレーム2000本の線を描く処理をゼロコストに削減。

    [OPT-2] 可視範囲クリッピング: _draw_notes / _draw_ai_phoneme_ghosts /
            _draw_parameter_curves で可視範囲外のノートを早期スキップ。
            ノート数が多い曲での描画コストをO(n)→O(visible)に削減。

    [OPT-3] ノート矩形キャッシュ: _note_rects_cache に QRectF を保持し、
            ノートリストが変化したときだけ再計算する。
            get_note_rect() の繰り返し計算を排除。

    [OPT-4] 選択変更の差分更新: is_selected が変わったノートだけ
            update(rect) で部分再描画する（将来拡張用に構造を用意）。
    """

    notes_changed_signal = Signal()
    scroll_synced_signal = Signal(int)
    # Undo/Redo用: ノート編集が確定したタイミングで
    # (操作前のnotes_list, 操作後のnotes_list, 操作の説明文) を渡す。
    # notes_changed_signal は描画更新やエンジン同期など高頻度に発火するのに対し、
    # このシグナルは「1つの編集操作が完了した」時にのみ発火する。
    edit_committed_signal = Signal(object, object, str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)

        self._drag_copy_notes: List[Any] = []   # コピー元ノート（移動中）
        self._drag_copy_offset: float = 0.0     # マウス

        self.tempo: float = 120.0
        self.pixels_per_beat: float = 80.0
        self.key_height_pixels: float = 20.0
        self.quantize_resolution: float = 0.25

        self.scroll_x_offset: float = 0.0
        self.scroll_y_offset: float = 0.0
        self._current_playback_time: float = 0.0

        self.notes_list: List[Any] = []
        self.parameters: Dict[str, Dict[float, float]] = {
            "Dynamics": {}, "Pitch": {}, "Vibrato": {}, "Formant": {}
        }
        self.current_param_layer: str = "Pitch"

        # GraphEditorWidgetの実データを直接参照する（ピアノロールのオーバーレイ描画専用）
        self._param_source: Any = None
        self.show_parameter_overlay: bool = True
        self.PITCH_AUTOMATION_SEMITONE_RANGE: float = 2.0

        self.audio_level: float = 0.0

        self.edit_mode: Optional[str] = None
        self.drag_start_pos: Optional[QPoint] = None
        self.selection_rect: QRect = QRect()
        self._resizing_note: Optional[Any] = None
        # Undo用: move/resize 操作開始時点のノート状態のスナップショット。
        # mousePressEvent で記録し、mouseReleaseEvent で変化があれば
        # edit_committed_signal を発火してから None に戻す。
        self._edit_snapshot_before: Optional[List[Any]] = None

        self._wave_cache: List[float] = []
        self._wave_cache_path: str = ""

        self.show_ai_phonemes: bool = True
        self.ai_ghost_alpha: int = 100

        # [OPT-1] グリッドキャッシュ
        self._grid_pixmap: Optional[QPixmap] = None
        self._grid_cache_key: tuple = ()  # (width, height, ppb, kh, scroll_y) で無効化

        # [OPT-3] ノート矩形キャッシュ
        # notes_list の id と scroll に依存するため、
        # _invalidate_note_rects() で明示的に無効化する
        self._note_rects_cache: Dict[int, QRectF] = {}  # id(note) -> QRectF
        self._note_rects_scroll: tuple = ()             # (scroll_x, scroll_y, ppb, kh)

        try:
            self.tokenizer: Any = _TOKENIZER_CLASS()
        except Exception:
            self.tokenizer = _FallbackTokenizer()

        self.vose_core: Any = None
        self.init_voice_engine()

        self.setMinimumSize(400, 200)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)

        self._transient_flashes = []  # トランジェント（一時的）なエフェクト管理用

    def set_parameter_source(self, graph_editor_widget: Any) -> None:
        """GraphEditorWidgetの参照を登録する。以後、ピアノロール上のカーブオーバーレイは
        このウィジェットが持つ実データ(all_parameters / current_mode)を直接読みに行く。"""
        self._param_source = graph_editor_widget

    def set_show_parameter_overlay(self, enabled: bool) -> None:
        self.show_parameter_overlay = bool(enabled)
        self.update()
      
    def copy_selected_notes_to_clipboard(self) -> None:
        """MainWindow互換: 選択ノートをJSONでクリップボードへ。"""
        try:
            from PySide6.QtWidgets import QApplication
            import json

            selected = [n for n in self.notes_list if getattr(n, "is_selected", False)]
            payload = []
            for n in selected:
                if hasattr(n, "to_dict"):
                    payload.append(n.to_dict())
                else:
                    payload.append({
                        "start_time": float(getattr(n, "start_time", 0.0)),
                        "duration": float(getattr(n, "duration", 0.0)),
                        "note_number": int(getattr(n, "note_number", 60)),
                        "lyrics": str(getattr(n, "lyrics", "la")),
                    })
    
            QApplication.clipboard().setText(json.dumps(payload, ensure_ascii=False))
        except Exception:
            # CI/環境差異でも落とさない
            pass

    def paste_notes_from_clipboard(self) -> None:
        """MainWindow互換: クリップボードJSONをノートとして追加。"""
        try:
            from PySide6.QtWidgets import QApplication
            import json

            text = QApplication.clipboard().text().strip()
            if not text:
                return

            data = json.loads(text)
            if not isinstance(data, list):
                return

            before_snapshot = self._snapshot_notes()

            for item in data:
                if not isinstance(item, dict):
                    continue
                
                # --- 修正ポイント: 定義済みの NoteEventClass (Fallback含む) を使用 ---
                note = NoteEventClass(
                    note_number=int(item.get("note_number", 60)),      # ✅ 正しい順序
                    start_time=float(item.get("start_time", 0.0)),
                    duration=float(item.get("duration", 0.5)),
                    lyric=str(item.get("lyric", item.get("lyrics", "la")))  # ✅ 後方互換性
                )
                note.is_selected = False
                # -----------------------------------------------------------
                
                self.notes_list.append(note)
                
                # 🌟 追加: ペーストされた各ノートに金色のフェードイン・エフェクトをトリガー
                self.trigger_flash(note, "add")

            self._invalidate_note_rects()  # [OPT-3] ノートが増えたのでキャッシュを無効化
            self.notes_changed_signal.emit()
            self._commit_edit(before_snapshot, "ペースト")
            self.update()
        except Exception:
            # CI環境や例外時にプロセスを落とさないためのガード
            pass

    def delete_selected_notes(self) -> None:
        """MainWindow互換: 選択ノート削除。"""
        try:
            before_snapshot = self._snapshot_notes()
            before = len(self.notes_list)
            
            # 🌟 追加: 実際の配列から削除される前に、選択されたノートをスキャンして消滅エフェクトを焼き付ける
            for n in self.notes_list:
                if getattr(n, "is_selected", False):
                    self.trigger_flash(n, "delete")
            
            # ノート配列の削除更新（代表のスマートな内包表記を100%維持）
            self.notes_list = [n for n in self.notes_list if not getattr(n, "is_selected", False)]
            
            if len(self.notes_list) != before:
                self.notes_changed_signal.emit()
                self._commit_edit(before_snapshot, "ノート削除")
                self.update()
        except Exception:
            pass

    # --- 座標変換メソッド  ---
    def time_to_x(self, t_seconds: float) -> float:
        """秒単位の時間を現在のスクロール・ズーム状態に応じたX座標に変換"""
        beats = self.seconds_to_beats(t_seconds)
        return beats * self.pixels_per_beat - self.scroll_x_offset

    def x_to_time(self, x_px: float) -> float:
        """X座標を秒単位の時間に変換（逆変換）"""
        beats = (x_px + self.scroll_x_offset) / self.pixels_per_beat
        return self.beats_to_seconds(beats)

    
    # ============================================================
    # [OPT-1] グリッドキャッシュ管理
    # ============================================================

    def _get_grid_cache_key(self) -> tuple:
        return (self.width(), self.height(),
                self.pixels_per_beat, self.key_height_pixels,
                self.scroll_y_offset)

    def _invalidate_grid(self) -> None:
        """スクロール・ズーム変更時にグリッドキャッシュを破棄する"""
        self._grid_pixmap = None

    def _ensure_grid_pixmap(self) -> QPixmap:
        """
        グリッドキャッシュを返す。
        キャッシュキーが変わっていれば再生成する。
        """
        key = self._get_grid_cache_key()
        if self._grid_pixmap is not None and self._grid_cache_key == key:
            return self._grid_pixmap

        # キャッシュミス: グリッドを QPixmap に描画して保存
        pixmap = QPixmap(self.width(), self.height())
        pixmap.fill(QColor(18, 18, 18))

        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)  # グリッドはAAなし

        # --- 横線（ノート行・黒鍵強調） ---
        pen_dark = QPen(QColor(35, 35, 35), 1)
        for n in range(128):
            y = (127 - n) * self.key_height_pixels - self.scroll_y_offset
            if y + self.key_height_pixels < 0:
                continue
            if y > self.height():
                break
            if (n % 12) in (1, 3, 6, 8, 10):
                p.fillRect(QRectF(0, y, self.width(), self.key_height_pixels),
                           QColor(22, 22, 22))
            p.setPen(pen_dark)
            p.drawLine(0, int(y), self.width(), int(y))

        # --- 縦線（可視範囲のみ）[OPT-2] ---
        # scroll_x_offset を考慮して最初の拍インデックスを計算
        first_beat = int(self.scroll_x_offset / self.pixels_per_beat)
        last_beat  = int((self.scroll_x_offset + self.width()) / self.pixels_per_beat) + 2
        for i in range(first_beat, last_beat):
            x = i * self.pixels_per_beat - self.scroll_x_offset
            p.setPen(QPen(QColor(58, 58, 60) if i % 4 == 0 else QColor(36, 36, 36), 1))
            p.drawLine(int(x), 0, int(x), self.height())

        p.end()

        self._grid_pixmap = pixmap
        self._grid_cache_key = key
        return pixmap

    # ============================================================
    # [OPT-3] ノート矩形キャッシュ管理
    # ============================================================

    def _get_scroll_key(self) -> tuple:
        return (self.scroll_x_offset, self.scroll_y_offset,
                self.pixels_per_beat, self.key_height_pixels)

    def _invalidate_note_rects(self) -> None:
        """ノートリスト変更時にキャッシュを全破棄する"""
        self._note_rects_cache.clear()
        self._note_rects_scroll = ()

    def _rebuild_note_rects_if_needed(self) -> None:
        """
        スクロール・ズームが変わったらノート矩形を全再計算する。
        ノートリスト変更時は _invalidate_note_rects() を先に呼ぶこと。
        """
        key = self._get_scroll_key()
        if self._note_rects_scroll == key and self._note_rects_cache:
            return
        self._note_rects_cache = {
            id(n): self._calc_note_rect(n) for n in self.notes_list
        }
        self._note_rects_scroll = key

    def _calc_note_rect(self, note: Any) -> QRectF:
        """座標計算の実体（キャッシュなし）"""
        x = self.seconds_to_beats(note.start_time) * self.pixels_per_beat - self.scroll_x_offset
        y = (127 - note.note_number) * self.key_height_pixels - self.scroll_y_offset
        w = self.seconds_to_beats(note.duration) * self.pixels_per_beat
        h = self.key_height_pixels
        return QRectF(x, y, w, h)

    def get_note_rect(self, note: Any) -> QRectF:
        """
        キャッシュ済み矩形を返す。
        マウスイベントなど頻繁に呼ばれる箇所でキャッシュを活用する。
        """
        nid = id(note)
        if nid in self._note_rects_cache:
            return self._note_rects_cache[nid]
        # キャッシュミス（新規ノートなど）: 計算して登録
        rect = self._calc_note_rect(note)
        self._note_rects_cache[nid] = rect
        return rect

    # ============================================================
    # 座標変換
    # ============================================================

    def seconds_to_beats(self, s: float) -> float:
        return s / (60.0 / self.tempo)

    def beats_to_seconds(self, b: float) -> float:
        return b * (60.0 / self.tempo)

    def quantize(self, val: float) -> float:
        return round(val / self.quantize_resolution) * self.quantize_resolution

    # ============================================================
    # 歌詞 → 音素解析
    # ============================================================

    def analyze_lyric_to_phoneme(self, text: str) -> str:
        try:
            tokens = self.tokenizer.tokenize(text)
            phonemes = []
            for t in tokens:
                reading = str(getattr(t, 'reading', '*'))
                surface = str(getattr(t, 'surface', ''))
                phonemes.append(reading if reading != "*" else surface)
            return "".join(phonemes) if phonemes else text
        except Exception:
            return text

    # ============================================================
    # 音声エンジン
    # ============================================================

    def init_voice_engine(self) -> None:
        voice_db_path = "assets/voice_db/"
        if not os.path.exists(voice_db_path):
            return
        for file in os.listdir(voice_db_path):
            if file.endswith(".wav"):
                phoneme = file.replace(".wav", "")
                try:
                    with wave.open(os.path.join(voice_db_path, file), 'rb') as wr:
                        frames = wr.readframes(wr.getnframes())
                        data = np.frombuffer(frames, dtype=np.int16)
                        if self.vose_core:
                            self.vose_core.load_embedded_resource(
                                phoneme.encode('utf-8'),
                                data.ctypes.data_as(ctypes.POINTER(ctypes.c_int16)),
                                len(data)
                            )
                except Exception as e:
                    logger.error(f"Voice load error: {e}")

    # ============================================================
    # データ入出力
    # ============================================================

    def export_all_data(self, file_path: str = "engine_input.json") -> None:
        top = self.window()
        char_name = getattr(top, 'current_voice', "Default_Standard")
        char_id = getattr(top, 'current_voice_id', "__INTERNAL__:standard")
        active_device = getattr(top, 'active_device', "CPU")

        data = {
            "metadata": {
                "tempo": self.tempo,
                "version": "1.4.0",
                "project": "VO-SE_Project",
                "character_name": char_name,
                "character_id": char_id,
                "render_device": active_device,
                "timestamp": datetime.now().isoformat(),
            },
            "notes": [
                {
                    "t": n.start_time, "d": n.duration, "n": n.note_number,
                    "p": self.analyze_lyric_to_phoneme(n.lyrics),
                    "lyric": n.lyrics,
                    "onset": float(getattr(n, 'onset', 0.0)),
                    "overlap": float(getattr(n, 'overlap', 0.0)),
                    "pre_utterance": float(getattr(n, 'pre_utterance', 0.0)),
                    "optimized": bool(getattr(n, 'has_analysis', False)),
                }
                for n in self.notes_list
            ],
            "parameters": {
                "pitch": [
                    {"time": cast(Any, ev).time, "value": cast(Any, ev).value} 
                    for ev in self.parameters.get("Pitch", [])
                ],
                "gender": [
                    {"time": cast(Any, ev).time, "value": cast(Any, ev).value} 
                    for ev in self.parameters.get("Gender", [])
                ],
                "tension": [
                    {"time": cast(Any, ev).time, "value": cast(Any, ev).value} 
                    for ev in self.parameters.get("Tension", [])
                ],
                "breath": [
                    {"time": cast(Any, ev).time, "value": cast(Any, ev).value} 
                    for ev in self.parameters.get("Breath", [])
                ],
            },
        }

        try:
            abs_path = os.path.abspath(file_path)
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            with open(abs_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.info(f"Exported (Voice: {char_name}) -> {abs_path}")
            if isinstance(top, QMainWindow):
                sb = top.statusBar()
                if sb:
                    sb.showMessage(f"Export Complete: {char_name}", 5000)
        except Exception as e:
            logger.error(f"Export failed: {e}")

    def get_all_notes_data(self) -> list:
        return self.notes_list

    def get_notes(self) -> list:
        return self.notes_list

    def get_max_beat_position(self) -> float:
        """現在のノート列の最終位置を「拍」単位で返す（スクロールバー範囲計算用）"""
        if not self.notes_list:
            return 0.0
        max_end_sec = max((n.start_time + n.duration) for n in self.notes_list)
        return self.seconds_to_beats(max_end_sec)

    def set_notes(self, notes: List[Any]) -> None:
        self.notes_list = list(notes or [])
        self._invalidate_note_rects()
        self.notes_changed_signal.emit()
        self.update()

    def _snapshot_notes(self) -> List[Any]:
        """
        Undo/Redo用にノートリストの深いコピーを作る。

        NoteEventClass は実行時に modules.data.data_models.NoteEvent または
        _FallbackNoteEvent に動的解決されるため、属性を個別に列挙して
        再構築するのではなく copy.deepcopy で丸ごとコピーする方が安全。
        """
        import copy
        return copy.deepcopy(self.notes_list)

    def _restore_notes_snapshot(self, snapshot: List[Any]) -> None:
        """_snapshot_notes() で取得したスナップショットを復元する(Undo/Redo用)。"""
        import copy
        self.notes_list = copy.deepcopy(snapshot)
        self._invalidate_note_rects()
        self.notes_changed_signal.emit()
        self.update()

    def _commit_edit(self, before_snapshot: Optional[List[Any]], description: str) -> None:
        """
        編集操作の確定処理。

        before_snapshot（操作開始前の状態）と現在の notes_list を比較し、
        実質的な変化がある場合のみ edit_committed_signal を発火して
        MainWindow 側に Undo 履歴への登録を依頼する。
        変化が無い場合（クリックだけでドラッグが発生しなかった等）は発火しない。
        """
        if before_snapshot is None:
            return
        after_snapshot = self._snapshot_notes()
        if self._notes_equal(before_snapshot, after_snapshot):
            return
        self.edit_committed_signal.emit(before_snapshot, after_snapshot, description)

    @staticmethod
    def _notes_equal(a: List[Any], b: List[Any]) -> bool:
        """2つのノートリストが実質的に同じ内容かを比較する(Undo判定用)。"""
        if len(a) != len(b):
            return False
        try:
            for na, nb in zip(a, b):
                if (na.start_time != nb.start_time or
                        na.duration != nb.duration or
                        na.note_number != nb.note_number or
                        getattr(na, "lyrics", None) != getattr(nb, "lyrics", None)):
                    return False
            return True
        except Exception:
            # 比較に失敗した場合は安全側に倒し、変化ありとして扱う
            return False

    def get_selected_notes_range(self) -> Optional[tuple[float, float]]:
        selected = [n for n in self.notes_list if getattr(n, "is_selected", False)]
        if not selected:
            return None
        start = min(float(getattr(n, "start_time", 0.0)) for n in selected)
        end = max(
            float(getattr(n, "start_time", 0.0)) + float(getattr(n, "duration", 0.0))
            for n in selected
        )
        return start, end

    def set_current_time(self, t: float) -> None:
        self.set_playback_time(t)

    def add_note_from_midi(self, pitch: int, start_beat: float, duration_beat: float) -> None:
        new_note = NoteEventClass(
            note_number=pitch,                              # ✅ 修正
            start_time=self.beats_to_seconds(start_beat),   # ✅ 修正
            duration=self.beats_to_seconds(duration_beat),  # ✅ 修正
            lyric="la"
        )
        new_note.phoneme = "la"
        self.notes_list.append(new_note)
        self._invalidate_note_rects()
        self.notes_changed_signal.emit()
        self.update()

    # ============================================================
    # 音声波形
    # ============================================================

    def get_audio_peaks(self, file_path: str, num_peaks: int = 2000) -> List[float]:
        if not file_path or not os.path.exists(file_path):
            return []
        try:
            with wave.open(file_path, 'rb') as w:
                params = w.getparams()
                if params.nframes == 0:
                    return []
                samples = np.frombuffer(w.readframes(params.nframes), dtype=np.int16)
                if params.nchannels == 2:
                    samples = samples[::2]
                num_peaks = min(num_peaks, max(1, len(samples)))
                chunks = np.array_split(samples, num_peaks)
                peaks = [float(np.max(np.abs(c))) if len(c) > 0 else 0.0 for c in chunks]
                max_val = float(np.max(peaks)) if peaks else 1.0
                return [p / max_val for p in peaks] if max_val > 0 else peaks
        except Exception as e:
            logger.error(f"Waveform Analysis Error: {e}")
            return []

    def _draw_audio_waveform(self, p: QPainter) -> None:
        audio_path = str(getattr(self.window(), 'current_audio_path', ''))
        if not audio_path or not os.path.exists(audio_path):
            return
        if self._wave_cache_path != audio_path:
            self._wave_cache = self.get_audio_peaks(audio_path)
            self._wave_cache_path = audio_path
        if not self._wave_cache:
            return
        p.setPen(QPen(QColor(0, 255, 255, 50), 1))
        mid_y = self.height() / 2.0
        max_h = self.height() * 0.7
        interval_px = (self.tempo / 60.0) * self.pixels_per_beat * 0.05
        for i, peak in enumerate(self._wave_cache):
            x = i * interval_px - self.scroll_x_offset
            if x < -interval_px:
                continue
            if x > self.width():
                break
            h = peak * max_h
            p.drawLine(int(x), int(mid_y - h / 2), int(x), int(mid_y + h / 2))

    # ============================================================
    # 描画
    # ============================================================

    _PARAM_COLORS: Dict[str, QColor] = {
        "Dynamics": QColor(255, 45, 85),
        "Pitch":    QColor(0, 255, 255),
        "Vibrato":  QColor(255, 165, 0),
        "Formant":  QColor(200, 100, 255),
    }

    def paintEvent(self, event: QPaintEvent) -> None:
        self._rebuild_note_rects_if_needed()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        p.drawPixmap(0, 0, self._ensure_grid_pixmap())
        self._draw_audio_waveform(p)
        self._draw_glow(p)
        if self.show_ai_phonemes:
            self._draw_ai_phoneme_ghosts(p)
        
        self._draw_notes(p)
        self._draw_parameter_curves(p)
        self._draw_transient_flashes(p)  # 🌟 ノートの上に重ねてフラッシュを描画
        
        self._draw_selection_rect(p)
        self._draw_playhead(p)
        p.end()

    def trigger_flash(self, note: Any, flash_type: str = "add") -> None:
        """ノートの追加・削除時にグリッド同期型の美麗なフェードエフェクトを発生させる"""
        if not hasattr(self, "_transient_flashes"):
            self._transient_flashes = []

        # オブジェクトそのものではなく固定の「時間・音高」を記録するため、
        # 削除されたノートであってもその座標にエフェクトを焼き付けることが可能
        flash_item = {
            "start_time": float(note.start_time),
            "duration": float(note.duration),
            "note_number": int(note.note_number),
            "type": flash_type,
            "alpha": 220 if flash_type == "add" else 255
        }
        self._transient_flashes.append(flash_item)
        
        # 40ms間隔（約25fps）で滑らかにフェードアウトを計算するタイマーループ
        self._run_flash_animation_step(flash_item, steps=8)

    def _run_flash_animation_step(self, flash_item: dict, steps: int) -> None:
        from PySide6.QtCore import QTimer
        if steps <= 0:
            if flash_item in self._transient_flashes:
                self._transient_flashes.remove(flash_item)
            self.update()
            return

        # 指数関数的にアルファを減衰させて滑らかな消え方を演出
        flash_item["alpha"] = int(flash_item["alpha"] * 0.55)
        self.update()
        
        QTimer.singleShot(40, lambda: self._run_flash_animation_step(flash_item, steps - 1))

    def _draw_transient_flashes(self, p: QPainter) -> None:
        """現在のスクロール・ズーム状態を毎フレーム動的に反映してエフェクトを描画"""
        if not hasattr(self, "_transient_flashes") or not self._transient_flashes:
            return

        for f in self._transient_flashes:
            x = self.time_to_x(f["start_time"])
            y = (127 - f["note_number"]) * self.key_height_pixels - self.scroll_y_offset
            w = self.seconds_to_beats(f["duration"]) * self.pixels_per_beat
            h = self.key_height_pixels

            rect = QRectF(x, y, w, h)
            
            # 可視範囲外（画面外）ならクリッピングして計算リソースを保護
            if rect.right() < 0 or rect.left() > self.width():
                continue

            p.save()
            if f["type"] == "add":
                # 追加時：サイバーゴールド（金色）の鮮烈な枠線＋ネオン塗り
                gold = QColor(255, 215, 0, f["alpha"])
                p.setPen(QPen(gold, 2, Qt.PenStyle.SolidLine))
                p.setBrush(QBrush(QColor(255, 215, 0, int(f["alpha"] * 0.25))))
                p.drawRect(rect)
            elif f["type"] == "delete":
                # 削除時：熱源が融解するように消え去るネオンオレンジ
                orange = QColor(255, 69, 0, f["alpha"])
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(orange))
                p.drawRect(rect)
            p.restore()

    def resizeEvent(self, event: Any) -> None:
        """ウィンドウリサイズ時にグリッドキャッシュを無効化する"""
        self._invalidate_grid()
        super().resizeEvent(event)

    def _draw_glow(self, p: QPainter) -> None:
        if self.audio_level <= 0.001:
            return
        cx = int(self.seconds_to_beats(self._current_playback_time)
                 * self.pixels_per_beat - self.scroll_x_offset)
        gw = int(self.audio_level * 150)
        grad = QLinearGradient(float(cx - gw), 0.0, float(cx + gw), 0.0)
        grad.setColorAt(0, QColor(255, 45, 85, 0))
        grad.setColorAt(0.5, QColor(255, 45, 85, int(self.audio_level * 150)))
        grad.setColorAt(1, QColor(255, 45, 85, 0))
        p.fillRect(self.rect(), QBrush(grad))

    def _draw_ai_phoneme_ghosts(self, p: QPainter) -> None:
        """onset をノート左側にグラデーションで可視化 [OPT-2: 可視範囲クリッピング]"""
        vw = self.width()
        for n in self.notes_list:
            rect = self.get_note_rect(n)
            # [OPT-2] 可視範囲外を早期スキップ
            if rect.right() < 0 or rect.left() > vw:
                continue
            onset_px = self.seconds_to_beats(float(getattr(n, 'onset', 0.1))) * self.pixels_per_beat
            ghost_rect = QRectF(rect.left() - onset_px, rect.top(), onset_px, rect.height())
            p.setPen(Qt.PenStyle.NoPen)
            grad = QLinearGradient(ghost_rect.topLeft(), ghost_rect.topRight())
            grad.setColorAt(0, QColor(0, 255, 255, 0))
            grad.setColorAt(1, QColor(0, 255, 255, self.ai_ghost_alpha))
            p.setBrush(QBrush(grad))
            p.drawRect(ghost_rect)
            p.setPen(QPen(QColor(0, 255, 255, 180), 1, Qt.PenStyle.DashLine))
            p.drawLine(int(rect.left()), int(rect.top()),
                       int(rect.left()), int(rect.bottom()))

    def _draw_notes(self, painter: QPainter) -> None:
        """
        [VO-SE Pro: Ultra Fast Rendering & High-End UX]
        代表、二分探索に加えて、Apple/Logic Pro級の視覚的階層化(LOD)を実装しました。
        """
        if not self.notes_list:
            return

        import bisect

        # --- 1. 描画範囲の計算 ---
        vw = self.width()
        vh = self.height()
        visible_start_time = self.scroll_x_offset / self.pixels_per_beat
        visible_end_time = (self.scroll_x_offset + vw) / self.pixels_per_beat

        # --- 2. 高速シーク ---
        # 描画前に一度だけソートを保証（データ量が多い場合は外部で管理するのがベスト）
        self.notes_list.sort(key=lambda n: n.start_time)
        start_times = [n.start_time for n in self.notes_list]
        start_idx = bisect.bisect_left(start_times, visible_start_time - 1.0)

        # --- 3. 描画ループ ---
        for i in range(start_idx, len(self.notes_list)):
            n = self.notes_list[i]
            if n.start_time > visible_end_time:
                break

            rect = self.get_note_rect(n)
            if rect.bottom() < 0 or rect.top() > vh:
                continue

            # --- [三：ブラッシュアップ] 視覚演出ロジック ---
            is_selected = bool(getattr(n, 'is_selected', False))
            
            # 配色決定（Apple Neon Style）
            if is_selected:
                base_color = QColor(255, 159, 10)  # オレンジ
                border_color = base_color.lighter(140)
            else:
                base_color = QColor(10, 132, 255)  # ブルー
                border_color = base_color.lighter(120)

            # A. 本体グラデーション描画
            gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            gradient.setColorAt(0, border_color)   # 上部は明るく
            gradient.setColorAt(1, base_color)     # 下部は基本色
            
            painter.setBrush(QBrush(gradient))
            painter.setPen(QPen(border_color, 1.2)) # わずかに光るエッジ
            painter.drawRoundedRect(rect, 3, 3)

            # B. LOD (Level of Detail) ロジック
            # ノートの横幅に応じて描画する情報の密度を変える
            note_w = rect.width()

            if note_w > 20: # 歌詞を表示する最小幅
                # 歌詞描画
                painter.setPen(Qt.GlobalColor.white)
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                
                # テキストがはみ出さないように elidedText 的な処理
                lyric_text = n.lyrics
                painter.drawText(
                    rect.adjusted(5, 0, -2, 0),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                    lyric_text
                )

                if note_w > 50 and rect.height() > 18: # 音素を表示する幅と高さ
                    # 音素描画（少し透明度を下げて階層化）
                    painter.setPen(QColor(255, 255, 255, 170))
                    painter.setFont(QFont("Consolas", 7))
                    phoneme = getattr(n, 'phoneme', "") or self.analyze_lyric_to_phoneme(n.lyrics)
                    painter.drawText(
                        rect.adjusted(5, int(self.key_height_pixels * 0.55), 0, 0),
                        Qt.AlignmentFlag.AlignLeft,
                        phoneme
                    )

            # C. 選択中の「グロー（発光）」演出（オプション）
            if is_selected:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(255, 255, 255, 100), 2))
                painter.drawRoundedRect(rect.adjusted(-1, -1, 1, 1), 4, 4)

    def _draw_parameter_curves(self, p: QPainter) -> None:
        if not self.show_parameter_overlay or self._param_source is None:
            return

        source = self._param_source
        all_params = getattr(source, "all_parameters", None)
        if not all_params:
            return

        current_mode = getattr(source, "current_mode", "Pitch")
        colors = getattr(source, "colors", self._PARAM_COLORS)

        # Pitchモードの描画がある場合だけ、ノート検索用の開始時刻リストを1回作る
        note_starts = [n.start_time for n in self.notes_list] if self.notes_list else []

        # 非アクティブなパラメーターは薄いゴースト表示
        for name, events in all_params.items():
            if name == current_mode or not events:
                continue
            self._draw_param_events(p, events, name,
                                     colors.get(name, QColor(200, 200, 200)),
                                     alpha=50, width=1, note_starts=note_starts)

        # アクティブなパラメーターは太く鮮やかに、最後に描いて最前面に
        active_events = all_params.get(current_mode, [])
        if active_events:
            self._draw_param_events(p, active_events, current_mode,
                                     colors.get(current_mode, QColor(255, 255, 255)),
                                     alpha=235, width=2, note_starts=note_starts)

    def _draw_param_events(self, p: QPainter, events: list, mode: str, color: QColor,
                            alpha: int, width: int, note_starts: List[float]) -> None:
        if not events:
            return

        vw = self.width()
        ordered = sorted(events, key=lambda e: getattr(e, "time", 0.0))

        glow_color = QColor(color)
        glow_color.setAlpha(int(alpha * 0.30))
        glow_pen = QPen(glow_color, width * 3, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

        core_color = QColor(color)
        core_color.setAlpha(alpha)
        core_pen = QPen(core_color, width, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

        prev: Optional[QPointF] = None
        for ev in ordered:
            t = float(getattr(ev, "time", 0.0))
            x = self.seconds_to_beats(t) * self.pixels_per_beat - self.scroll_x_offset

            if x > vw + 10:
                break

            val = float(getattr(ev, "value", 0.0))
            y = self._param_value_to_y(mode, t, val, note_starts)
            curr = QPointF(x, y)

            if prev is not None and x > -10:
                p.setPen(glow_pen)
                p.drawLine(prev, curr)
                p.setPen(core_pen)
                p.drawLine(prev, curr)

            prev = curr

    def _param_value_to_y(self, mode: str, time_sec: float, value: float,
                          note_starts: List[float]) -> float:
        """パラメーター値をピアノロール上のY座標に変換する。

        Pitch: そのノートの実音高 + ピッチベンド偏差(半音換算) を絶対音高として求め、
               ノート描画と全く同じ座標系 (127 - note_number) * key_height_pixels で
               マッピングする。これによりカーブが実際のノートの上を通って見える。
        それ以外: 0.0〜1.0のパラメーターとして、ピアノロール下部の帯にミニグラフ表示する。
        """
        if mode == "Pitch":
            base_note = self._get_note_pitch_at_time(time_sec, note_starts)
            semitone_offset = (value / 8191.0) * self.PITCH_AUTOMATION_SEMITONE_RANGE
            absolute_pitch = base_note + semitone_offset
            return (127.0 - absolute_pitch) * self.key_height_pixels - self.scroll_y_offset

        band_h = max(40.0, self.height() * 0.18)
        band_top = self.height() - band_h - 4
        clamped = max(0.0, min(1.0, value))
        return band_top + band_h * (1.0 - clamped)

    def _get_note_pitch_at_time(self, time_sec: float, note_starts: List[float]) -> float:
        """指定時刻を含む(または直前の)ノートの音高を返す。ノートが1つもなければA4(69)を仮定値とする。
        note_starts は self.notes_list と同じ並び順・昇順ソート済みであること。"""
        if not self.notes_list:
            return 69.0
        idx = bisect.bisect_right(note_starts, time_sec) - 1
        if idx < 0:
            return float(self.notes_list[0].note_number)
        return float(self.notes_list[idx].note_number)

    def _draw_selection_rect(self, p: QPainter) -> None:
        if self.edit_mode == "select_box" and self.selection_rect.isValid():
            # Apple風の洗練された選択エリア（システムブルーの透過）
            base_blue = QColor(10, 132, 255)
            
            # 枠線（少し明るく）
            p.setPen(QPen(base_blue.lighter(120), 1.5, Qt.PenStyle.DashLine))
            
            # 塗りつぶし（極めて薄く）
            bg_color = QColor(base_blue)
            bg_color.setAlpha(30)
            p.setBrush(QBrush(bg_color))
            
            p.drawRect(self.selection_rect)

    def _draw_playhead(self, p: QPainter) -> None:
        cx = int(self.seconds_to_beats(self._current_playback_time)
                 * self.pixels_per_beat - self.scroll_x_offset)
                 
        # 画面外なら描画をスキップして負荷削減
        if cx < -10 or cx > self.width() + 10:
            return

        head_color = QColor(255, 45, 85) # ビビッドなレッド（アクセントカラー）

        # 1. 線の発光エフェクト（太い半透明）
        p.setPen(QPen(QColor(255, 45, 85, 80), 3))
        p.drawLine(cx, 0, cx, self.height())

        # 2. 中心のソリッドな線
        p.setPen(QPen(head_color, 1.5))
        p.drawLine(cx, 0, cx, self.height())

        # 3. プレイヘッドの「頭（逆三角形）」を描画
        p.setBrush(QBrush(head_color))
        p.setPen(Qt.PenStyle.NoPen)
        from PySide6.QtCore import QPoint
        p.drawPolygon([
            QPoint(cx - 7, 0),   # 左上
            QPoint(cx + 7, 0),   # 右上
            QPoint(cx, 10)       # 下の尖った部分
        ])

    # ============================================================
    # マウス・ホイールイベント    
    # ============================================================

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # --- [吸い付くズームのロジック] ---
            zoom_factor = 1.1 if event.angleDelta().y() > 0 else 0.9
            
            # 1. 現在のマウス位置（ピクセル）を取得
            mouse_x = event.position().x()
            
            # 2. ズーム前の「マウス下の時間（拍数）」を固定する
            # (ピクセル + スクロール量) / 倍率 = ズーム前の絶対時間
            time_at_mouse = (mouse_x + self.scroll_x_offset) / self.pixels_per_beat
            
            # 3. 倍率を更新
            old_ppb = self.pixels_per_beat
            self.pixels_per_beat = max(10.0, min(500.0, self.pixels_per_beat * zoom_factor))
            
            # 4. ズーム後のスクロール位置を再計算
            # 新しいスクロール位置 = (絶対時間 * 新倍率) - マウスのピクセル位置
            self.scroll_x_offset = (time_at_mouse * self.pixels_per_beat) - mouse_x
            self.scroll_x_offset = max(0, self.scroll_x_offset)
            
            self._invalidate_grid() # グリッド再描画
            self._invalidate_note_rects()
            self.scroll_synced_signal.emit(int(self.scroll_x_offset))
            self.update()
        else:
            # 通常のスクロール（横）
            delta = event.angleDelta().y()
            self.scroll_x_offset = max(0, self.scroll_x_offset - delta)
            self._invalidate_grid()
            self._invalidate_note_rects()
            self.scroll_synced_signal.emit(int(self.scroll_x_offset))
            self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event is None:
            return
        pos_f = event.position()
        self.drag_start_pos = QPoint(int(pos_f.x()), int(pos_f.y()))

        if event.button() == Qt.MouseButton.RightButton:
            return


        # ★ Ctrlキーが押されていて、かつノートの上ならコピーモード開始
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            for n in reversed(self.notes_list):
                r = self.get_note_rect(n)
                if r.contains(pos_f):
                    # 選択中のノートをすべてコピー
                    selected = [n for n in self.notes_list if getattr(n, 'is_selected', False)]
                    if not selected:
                        # クリックしたノートだけ選択
                        self.deselect_all()
                        n.is_selected = True
                        selected = [n]
                    
                    # コピーを作成（ディープコピー）
                    import copy
                    self._drag_copy_notes = []
                    for src in selected:
                        clone = copy.deepcopy(src)
                        clone.is_selected = True
                        src.is_selected = False  # 元の選択を外す
                        self.notes_list.append(clone)
                        self._drag_copy_notes.append(clone)
                    
                    # オフセット計算（マウス位置を基点にドラッグ）
                    self._drag_copy_offset = pos_f.x() - self.time_to_x(clone.start_time)
                    self.edit_mode = "drag_copy"
                    self._edit_snapshot_before = self._snapshot_notes()
                    self.update()
                    return

        for n in reversed(self.notes_list):
            r = self.get_note_rect(n)
            if QRectF(r.right() - 12, r.top(), 12, r.height()).contains(pos_f):
                self.edit_mode = "resize"
                self._resizing_note = n
                self._edit_snapshot_before = self._snapshot_notes()
                self.deselect_all()
                n.is_selected = True
                return
            if r.contains(pos_f):
                if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
                    if not n.is_selected:
                        self.deselect_all()
                n.is_selected = True
                self.edit_mode = "move"
                self._edit_snapshot_before = self._snapshot_notes()
                self.update()
                return

        if not (event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self.deselect_all()
        self.edit_mode = "select_box"
        self.selection_rect = QRect(self.drag_start_pos, QSize(0, 0))
        self.update()

    def _get_snapped_time(self, raw_time: float) -> float:
        """[UX: マグネット] 最も近いグリッドに吸い付かせる計算"""
        # grid_unit: 0.25 = 16分音符
        grid_unit = 0.25 
        snapped = round(raw_time / grid_unit) * grid_unit
        
        # 吸い付いた瞬間にわずかにフィードバック（振動や色変化）を
        # 与えるためのフラグをここで立てることも可能です
        return snapped

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if event is None or self.drag_start_pos is None:
            return
            
        pos_f = event.position()
        curr_pos = QPoint(int(pos_f.x()), int(pos_f.y()))

        # --- [1. エッジ・オートスクロール処理] ---
        # ドラッグ中にマウスが画面端にあるかチェック
        self._check_edge_scroll(curr_pos)

        # ★ コピードラッグ中
        if self.edit_mode == "drag_copy" and self._drag_copy_notes:
            dx_beats = (curr_pos.x() - self.drag_start_pos.x()) / self.pixels_per_beat
            dt = self.beats_to_seconds(dx_beats)
            
            # 基点となる最初のノートの元の開始時刻を保持
            base_start = self._drag_copy_notes[0].start_time - dt
            for clone in self._drag_copy_notes:
                clone.start_time = max(0.0, base_start + dt)
            self._invalidate_note_rects()
            self.update()
            return


        if self.edit_mode == "draw_parameter":
            self._add_param_pt(pos_f)

        elif self.edit_mode == "move":
            # --- [2. マグネット・スナップ移動] ---
            # 移動量を拍数で計算
            dx_beats = (curr_pos.x() - self.drag_start_pos.x()) / self.pixels_per_beat
            dy_notes = -int(round((curr_pos.y() - self.drag_start_pos.y()) / self.key_height_pixels))
            
            # クオンタイズ（吸い付き）処理: 例 0.25 = 16分音符
            res = getattr(self, "quantize_resolution", 0.25)
            snapped_dx = round(dx_beats / res) * res
            dt = self.beats_to_seconds(snapped_dx)

            if abs(dt) > 0.0001 or dy_notes != 0:
                for n in self.notes_list:
                    if getattr(n, 'is_selected', False):
                        # スナップした分だけ移動
                        n.start_time = max(0.0, n.start_time + dt)
                        n.note_number = max(0, min(127, n.note_number + dy_notes))
                        # 操作中の視覚フィードバック用フラグ
                        setattr(n, 'is_dragging', True)

                self._invalidate_note_rects()
                # スナップした位置を基準に次のドラッグを開始（累積誤差防止）
                self.drag_start_pos = curr_pos
                self.notes_changed_signal.emit()

        elif self.edit_mode == "resize" and self._resizing_note is not None:
            # --- [3. リサイズのスナップ] ---
            note_start_px = (self.seconds_to_beats(self._resizing_note.start_time) 
                             * self.pixels_per_beat)
            raw_w_beats = (curr_pos.x() + self.scroll_x_offset - note_start_px) / self.pixels_per_beat
            
            res = getattr(self, "quantize_resolution", 0.25)
            snapped_w_beats = max(res, round(raw_w_beats / res) * res)
            
            self._resizing_note.duration = self.beats_to_seconds(snapped_w_beats)
            self._invalidate_note_rects()

        elif self.edit_mode == "select_box":
            self.selection_rect = QRect(self.drag_start_pos, curr_pos).normalized()
            for n in self.notes_list:
                # 高速化した矩形キャッシュを利用
                n.is_selected = self.selection_rect.intersects(self.get_note_rect(n).toRect())

        self.update()

    def _check_edge_scroll(self, pos: QPoint) -> None:
        """ドラッグ中に画面の端で自動スクロールさせる内部メソッド"""
        margin = 40  # 反応する範囲（ピクセル）
        max_speed = 20.0
        scrolled = False
        
        # 右端付近
        if pos.x() > self.width() - margin:
            # 端に近いほど速くスクロール
            ratio = (pos.x() - (self.width() - margin)) / margin
            self.scroll_x_offset += max_speed * min(1.0, ratio)
            scrolled = True
        # 左端付近
        elif pos.x() < margin and self.scroll_x_offset > 0:
            ratio = (margin - pos.x()) / margin
            self.scroll_x_offset = max(0, self.scroll_x_offset - max_speed * min(1.0, ratio))
            scrolled = True

        if scrolled:
            self._invalidate_grid()
            self._invalidate_note_rects()
            self.scroll_synced_signal.emit(int(self.scroll_x_offset))
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self.edit_mode == "draw_parameter":
            self._smooth_param()

        if self.edit_mode == "drag_copy" and self._drag_copy_notes:
            # コピー完了 → 履歴に登録
            self.notes_changed_signal.emit()
            self._commit_edit(self._edit_snapshot_before, "ノートコピー")
            self._drag_copy_notes = []
            self.edit_mode = None
            self.drag_start_pos = None
            self.update()
            return
        elif self.edit_mode in ("move", "resize"):
            for n in self.notes_list:
                if getattr(n, 'is_selected', False) or n == self._resizing_note:
                    n.start_time = self.beats_to_seconds(
                        self.quantize(self.seconds_to_beats(n.start_time)))
                    n.duration = self.beats_to_seconds(
                        max(self.quantize_resolution,
                            self.quantize(self.seconds_to_beats(n.duration))))
            self._invalidate_note_rects()  # [OPT-3] 量子化後に再計算
            self.notes_changed_signal.emit()
            description = "ノート移動" if self.edit_mode == "move" else "ノートリサイズ"
            self._commit_edit(self._edit_snapshot_before, description)
        self._edit_snapshot_before = None
        self.edit_mode = None
        self._resizing_note = None
        self.drag_start_pos = None
        self.update()
        super().mouseReleaseEvent(event)  # または元のコードをそのままコピー

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        menu = QMenu(self)
        selected_notes = [n for n in self.notes_list if getattr(n, 'is_selected', False)]

        if selected_notes:
            act_clear = QAction(f"選択したノートの {self.current_param_layer} をリセット", self)
            act_clear.triggered.connect(self._clear_selected_params)
            menu.addAction(act_clear)

            act_reset = QAction("歌詞を 'la' にリセット", self)
            act_reset.triggered.connect(self._reset_selected_lyrics)
            menu.addAction(act_reset)

            act_ghost = QAction(
                "AIゴーストを非表示" if self.show_ai_phonemes else "AIゴーストを表示", self)
            act_ghost.triggered.connect(self._toggle_ai_ghost)
            menu.addAction(act_ghost)

            menu.addSeparator()

        act_export = QAction("JSONエクスポート (Ctrl+S)", self)
        act_export.triggered.connect(self.export_all_data)
        menu.addAction(act_export)

        menu.exec(event.globalPos())

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event is None:
            return
        pos_f = event.position()
        for n in self.notes_list:
            if self.get_note_rect(n).contains(pos_f):
                before_snapshot = self._snapshot_notes()
                text, ok = QInputDialog.getText(
                    self, "歌詞入力", "ノートの歌詞:",
                    QLineEdit.EchoMode.Normal, n.lyrics)
                if ok and text:
                    n.lyrics = text
                    n.phoneme = self.analyze_lyric_to_phoneme(text)
                    chars = [str(getattr(t, 'surface', ''))
                             for t in self.tokenizer.tokenize(text)]
                    if len(chars) > 1:
                        self._split_note(n, chars)
                    self._invalidate_note_rects()  # [OPT-3]
                    self.notes_changed_signal.emit()
                    self._commit_edit(before_snapshot, "歌詞編集")
                    self.update()
                return
        # 既存ノート外をダブルクリックした場合は新規ノートを配置
        before_snapshot = self._snapshot_notes()
        beat_pos = (pos_f.x() + self.scroll_x_offset) / self.pixels_per_beat
        start_beat = max(0.0, self.quantize(float(beat_pos)))
        note_number = 127 - int((pos_f.y() + self.scroll_y_offset) / self.key_height_pixels)
        note_number = max(0, min(127, note_number))
        duration_beat = max(self.quantize_resolution, self.quantize_resolution)

        new_note = NoteEventClass(
            note_number=note_number,
            start_time=self.beats_to_seconds(start_beat),
            duration=self.beats_to_seconds(duration_beat),
            lyric="la",
        )
        new_note.is_selected = False
        new_note.phoneme = self.analyze_lyric_to_phoneme("la")
        self.notes_list.append(new_note)
        self._invalidate_note_rects()
        self.notes_changed_signal.emit()
        self._commit_edit(before_snapshot, "ノート追加")
        self.update()

    # ============================================================
    # キーボード操作
    # ============================================================

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event is None:
            return

        modifiers = event.modifiers()
        ctrl = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
        key_int = event.key()

        layer_map = {
            Qt.Key.Key_1.value: "Pitch",      # 
            Qt.Key.Key_2.value: "Gender",     # 
            Qt.Key.Key_3.value: "Tension",    # 
            Qt.Key.Key_4.value: "Breath",     #
        }

        main_window = self.window()
        status_bar = getattr(main_window, "statusBar", lambda: None)()

        if key_int in layer_map:
            target_layer = layer_map[key_int]
            self.change_layer(target_layer)
            if status_bar:
                status_bar.showMessage(f"Layer switched to: {target_layer}", 2000)

        elif ctrl:
            if key_int == Qt.Key.Key_S.value:
                self.export_all_data()
            elif key_int == Qt.Key.Key_D.value:
                self._duplicate_notes()
            elif key_int == Qt.Key.Key_A.value:
                self.select_all()
            # Ctrl+C / Ctrl+V は MainWindow.copy_action / paste_action
            # (QAction、ファイル/編集メニューにも表示される) と同じ
            # キーシーケンスを持つ。Qt はフォーカスウィジェットの
            # keyPressEvent より先にアプリ全体のショートカットマップを
            # 解決するため、ここに同じ処理を書いても実際には呼ばれず、
            # 死んだコードとして混乱の元になるだけだった。MainWindow側に一本化する。

        # Delete / Backspace は MainWindow.setup_vose_keyboard_navigation の
        # QShortcut (self.delete_selected_note -> timeline_widget.delete_selected())
        # に一本化した。ここに同じ処理を残すと、上記と同じ理由で
        # 常に MainWindow 側の QShortcut に処理を奪われる死んだコードになるため削除した。

        self.update()

    # ============================================================
    # 編集ロジック
    # ============================================================

    def change_layer(self, name: str) -> None:
        self.current_param_layer = name
        if self._param_source is not None and hasattr(self._param_source, "set_mode"):
            self._param_source.set_mode(name)   # GraphEditorWidget側も追従させる
        main_win = self.window()
        if isinstance(main_win, QMainWindow):
            sb = main_win.statusBar()
            if sb:
                sb.showMessage(f"Active Layer: {name}", 2000)
        self.update()
        logger.info(f"Graph Editor: Layer changed to '{name}'")

    def _toggle_ai_ghost(self) -> None:
        self.show_ai_phonemes = not self.show_ai_phonemes
        self.update()

    def _add_param_pt(self, pos: QPointF) -> None:
        t = self.beats_to_seconds((pos.x() + self.scroll_x_offset) / self.pixels_per_beat)
        val = max(0.0, min(1.0, (self.height() - 20 - pos.y()) / (self.height() * 0.4)))
        self.parameters[self.current_param_layer][float(t)] = float(val)
        self.update()

    def _smooth_param(self) -> None:
        layer = self.current_param_layer
        data = self.parameters[layer]
        if len(data) < 3:
            return
        keys = sorted(data.keys())
        smoothed = {}
        for i, t in enumerate(keys):
            window = [data[keys[j]] for j in range(max(0, i - 1), min(len(keys), i + 2))]
            smoothed[t] = sum(window) / len(window)
        self.parameters[layer] = smoothed
        self.notes_changed_signal.emit()

    def _clear_selected_params(self) -> None:
        """選択中のノートの時間範囲にある、現在のレイヤーのパラメーターポイントを削除する。
        GraphEditorWidgetの実データ(all_parameters)を操作するように変更。"""
        source = self._param_source
        if source is None or not hasattr(source, "all_parameters"):
            return

        layer = self.current_param_layer
        events = source.all_parameters.get(layer)
        if not events:
            return

        selected_ranges = [
            (n.start_time, n.start_time + n.duration)
            for n in self.notes_list if getattr(n, 'is_selected', False)
        ]
        if not selected_ranges:
            return

        before = source._snapshot_parameters() if hasattr(source, "_snapshot_parameters") else None
        events[:] = [
            ev for ev in events
            if not any(start <= getattr(ev, 'time', 0.0) <= end for start, end in selected_ranges)
        ]

        if hasattr(source, "parameters_changed"):
            source.parameters_changed.emit(source.all_parameters)
        if before is not None and hasattr(source, "_commit_edit"):
            source._commit_edit(before, f"{layer} パラメーターリセット")

        source.update()
        self.update()

    def _reset_selected_lyrics(self) -> None:
        for n in self.notes_list:
            if getattr(n, 'is_selected', False):
                n.lyrics = "la"
                n.phoneme = "la"
        self.update()

    def _split_note(self, n: Any, chars: List[str]) -> None:
        if n not in self.notes_list:
            return
        single_dur = n.duration / len(chars)
        start_t, pitch = n.start_time, n.note_number
        self.notes_list.remove(n)
        for i, char in enumerate(chars):
            new_n = NoteEventClass(start_t + i * single_dur, single_dur, pitch, char)
            new_n.phoneme = self.analyze_lyric_to_phoneme(char)
            self.notes_list.append(new_n)
        self._invalidate_note_rects()  # [OPT-3]

    def _copy_notes(self) -> None:
        sel = [n for n in self.notes_list if getattr(n, 'is_selected', False)]
        if not sel:
            return
        base_t = min(n.start_time for n in sel)
        payload = [{"l": n.lyrics, "n": n.note_number,
                    "o": n.start_time - base_t, "d": n.duration} for n in sel]
        QApplication.clipboard().setText(json.dumps(payload))

    def _paste_notes(self) -> None:
        try:
            data = json.loads(QApplication.clipboard().text())
            before_snapshot = self._snapshot_notes()
            self.deselect_all()
            for d in data:
                nn = NoteEventClass(
                    self._current_playback_time + d["o"], d["d"], d["n"], d["l"])
                nn.is_selected = True
                nn.phoneme = self.analyze_lyric_to_phoneme(d["l"])
                self.notes_list.append(nn)
            self._invalidate_note_rects()  # [OPT-3]
            self.notes_changed_signal.emit()
            self._commit_edit(before_snapshot, "ペースト")
            self.update()
        except Exception:
            pass

    def _duplicate_notes(self) -> None:
        sel = [n for n in self.notes_list if getattr(n, 'is_selected', False)]
        if not sel:
            return
        before_snapshot = self._snapshot_notes()
        offset = (max(n.start_time + n.duration for n in sel)
                  - min(n.start_time for n in sel))
        self.deselect_all()
        for n in sel:
            clone = NoteEventClass(n.start_time + offset, n.duration,
                                   n.note_number, n.lyrics)
            clone.is_selected = True
            clone.phoneme = n.phoneme
            self.notes_list.append(clone)
        self._invalidate_note_rects()  # [OPT-3]
        self.notes_changed_signal.emit()
        self._commit_edit(before_snapshot, "ノート複製")
        self.update()

    def select_all(self) -> None:
        for n in self.notes_list:
            n.is_selected = True
        self.update()

    def deselect_all(self) -> None:
        for n in self.notes_list:
            n.is_selected = False
        self.update()

    def select_next_note_in_time(self) -> None:
        """
        start_time順で「現在選択中のノートの次」を単一選択する。

        MainWindow.select_next_note (Alt+Right) から呼ばれる。
        以前は self.notes という別管理のリストを操作していたが、
        実際に画面に表示されるデータと同期していなかったため、
        timeline_widget.notes_list を直接参照する形に統一した。
        """
        if not self.notes_list:
            return
        ordered = sorted(self.notes_list, key=lambda n: n.start_time)
        selected_idx = next(
            (i for i, n in enumerate(ordered) if getattr(n, "is_selected", False)),
            None,
        )
        if selected_idx is None:
            next_idx = 0
        elif selected_idx < len(ordered) - 1:
            next_idx = selected_idx + 1
        else:
            return  # 既に末尾のノートを選択中
        self.deselect_all()
        ordered[next_idx].is_selected = True
        self.update()

    def select_prev_note_in_time(self) -> None:
        """start_time順で「現在選択中のノートの前」を単一選択する。MainWindow.select_prev_note (Alt+Left) から呼ばれる。"""
        if not self.notes_list:
            return
        ordered = sorted(self.notes_list, key=lambda n: n.start_time)
        selected_idx = next(
            (i for i, n in enumerate(ordered) if getattr(n, "is_selected", False)),
            None,
        )
        if selected_idx is None or selected_idx == 0:
            return
        self.deselect_all()
        ordered[selected_idx - 1].is_selected = True
        self.update()

    def delete_selected(self) -> None:
        before_snapshot = self._snapshot_notes()
        self.notes_list = [n for n in self.notes_list
                           if not getattr(n, 'is_selected', False)]
        self._invalidate_note_rects()  # [OPT-3]
        self.notes_changed_signal.emit()
        self._commit_edit(before_snapshot, "ノート削除")
        self.update()

    # ============================================================
    # 外部スロット
    # ============================================================

    @Slot(float)
    def update_audio_level(self, level: float) -> None:
        self.audio_level = level
        self.update()

    @Slot(float)
    def set_playback_time(self, t: float) -> None:
        """[UX: 官能的スクロール] 再生ヘッドに合わせて背景を滑らかに動かす"""
        self._current_playback_time = t
        
        # 追従設定がONの場合のみ実行
        if getattr(self, "auto_scroll_enabled", True):
            # 現在の再生位置(px)
            playback_px = self.time_to_x(t) + self.scroll_x_offset
            vw = self.width()
            
            # 再生ヘッドが画面の70%を越えたらスクロール開始
            threshold = vw * 0.7
            if playback_px > self.scroll_x_offset + threshold:
                # ターゲットのオフセット（ヘッドを画面の30%位置に保つ）
                target_offset = playback_px - (vw * 0.3)
                
                # 直接代入せず、少しずつ近づけることで「ヌルヌル」感を出す（イージング）
                # 0.1 は追従の速さ。数値を上げるとキビキビ動きます
                diff = target_offset - self.scroll_x_offset
                self.scroll_x_offset += diff * 0.1
                
                self._invalidate_grid() # グリッド更新
        
        self.update()

    @Slot(int)
    def set_vertical_offset(self, val: int) -> None:
        self.scroll_y_offset = float(val)
        self._invalidate_grid()        # [OPT-1]
        self.update()

    @Slot(int)
    def set_horizontal_offset(self, val: int) -> None:
        self.scroll_x_offset = float(val)
        self._invalidate_grid()        # [OPT-1]
        self._invalidate_note_rects()  # [OPT-3]
        self.update()
