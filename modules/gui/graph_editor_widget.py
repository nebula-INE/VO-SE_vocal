# graph_editor_widget.py

import copy
import bisect
import logging
from typing import Optional, List, Dict, Any

from PySide6.QtCore import Qt, Signal, Slot, QRect, QPointF
from PySide6.QtGui import QPainter, QColor, QBrush, QPen, QPaintEvent, QMouseEvent, QPainterPath
from PySide6.QtWidgets import QWidget

from modules.data.data_models import PitchEvent

logger = logging.getLogger(__name__)


class GraphEditorWidget(QWidget):
    parameters_changed = Signal(dict)
    edit_committed_signal = Signal(object, object, str)

    PITCH_MAX = 8191
    PITCH_MIN = -8192

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setMouseTracking(True)

        self._edit_snapshot_before: Optional[Dict[str, Any]] = None

        self.scroll_x_offset = 0.0
        self.pixels_per_beat = 40.0
        self.tempo = 120.0

        self.all_parameters: Dict[str, List[PitchEvent]] = {
            "Pitch": [],
            "Gender": [],
            "Tension": [],
            "Breath": []
        }

        self.current_mode = "Pitch"
        self.colors = {
            "Pitch": QColor(0, 255, 127),
            "Gender": QColor(231, 76, 60),
            "Tension": QColor(46, 204, 113),
            "Breath": QColor(241, 196, 15)
        }

        self.editing_point_index: Optional[int] = None
        self.hover_point_index: Optional[int] = None

        self.pen_mode: bool = False
        self.pen_interval: int = 6
        self._last_pen_pos: Optional[QPointF] = None

        logger.info("GraphEditorWidget initialized successfully.")

    # =========================================================================
    # スナップショット & Undo/Redo コミット
    # =========================================================================
    def _snapshot_parameters(self) -> Dict[str, Any]:
        return copy.deepcopy(self.all_parameters)

    def _restore_parameters_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Undo/Redo 用：パラメータスナップショットを復元（ディープコピーで安全に）"""
        import copy
        self.all_parameters = copy.deepcopy(snapshot)
        self.parameters_changed.emit(self.all_parameters)
        self.update()

    def _commit_edit(self, before_snapshot: Optional[Dict[str, Any]], description: str) -> None:
        if before_snapshot is None:
            return
        after = self._snapshot_parameters()
        if before_snapshot != after:
            self.edit_committed_signal.emit(before_snapshot, after, description)

    # =========================================================================
    # 互換性メソッド
    # =========================================================================
    @Slot(bool)
    def set_pen_mode(self, enabled: bool) -> None:
        self.pen_mode = enabled
        self.setCursor(Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor)

    def set_horizontal_offset(self, offset: int) -> None:
        try:
            self.scroll_x_offset = float(offset)
            self.update()
        except Exception:
            pass

    def set_vertical_offset(self, offset: int) -> None:
        _ = offset

    def sync_with_notes(self, notes: Optional[list] = None) -> None:
        _ = notes
        self.update()

    def get_value_at_time(self, events: list, t: float) -> float:
        if not events:
            return 0.0
        try:
            last_val = float(getattr(events[0], "value", 0.0))
            for ev in events:
                ev_t = float(getattr(ev, "time", 0.0))
                ev_v = float(getattr(ev, "value", last_val))
                if ev_t > t:
                    break
                last_val = ev_v
            return last_val
        except Exception:
            return 0.0

    @Slot(str)
    def set_mode(self, mode: str):
        if mode in self.all_parameters:
            self.current_mode = mode
            self.editing_point_index = None
            self.update()

    # =========================================================================
    # 座標変換
    # =========================================================================
    def time_to_x(self, seconds: float) -> float:
        beats = (seconds * self.tempo) / 60.0
        return float((beats * self.pixels_per_beat) - self.scroll_x_offset)

    def x_to_time(self, x: float) -> float:
        absolute_x = x + self.scroll_x_offset
        beats = absolute_x / self.pixels_per_beat
        return float((beats * 60.0) / self.tempo)

    def value_to_y(self, value: float) -> float:
        h = float(self.height())
        if self.current_mode == "Pitch":
            center_y = h / 2.0
            range_y = center_y * 0.8
            return center_y - (value / self.PITCH_MAX) * range_y
        else:
            return h - (value * (h * 0.8) + (h * 0.1))

    def y_to_value(self, y: float) -> float:
        h = float(self.height())
        if self.current_mode == "Pitch":
            center_y = h / 2.0
            range_y = center_y * 0.8
            val = -((y - center_y) / range_y) * self.PITCH_MAX
            return float(max(self.PITCH_MIN, min(self.PITCH_MAX, val)))
        else:
            val = (h - y - (h * 0.1)) / (h * 0.8)
            return float(max(0.0, min(1.0, val)))

    def value_to_y_for_mode(self, value: float, mode: str) -> float:
        h = float(self.height())
        if mode == "Pitch":
            center_y = h / 2.0
            return center_y - (value / self.PITCH_MAX) * (center_y * 0.8)
        else:
            return h - (value * (h * 0.8) + (h * 0.1))

    def _get_point_at_pos(self, pos: QPointF, events: list) -> Optional[int]:
        if not events:
            return None
        click_time = self.x_to_time(pos.x())
        idx = bisect.bisect_left(events, click_time, key=lambda e: e.time)
        start_idx = max(0, idx - 2)
        end_idx = min(len(events), idx + 2)
        for i in range(start_idx, end_idx):
            p = events[i]
            px = self.time_to_x(p.time)
            py = self.value_to_y(p.value)
            if QRect(int(px) - 8, int(py) - 8, 16, 16).contains(pos.toPoint()):
                return i
        return None

    def _add_pen_point(self, time_val: float, param_val: float) -> None:
        current_list = self.all_parameters.get(self.current_mode)
        if current_list is None:
            return
        for p in current_list:
            if abs(p.time - time_val) < 0.001:
                p.value = param_val
                return
        current_list.append(PitchEvent(time=time_val, value=param_val))

    # =========================================================================
    # マウスイベント (完全版)
    # =========================================================================
    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        events = self.all_parameters[self.current_mode]

        # ペンモード
        if self.pen_mode and event.button() == Qt.MouseButton.LeftButton:
            self._edit_snapshot_before = self._snapshot_parameters()
            time_val = float(self.x_to_time(pos.x()))
            param_val = float(self.y_to_value(pos.y()))
            self._add_pen_point(time_val, param_val)
            self._last_pen_pos = pos
            self.update()
            return

        # 左クリック: ポイントを掴んだ時だけスナップショットを取る
        if event.button() == Qt.MouseButton.LeftButton:
            idx = self._get_point_at_pos(pos, events)
            if idx is not None:
                self._edit_snapshot_before = self._snapshot_parameters()
                self.editing_point_index = idx
            else:
                # クリックが外れた場合はスナップショットをクリア
                self._edit_snapshot_before = None
                self.editing_point_index = None
            self.update()
            return

        # 右クリック: ポイント削除
        if event.button() == Qt.MouseButton.RightButton:
            before = self._snapshot_parameters()
            target_idx = self._get_point_at_pos(pos, events)
            if target_idx is not None:
                events.pop(target_idx)
                self.parameters_changed.emit(self.all_parameters)
                self._commit_edit(before, "パラメータポイント削除")
                self.update()
            return

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()

        # ペンモードドラッグ中
        if self.pen_mode and (event.buttons() & Qt.MouseButton.LeftButton):
            if self._last_pen_pos is not None:
                if (pos - self._last_pen_pos).manhattanLength() >= self.pen_interval:
                    time_val = float(self.x_to_time(pos.x()))
                    param_val = float(self.y_to_value(pos.y()))
                    self._add_pen_point(time_val, param_val)
                    self._last_pen_pos = pos
                    self.update()
            return

        # 通常ドラッグ (ポイント移動)
        events = self.all_parameters[self.current_mode]
        if event.buttons() & Qt.MouseButton.LeftButton and self.editing_point_index is not None:
            p = events[self.editing_point_index]
            p.time = max(0.0, self.x_to_time(pos.x()))
            p.value = self.y_to_value(pos.y())
            self.parameters_changed.emit(self.all_parameters)
            self.update()
            return

        # ホバー判定 (ドラッグ中でない時のみ)
        self.hover_point_index = self._get_point_at_pos(pos, events)
        self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        # ペンモード終了
        if self.pen_mode and event.button() == Qt.MouseButton.LeftButton:
            self._last_pen_pos = None
            self.all_parameters[self.current_mode].sort(key=lambda x: x.time)
            self.parameters_changed.emit(self.all_parameters)
            self._commit_edit(self._edit_snapshot_before, "ペン描画")
            self._edit_snapshot_before = None
            self.update()
            return

        # ポイント移動終了
        if self.editing_point_index is not None:
            self.all_parameters[self.current_mode].sort(key=lambda x: x.time)
            self.parameters_changed.emit(self.all_parameters)
            self._commit_edit(self._edit_snapshot_before, "パラメータポイント移動")
            self._edit_snapshot_before = None
            self.editing_point_index = None
            self.update()
            return

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            before = self._snapshot_parameters()
            pos = event.position()
            time_val = float(self.x_to_time(pos.x()))
            param_val = float(self.y_to_value(pos.y()))
            new_point = PitchEvent(time=time_val, value=param_val)

            current_list = self.all_parameters.get(self.current_mode)
            if current_list is not None:
                current_list[:] = [p for p in current_list if abs(p.time - time_val) > 0.001]
                current_list.append(new_point)
                current_list.sort(key=lambda x: x.time)
                self.parameters_changed.emit(self.all_parameters)
                self._commit_edit(before, "パラメータポイント追加")
                self.update()
                logger.debug(f"Point added at t={time_val:.3f}, v={param_val:.3f}")

    # =========================================================================
    # 描画
    # =========================================================================
    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        painter.fillRect(self.rect(), QColor(30, 30, 30))

        h = float(self.height())
        w = float(self.width())

        if self.current_mode == "Pitch":
            painter.setPen(QPen(QColor(60, 60, 60), 1, Qt.PenStyle.DashLine))
            painter.drawLine(0, int(h / 2), int(w), int(h / 2))

        for mode, events in self.all_parameters.items():
            if mode == self.current_mode or not events:
                continue
            color = QColor(self.colors[mode])
            color.setAlpha(30)
            painter.setPen(QPen(color, 1))
            path = QPainterPath()
            first_p = events[0]
            path.moveTo(self.time_to_x(first_p.time), self.value_to_y_for_mode(first_p.value, mode))
            for p in events[1:]:
                path.lineTo(self.time_to_x(p.time), self.value_to_y_for_mode(p.value, mode))
            painter.drawPath(path)

        events = self.all_parameters[self.current_mode]
        color = self.colors[self.current_mode]
        if len(events) >= 2:
            painter.setPen(QPen(color, 2))
            path = QPainterPath()
            first_p = events[0]
            path.moveTo(self.time_to_x(first_p.time), self.value_to_y(first_p.value))
            for p in events[1:]:
                path.lineTo(self.time_to_x(p.time), self.value_to_y(p.value))
            painter.drawPath(path)

        for i, p in enumerate(events):
            px = self.time_to_x(p.time)
            py = self.value_to_y(p.value)
            if i == self.hover_point_index:
                painter.setBrush(QBrush(QColor(255, 255, 255)))
                painter.setPen(QPen(color, 2))
                radius = 6
            else:
                painter.setBrush(QBrush(color))
                painter.setPen(Qt.PenStyle.NoPen)
                radius = 4
            painter.drawEllipse(QPointF(px, py), radius, radius)
