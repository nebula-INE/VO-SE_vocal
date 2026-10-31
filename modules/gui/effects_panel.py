# effects_panel.py

from typing import Dict, List, Any
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QSlider,
    QPushButton, QGroupBox, QScrollArea, QComboBox, QGridLayout, QFrame
)


class EffectsPanel(QWidget):
    """エフェクトチェーン管理パネル"""
    effect_parameters_changed = Signal(str, dict)  # effect_id, params

    def __init__(self, parent=None):
        super().__init__(parent)
        self.effects: List[Dict[str, Any]] = []          # エフェクトデータ
        self.effect_widgets: Dict[str, QWidget] = {}    # エフェクトID → ウィジェット
        self.init_ui()

    def init_ui(self):
        layout = QVBoxLayout(self)

        # スクロールエリア
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        scroll.setStyleSheet("QScrollArea { background: transparent; }")
        self.container = QWidget()
        self.container.setStyleSheet("background: transparent;")
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        # 空状態の案内（エフェクト追加で非表示）
        self.empty_label = QLabel("エフェクトはまだありません\n下のメニューから追加できます")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setStyleSheet("color: #6b6b73; padding: 32px 8px;")
        self.container_layout.addWidget(self.empty_label)
        scroll.setWidget(self.container)
        layout.addWidget(scroll)

        # 追加コントロール
        add_layout = QHBoxLayout()
        self.effect_selector = QComboBox()
        self.effect_selector.addItems(["コンプレッサー", "リバーブ", "EQ"])
        self.add_btn = QPushButton("追加")
        self.add_btn.clicked.connect(self.on_add_effect)
        add_layout.addWidget(QLabel("エフェクト:"))
        add_layout.addWidget(self.effect_selector)
        add_layout.addWidget(self.add_btn)
        layout.addLayout(add_layout)

    def on_add_effect(self):
        effect_type = self.effect_selector.currentText()
        self.add_effect(effect_type)

    def add_effect(self, effect_type: str):
        effect_id = f"{effect_type}_{len(self.effects)}"
        params = self._default_params(effect_type)
        data = {
            "id": effect_id,
            "type": effect_type,
            "enabled": True,
            "params": params,
        }
        self.effects.append(data)
        self.empty_label.hide()
        widget = self._create_effect_widget(data)
        self.container_layout.addWidget(widget)
        self.effect_widgets[effect_id] = widget

    def _default_params(self, effect_type: str) -> Dict[str, float]:
        defaults = {
            "コンプレッサー": {"threshold": 0.5, "ratio": 4.0, "attack": 0.01, "release": 0.1, "gain": 0.0},
            "リバーブ": {"room_size": 0.5, "damping": 0.5, "wet": 0.3, "dry": 0.7, "width": 0.5},
            "EQ": {"freq": 1000.0, "gain": 0.0, "q": 1.0},
        }
        return defaults.get(effect_type, {})

    def _create_effect_widget(self, data: Dict[str, Any]) -> QWidget:
        group = QGroupBox(data["type"])
        group.setCheckable(True)
        group.setChecked(data["enabled"])
        group.toggled.connect(lambda checked: self._on_enabled(data["id"], checked))

        layout = QGridLayout(group)
        row = 0
        for name, value in data["params"].items():
            label = QLabel(name)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 1000)
            slider.setValue(int(value * 1000))
            val_label = QLabel(f"{value:.2f}")
            slider.valueChanged.connect(
                lambda v, n=name, lbl=val_label: self._on_param(data["id"], n, v / 1000.0, lbl)
            )
            layout.addWidget(label, row, 0)
            layout.addWidget(slider, row, 1)
            layout.addWidget(val_label, row, 2)
            row += 1

        remove_btn = QPushButton("削除")
        remove_btn.clicked.connect(lambda: self._remove_effect(data["id"]))
        layout.addWidget(remove_btn, row, 0, 1, 3)
        return group

    def _on_enabled(self, effect_id: str, enabled: bool):
        for e in self.effects:
            if e["id"] == effect_id:
                e["enabled"] = enabled
                self.effect_parameters_changed.emit(effect_id, e["params"])
                break

    def _on_param(self, effect_id: str, name: str, value: float, label: QLabel):
        label.setText(f"{value:.2f}")
        for e in self.effects:
            if e["id"] == effect_id:
                e["params"][name] = value
                self.effect_parameters_changed.emit(effect_id, e["params"])
                break

    def _remove_effect(self, effect_id: str):
        self.effects = [e for e in self.effects if e["id"] != effect_id]
        widget = self.effect_widgets.pop(effect_id, None)
        if widget:
            widget.setParent(None)
            widget.deleteLater()
