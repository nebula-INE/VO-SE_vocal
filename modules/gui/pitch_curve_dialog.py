"""Desktop editor for UTAU Mode 2 pitch-bend fields (PBS/PBW/PBY/PBM)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
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


class PitchCurvePreview(QWidget):
    """Read-only curve preview using the same UST parser/interpolator as rendering."""

    def __init__(self, duration_ms: float, parent=None) -> None:
        super().__init__(parent)
        self.duration_ms = max(1.0, float(duration_ms))
        self.fields = ("", "", "", "")
        self.error = ""
        self.setMinimumHeight(180)

    def set_fields(self, fields: tuple[str, str, str, str]) -> None:
        self.fields = fields
        try:
            UstConverter._build_ust_control_points(*fields, self.duration_ms)
            self.error = ""
        except (TypeError, ValueError, OverflowError) as exc:
            self.error = str(exc)
        self.update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#18181a"))
        area = self.rect().adjusted(42, 12, -14, -28)
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
            points, shapes, _offset = UstConverter._build_ust_control_points(
                *self.fields, self.duration_ms
            )
            if len(points) < 2:
                painter.setPen(QColor("#9a9aa2"))
                painter.drawText(area, Qt.AlignmentFlag.AlignCenter, "ピッチベンドなし")
                return
            min_x = min(0.0, min(point[0] for point in points))
            max_x = max(self.duration_ms, max(point[0] for point in points), min_x + 1.0)
            max_abs_y = max(1.0, *(abs(point[1]) for point in points))
            y_limit = max(2.0, max_abs_y * 1.25)

            def map_x(value: float) -> float:
                return area.left() + (value - min_x) / (max_x - min_x) * area.width()

            def map_y(value: float) -> float:
                return area.center().y() - value / y_limit * (area.height() * 0.46)

            painter.setPen(QPen(QColor("#0a84ff"), 2))
            previous = None
            for index in range(161):
                time_ms = min_x + (max_x - min_x) * index / 160
                value = _interp_shape(time_ms, points, shapes)
                current = (map_x(time_ms), map_y(value))
                if previous is not None:
                    from PySide6.QtCore import QPointF
                    painter.drawLine(QPointF(*previous), QPointF(*current))
                previous = current
            painter.setBrush(QColor("#64d2ff"))
            painter.setPen(QPen(QColor("#64d2ff"), 1))
            for time_ms, value in points:
                painter.drawEllipse(
                    __import__("PySide6.QtCore", fromlist=["QPointF"]).QPointF(
                        map_x(time_ms), map_y(value)
                    ),
                    3.5,
                    3.5,
                )
        except (TypeError, ValueError, OverflowError) as exc:
            painter.setPen(QColor("#ff6b6b"))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter, str(exc))


class PitchCurveEditorDialog(QDialog):
    """Edit the UST-compatible portamento fields while previewing the curve."""

    def __init__(self, note: object, tempo: float, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ノートのピッチカーブ")
        self.setMinimumWidth(540)
        duration = max(0.001, float(getattr(note, "duration", 0.5)))
        duration_ms = duration * 1000.0
        self._fields = {
            "PBS": QLineEdit(self),
            "PBW": QLineEdit(self),
            "PBY": QLineEdit(self),
            "PBM": QLineEdit(self),
        }
        initial = note_pitch_fields(note)
        self._preview = PitchCurvePreview(duration_ms, self)
        self._error_label = QLabel(
            "UST Mode 2のPBS/PBW/PBY/PBMを編集します。数値単位はUTAU仕様です。",
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
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText("適用")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._refresh_preview()

    def _current_fields(self) -> tuple[str, str, str, str]:
        return tuple(edit.text().strip() for edit in self._fields.values())  # type: ignore[return-value]

    def _refresh_preview(self) -> None:
        fields = self._current_fields()
        self._preview.set_fields(fields)
        apply_button = self.findChild(QDialogButtonBox).button(
            QDialogButtonBox.StandardButton.Apply
        )
        valid = not self._preview.error
        apply_button.setEnabled(valid)
        if not valid:
            self._error_label.setText(f"入力値を確認してください: {self._preview.error}")
        else:
            self._error_label.setText(
                "曲線プレビュー。PBSは開始位置/開始音程、PBWは区間幅、"
                "PBYは区間の音程、PBMは補間形状です。"
            )

    def apply_to_note(self, note: object) -> None:
        for key, value in zip(
            ("_ust_pbs", "_ust_pbw", "_ust_pby", "_ust_pbm"),
            self._current_fields(),
        ):
            setattr(note, key, value)
