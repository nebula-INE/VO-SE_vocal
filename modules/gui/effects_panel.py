"""Desktop master-bus effects controls with real parameter ranges and units."""
from __future__ import annotations

import copy
import math
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from modules.audio.master_effects import (
    EFFECT_DEFAULTS,
    EFFECT_RANGES,
    normalize_effect_chain,
)


EFFECT_LABELS = {
    "compressor": "コンプレッサー",
    "reverb": "リバーブ",
    "eq": "EQ",
}
PARAMETER_LABELS = {
    "threshold_db": ("Threshold", "dB"),
    "ratio": ("Ratio", ":1"),
    "attack_ms": ("Attack", "ms"),
    "release_ms": ("Release", "ms"),
    "makeup_gain_db": ("Makeup", "dB"),
    "room_size": ("Room size", ""),
    "damping": ("Damping", ""),
    "wet": ("Wet", "%"),
    "dry": ("Dry", "%"),
    "width": ("Width", "%"),
    "frequency_hz": ("Frequency", "Hz"),
    "gain_db": ("Gain", "dB"),
    "q": ("Q", ""),
}


class EffectsPanel(QWidget):
    """Manage a single post-track master-bus chain.

    The controls never alter individual vocal renders. The same serialized
    chain is consumed by the live AudioMixer and offline project mixdown.
    """

    effect_parameters_changed = Signal(str, dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.effects: list[dict[str, Any]] = []
        self.effect_widgets: dict[str, QWidget] = {}
        self._next_id = 1
        self._suppress_changes = False
        self.init_ui()

    def init_ui(self) -> None:
        layout = QVBoxLayout(self)
        scope_label = QLabel("マスターバス（全トラックの合成後に適用）")
        scope_label.setWordWrap(True)
        scope_label.setObjectName("EffectsScopeLabel")
        layout.addWidget(scope_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.container = QWidget()
        self.container.setStyleSheet("background: transparent;")
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.empty_label = QLabel("エフェクトはまだありません\n下のメニューから追加できます")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        self.empty_label.setStyleSheet("color: #6b6b73; padding: 32px 8px;")
        self.container_layout.addWidget(self.empty_label)
        scroll.setWidget(self.container)
        layout.addWidget(scroll)

        add_layout = QHBoxLayout()
        self.effect_selector = QComboBox()
        self.effect_selector.addItem(EFFECT_LABELS["compressor"], "compressor")
        self.effect_selector.addItem(EFFECT_LABELS["reverb"], "reverb")
        self.effect_selector.addItem(EFFECT_LABELS["eq"], "eq")
        self.add_btn = QPushButton("追加")
        self.add_btn.clicked.connect(self.on_add_effect)
        add_layout.addWidget(QLabel("エフェクト:"))
        add_layout.addWidget(self.effect_selector)
        add_layout.addWidget(self.add_btn)
        layout.addLayout(add_layout)

    def on_add_effect(self) -> None:
        effect_type = str(self.effect_selector.currentData() or "compressor")
        self.add_effect(effect_type)

    def add_effect(self, effect_type: str) -> None:
        effect_type = str(effect_type).strip().lower()
        if effect_type not in EFFECT_DEFAULTS:
            raise ValueError(f"未対応のエフェクトです: {effect_type}")
        while True:
            effect_id = f"{effect_type}_{self._next_id}"
            self._next_id += 1
            if effect_id not in self.effect_widgets and all(
                item["id"] != effect_id for item in self.effects
            ):
                break
        data = {
            "id": effect_id,
            "type": effect_type,
            "enabled": True,
            "params": dict(EFFECT_DEFAULTS[effect_type]),
        }
        self.effects.append(data)
        self.empty_label.hide()
        widget = self._create_effect_widget(data)
        self.container_layout.addWidget(widget)
        self.effect_widgets[effect_id] = widget
        self._emit_change(effect_id)

    def get_effect_chain(self) -> list[dict[str, Any]]:
        """Return a validated detached copy suitable for threading or JSON."""
        return normalize_effect_chain(copy.deepcopy(self.effects))

    def set_effect_chain(self, chain: Any) -> None:
        """Replace the chain when loading a project, without emitting UI changes."""
        normalized = normalize_effect_chain(chain)
        self._suppress_changes = True
        try:
            for widget in self.effect_widgets.values():
                self.container_layout.removeWidget(widget)
                widget.setParent(None)
                widget.deleteLater()
            self.effect_widgets.clear()
            self.effects = copy.deepcopy(normalized)
            self._next_id = max(self._next_id, len(self.effects) + 1)
            for data in self.effects:
                widget = self._create_effect_widget(data)
                self.container_layout.addWidget(widget)
                self.effect_widgets[data["id"]] = widget
            self.empty_label.setVisible(not self.effects)
        finally:
            self._suppress_changes = False

    def _create_effect_widget(self, data: dict[str, Any]) -> QWidget:
        effect_type = data["type"]
        group = QGroupBox(EFFECT_LABELS[effect_type])
        group.setCheckable(True)
        group.setChecked(bool(data["enabled"]))
        group.toggled.connect(
            lambda enabled, effect_id=data["id"]: self._on_enabled(effect_id, enabled)
        )

        grid = QGridLayout(group)
        for row, (name, value) in enumerate(data["params"].items()):
            label_text = PARAMETER_LABELS[name][0]
            label = QLabel(label_text)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 1000)
            slider.setValue(self._value_to_slider(effect_type, name, float(value)))
            value_label = QLabel(self._format_value(name, float(value)))
            slider.setToolTip(self._format_value(name, float(value)))
            slider.valueChanged.connect(
                lambda raw, effect_id=data["id"], parameter=name, out=value_label,
                       tip=slider: self._on_param(effect_id, parameter, raw, out, tip)
            )
            grid.addWidget(label, row, 0)
            grid.addWidget(slider, row, 1)
            grid.addWidget(value_label, row, 2)

        remove_button = QPushButton("削除")
        remove_button.clicked.connect(lambda _checked=False, eid=data["id"]: self._remove_effect(eid))
        grid.addWidget(remove_button, len(data["params"]), 0, 1, 3)
        return group

    @staticmethod
    def _value_to_slider(effect_type: str, name: str, value: float) -> int:
        minimum, maximum = EFFECT_RANGES[effect_type][name]
        if name == "frequency_hz":
            ratio = (
                (math.log(value) - math.log(minimum))
                / (math.log(maximum) - math.log(minimum))
            )
        else:
            ratio = (value - minimum) / (maximum - minimum)
        return int(round(min(1.0, max(0.0, ratio)) * 1000.0))

    @staticmethod
    def _slider_to_value(effect_type: str, name: str, raw: int) -> float:
        minimum, maximum = EFFECT_RANGES[effect_type][name]
        ratio = min(1.0, max(0.0, raw / 1000.0))
        if name == "frequency_hz":
            return float(math.exp(math.log(minimum) + ratio * (math.log(maximum) - math.log(minimum))))
        return float(minimum + ratio * (maximum - minimum))

    @staticmethod
    def _format_value(name: str, value: float) -> str:
        if name == "ratio":
            return f"{value:.1f}:1"
        if name in {"wet", "dry", "width", "room_size", "damping"}:
            return f"{value * 100.0:.0f}%" if name in {"wet", "dry", "width"} else f"{value:.2f}"
        if name == "frequency_hz":
            return f"{value:.0f} Hz"
        if name.endswith("_ms"):
            return f"{value:.1f} ms"
        if name.endswith("_db"):
            return f"{value:+.1f} dB"
        if name == "q":
            return f"{value:.2f}"
        return f"{value:.2f}"

    def _on_enabled(self, effect_id: str, enabled: bool) -> None:
        if self._suppress_changes:
            return
        for effect in self.effects:
            if effect["id"] == effect_id:
                effect["enabled"] = bool(enabled)
                self._emit_change(effect_id)
                return

    def _on_param(
        self,
        effect_id: str,
        name: str,
        raw: int,
        value_label: QLabel,
        slider: QSlider,
    ) -> None:
        if self._suppress_changes:
            return
        for effect in self.effects:
            if effect["id"] == effect_id:
                value = self._slider_to_value(effect["type"], name, raw)
                effect["params"][name] = value
                formatted = self._format_value(name, value)
                value_label.setText(formatted)
                slider.setToolTip(formatted)
                self._emit_change(effect_id)
                return

    def _remove_effect(self, effect_id: str) -> None:
        self.effects = [effect for effect in self.effects if effect["id"] != effect_id]
        widget = self.effect_widgets.pop(effect_id, None)
        if widget is not None:
            self.container_layout.removeWidget(widget)
            widget.setParent(None)
            widget.deleteLater()
        self.empty_label.setVisible(not self.effects)
        self._emit_change(effect_id)

    def _emit_change(self, effect_id: str) -> None:
        if self._suppress_changes:
            return
        params: dict[str, Any] = {}
        for effect in self.effects:
            if effect["id"] == effect_id:
                params = copy.deepcopy(effect["params"])
                break
        self.effect_parameters_changed.emit(effect_id, params)
