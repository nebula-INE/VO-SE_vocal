"""Desktop visual editor for UTAU Mode 2 pitch-bend fields (PBS/PBW/PBY/PBM)."""

from __future__ import annotations

from collections.abc import Callable
import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from modules.data.ust_parser import UstConverter, _interp_shape


def note_pitch_fields(note: object) -> tuple[str, str, str, str]:
    """Read the canonical UST extension attributes from a desktop note."""
    return tuple(
        str(getattr(note, key, "") or "")
        for key in ("_ust_pbs", "_ust_pbw", "_ust_pby", "_ust_pbm")
    )  # type: ignore[return-value]


def serialize_pitch_points(
    points: list[tuple[float, float]],
    shapes: list[str] | None = None,
) -> tuple[str, str, str, str]:
    """Serialize time-ms/semitone control points back to UTAU Mode 2 units."""
    if not points:
        return "", "", "", ""
    ordered = sorted((float(x), float(y)) for x, y in points)
    pbs = f"{round(ordered[0][0])};{round(ordered[0][1] * 10)}"
    widths: list[str] = []
    heights: list[str] = []
    for previous, current in zip(ordered, ordered[1:]):
        widths.append(str(max(1, round(current[0] - previous[0]))))
        heights.append(str(round(current[1] * 10)))
    shape_values = list(shapes or [])
    if len(shape_values) < len(widths):
        shape_values.extend(["s"] * (len(widths) - len(shape_values)))
    return pbs, ",".join(widths), ",".join(heights), ",".join(shape_values[: len(widths)])


class PitchCurvePreview(QWidget):
    """Editable control-point curve preview using the same interpolation as rendering."""

    def __init__(self, duration_ms: float, parent=None) -> None:
        super().__init__(parent)
        self.duration_ms = max(1.0, float(duration_ms))
        self.fields = ("", "", "", "")
        self.error = ""
        self.explicit_points: list[tuple[float, float]] = [(0.0, 0.0)]
        self.shapes: list[str] = []
        self.dragging_index: int | None = None
        self.on_fields_changed: Callable[[tuple[str, str, str, str]], None] | None = None
        self.setMinimumHeight(190)
        self.setMouseTracking(True)
        self.setToolTip(
            "点をドラッグして移動、ダブルクリックで点を追加、右クリックで点を削除できます"
        )

    def set_fields(self, fields: tuple[str, str, str, str]) -> None:
        self.fields = fields
        try:
            pbs_parts = [part.strip() for part in fields[0].split(";")]
            if len(pbs_parts) > 2 or any(
                part and not math.isfinite(float(part)) for part in pbs_parts
            ):
                raise ValueError("PBSは有限の数値を最大2個指定してください")
            widths = [value.strip() for value in fields[1].split(",") if value.strip()]
            if any(not math.isfinite(float(value)) or float(value) <= 0 for value in widths):
                raise ValueError("PBWの区間幅は0より大きい有限値にしてください")
            heights = [value.strip() for value in fields[2].split(",") if value.strip()]
            if any(not math.isfinite(float(value)) for value in heights):
                raise ValueError("PBYは有限の数値を指定してください")
            curve_points, _curve_shapes, _offset = UstConverter._build_ust_control_points(
                *fields, self.duration_ms
            )
            widths = [value for value in fields[1].split(",") if value.strip()]
            explicit_count = len(widths) + 1
            self.explicit_points = list(curve_points[:explicit_count]) or [(0.0, 0.0)]
            self.shapes = [
                value.strip().lower() for value in fields[3].split(",") if value.strip()
            ]
            if len(self.shapes) < len(widths):
                self.shapes.extend(["s"] * (len(widths) - len(self.shapes)))
            self.error = ""
        except (TypeError, ValueError, OverflowError) as exc:
            self.error = str(exc)
        self.update()

    def _curve_points(self) -> tuple[list[tuple[float, float]], list[str]]:
        points, shapes, _offset = UstConverter._build_ust_control_points(
            *self.fields, self.duration_ms
        )
        return points, shapes

    def _x_limits(self) -> tuple[float, float]:
        xs = [point[0] for point in self.explicit_points]
        return min(-40.0, min(xs)), max(self.duration_ms, max(xs), 1.0)

    def _y_limit(self) -> float:
        return max(2.0, max((abs(point[1]) for point in self.explicit_points), default=0.0) * 1.25)

    def _graph_area(self):
        return self.rect().adjusted(42, 12, -14, -30)

    def _to_screen(self, time_ms: float, semitones: float) -> QPointF:
        area = self._graph_area()
        min_x, max_x = self._x_limits()
        y_limit = self._y_limit()
        x = area.left() + (time_ms - min_x) / max(1.0, max_x - min_x) * area.width()
        y = area.center().y() - semitones / y_limit * (area.height() * 0.46)
        return QPointF(x, y)

    def _from_screen(self, position: QPointF) -> tuple[float, float]:
        area = self._graph_area()
        min_x, max_x = self._x_limits()
        y_limit = self._y_limit()
        time_ms = min_x + (position.x() - area.left()) / max(1, area.width()) * (max_x - min_x)
        semitones = (
            (area.center().y() - position.y()) / max(1.0, area.height() * 0.46) * y_limit
        )
        return time_ms, max(-24.0, min(24.0, semitones))

    def _publish_points(self) -> None:
        serialized = serialize_pitch_points(self.explicit_points, self.shapes)
        if self.on_fields_changed is not None:
            self.on_fields_changed(serialized)

    def _nearest_point(self, position: QPointF) -> int | None:
        nearest_index = None
        nearest_distance = 13.0
        for index, (time_ms, semitones) in enumerate(self.explicit_points):
            point = self._to_screen(time_ms, semitones)
            distance = ((point.x() - position.x()) ** 2 + (point.y() - position.y()) ** 2) ** 0.5
            if distance < nearest_distance:
                nearest_index = index
                nearest_distance = distance
        return nearest_index

    def mousePressEvent(self, event: QMouseEvent) -> None:
        point_index = self._nearest_point(event.position())
        if event.button() == Qt.MouseButton.RightButton:
            if point_index is not None and point_index > 0:
                self.explicit_points.pop(point_index)
                if point_index - 1 < len(self.shapes):
                    self.shapes.pop(point_index - 1)
                self._publish_points()
                self.update()
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and point_index is not None:
            self.dragging_index = point_index
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.dragging_index is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        index = self.dragging_index
        time_ms, semitones = self._from_screen(event.position())
        if index > 0:
            time_ms = max(self.explicit_points[index - 1][0] + 1.0, time_ms)
        if index + 1 < len(self.explicit_points):
            time_ms = min(self.explicit_points[index + 1][0] - 1.0, time_ms)
        self.explicit_points[index] = (time_ms, semitones)
        self._publish_points()
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.dragging_index = None
            self.unsetCursor()
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        time_ms, semitones = self._from_screen(event.position())
        if not self._graph_area().contains(event.position().toPoint()):
            return
        if any(abs(existing_time - time_ms) < 2.0 for existing_time, _ in self.explicit_points):
            return
        insertion_index = next(
            (i for i, (existing_time, _value) in enumerate(self.explicit_points)
             if existing_time > time_ms),
            len(self.explicit_points),
        )
        self.explicit_points.insert(insertion_index, (time_ms, semitones))
        self.shapes.insert(max(0, insertion_index - 1), "s")
        self._publish_points()
        self.update()
        event.accept()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#18181a"))
        area = self._graph_area()
        painter.setPen(QPen(QColor("#45454d"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(area.left(), area.center().y(), area.right(), area.center().y())
        painter.drawRect(area)
        painter.setPen(QColor("#a1a1aa"))
        painter.drawText(6, area.top() + 10, "半音")
        painter.drawText(area.left(), self.height() - 8, "0 ms")
        painter.drawText(
            area.right() - 55,
            self.height() - 8,
            f"{self.duration_ms:.0f} ms",
        )
        if self.error:
            painter.setPen(QColor("#ff6b6b"))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, self.error)
            return

        try:
            points, shapes = self._curve_points()
            min_x, max_x = self._x_limits()
            painter.setPen(QPen(QColor("#0a84ff"), 2))
            previous = None
            for index in range(161):
                time_ms = min_x + (max_x - min_x) * index / 160
                value = _interp_shape(time_ms, points, shapes)
                current = self._to_screen(time_ms, value)
                if previous is not None:
                    painter.drawLine(previous, current)
                previous = current

            for index, (time_ms, value) in enumerate(self.explicit_points):
                point = self._to_screen(time_ms, value)
                painter.setBrush(QColor("#64d2ff"))
                painter.setPen(QPen(QColor("#e5f7ff"), 1))
                painter.drawEllipse(point, 5 if index == self.dragging_index else 4, 5 if index == self.dragging_index else 4)
        except (TypeError, ValueError, OverflowError) as exc:
            painter.setPen(QColor("#ff6b6b"))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, str(exc))


class PitchCurveEditorDialog(QDialog):
    """Edit the UST-compatible portamento fields with a draggable curve editor."""

    def __init__(self, note: object, tempo: float, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ノートのピッチカーブ")
        self.setMinimumWidth(560)
        duration = max(0.001, float(getattr(note, "duration", 0.5)))
        self._fields = {
            "PBS": QLineEdit(self),
            "PBW": QLineEdit(self),
            "PBY": QLineEdit(self),
            "PBM": QLineEdit(self),
        }
        initial = note_pitch_fields(note)
        self._preview = PitchCurvePreview(duration * 1000.0, self)
        self._error_label = QLabel(
            "点をドラッグして移動、ダブルクリックで追加、右クリックで削除できます。"
            "PBS/PBW/PBY/PBMの数値はUTAU Mode 2単位です。",
            self,
        )
        self._error_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        for (name, edit), value in zip(self._fields.items(), initial):
            edit.setPlaceholderText(f"{name} (空欄可)")
            edit.setText(value)
            edit.textChanged.connect(self._refresh_preview)
            form.addRow(name, edit)
        layout.addLayout(form)
        layout.addWidget(self._preview)
        layout.addWidget(self._error_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply,
            parent=self,
        )
        self._apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        self._apply_button.setText("適用")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._preview.on_fields_changed = self._set_fields_from_canvas
        self._refresh_preview()

    def _current_fields(self) -> tuple[str, str, str, str]:
        return tuple(edit.text().strip() for edit in self._fields.values())  # type: ignore[return-value]

    def _set_fields_from_canvas(self, fields: tuple[str, str, str, str]) -> None:
        for edit, value in zip(self._fields.values(), fields):
            if edit.text() != value:
                edit.setText(value)

    def _refresh_preview(self) -> None:
        fields = self._current_fields()
        self._preview.set_fields(fields)
        valid = not self._preview.error
        self._apply_button.setEnabled(valid)
        if not valid:
            self._error_label.setText(f"入力値を確認してください: {self._preview.error}")
        else:
            self._error_label.setText(
                "曲線上の点をドラッグして移動し、ダブルクリックで点を追加、"
                "右クリックで2点目以降を削除できます。PBSは開始位置/開始音程、"
                "PBWは区間幅、PBYは区間音程、PBMは補間形状です。"
            )

    def apply_to_note(self, note: object) -> None:
        for key, value in zip(
            ("_ust_pbs", "_ust_pbw", "_ust_pby", "_ust_pbm"),
            self._current_fields(),
        ):
            setattr(note, key, value)
