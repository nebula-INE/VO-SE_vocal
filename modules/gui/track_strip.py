# track_strip.py
"""
トラックリストの各行に埋め込む「チャンネルストリップ」ウィジェット。

DAWやSynthesizer Vのように、トラックごとにMute/Solo/Volume(フェーダー)/Pan を
常時その場で操作できるようにする。QListWidget に対して

    item = QListWidgetItem()
    item.setSizeHint(strip.sizeHint())
    track_list_widget.addItem(item)
    track_list_widget.setItemWidget(item, strip)

の形で1トラック1行として差し込んで使う。

設計方針:
- フェーダーやパンをドラッグしている間は MainWindow 側で重いリスト再構築を
  絶対に行わない（selfの中で完結してtrack対象のデータだけを更新し、
  コールバックで軽い後処理だけを外に投げる）。
- Mute/Soloのように「他トラックの見た目にも影響する」操作だけ、
  MainWindow側にコールバックして全体の見た目を更新してもらう。
"""
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QLineEdit,
    QPushButton, QSlider, QComboBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor


class TrackStripWidget(QFrame):
    """1トラック分のミキサーストリップ（トラックリストの1行として使う）"""

    # (track, ...) の形でMainWindowに通知する
    selected = Signal(object)
    mute_toggled = Signal(object, bool)
    solo_toggled = Signal(object, bool)
    volume_changed = Signal(object, float)   # 0.0 - 1.0
    pan_changed = Signal(object, float)      # -1.0 - 1.0
    name_edited = Signal(object, str)
    voicebank_changed = Signal(object, str)

    def __init__(self, track, parent=None, voice_options=None):
        super().__init__(parent)
        self.track = track
        self.setObjectName("TrackStrip")
        self.setFixedHeight(64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 8, 0)
        root.setSpacing(8)

        # --- 左端: トラックカラーの縦バー ---
        self.color_bar = QFrame()
        self.color_bar.setObjectName("TrackColorBar")
        self.color_bar.setFixedWidth(4)
        root.addWidget(self.color_bar)

        content = QVBoxLayout()
        content.setContentsMargins(2, 6, 0, 6)
        content.setSpacing(4)
        root.addLayout(content, 1)

        # --- 上段: トラック名 + タイプバッジ ---
        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        self.type_badge = QLabel()
        self.type_badge.setObjectName("TrackTypeBadge")
        self.type_badge.setFixedSize(18, 18)
        self.type_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        top_row.addWidget(self.type_badge)

        self.name_edit = QLineEdit(track.name)
        self.name_edit.setObjectName("TrackNameField")
        self.name_edit.setFrame(False)
        self.name_edit.editingFinished.connect(self._on_name_edited)
        top_row.addWidget(self.name_edit, 1)

        self.voice_selector = QComboBox()
        self.voice_selector.setObjectName("TrackVoicebankSelector")
        self.voice_selector.setFixedWidth(142)
        self.voice_selector.setToolTip("このボーカルトラックで使用するUTAU音源")
        self.voice_selector.currentIndexChanged.connect(self._on_voicebank_changed)
        top_row.addWidget(self.voice_selector)
        self.set_voice_options(voice_options or [])

        content.addLayout(top_row)

        # --- 下段: M / S / Pan / Fader ---
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(6)

        self.mute_btn = QPushButton("M")
        self.mute_btn.setObjectName("MuteBtn")
        self.mute_btn.setCheckable(True)
        self.mute_btn.setFixedSize(22, 20)
        self.mute_btn.toggled.connect(self._on_mute_toggled)
        bottom_row.addWidget(self.mute_btn)

        self.solo_btn = QPushButton("S")
        self.solo_btn.setObjectName("SoloBtn")
        self.solo_btn.setCheckable(True)
        self.solo_btn.setFixedSize(22, 20)
        self.solo_btn.toggled.connect(self._on_solo_toggled)
        bottom_row.addWidget(self.solo_btn)

        self.pan_slider = QSlider(Qt.Orientation.Horizontal)
        self.pan_slider.setObjectName("PanSlider")
        self.pan_slider.setRange(-100, 100)
        self.pan_slider.setValue(0)
        self.pan_slider.setFixedWidth(46)
        self.pan_slider.setToolTip("Pan")
        self.pan_slider.valueChanged.connect(self._on_pan_changed)
        bottom_row.addWidget(self.pan_slider)

        self.vol_fader = QSlider(Qt.Orientation.Horizontal)
        self.vol_fader.setObjectName("VolumeFader")
        self.vol_fader.setRange(0, 100)
        self.vol_fader.setValue(100)
        self.vol_fader.setToolTip("Volume")
        self.vol_fader.valueChanged.connect(self._on_volume_changed)
        bottom_row.addWidget(self.vol_fader, 1)

        self.vol_label = QLabel("100%")
        self.vol_label.setObjectName("VolumeReadout")
        self.vol_label.setFixedWidth(34)
        self.vol_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bottom_row.addWidget(self.vol_label)

        content.addLayout(bottom_row)

        self.sync_from_track()

    # ------------------------------------------------------------------
    # データ ⇄ UI の同期
    # ------------------------------------------------------------------
    def sync_from_track(self):
        """self.track の内容をUIへ反映する（外部からのデータ変更後に呼ぶ）"""
        t = self.track
        for w in (self.name_edit, self.mute_btn, self.solo_btn, self.pan_slider, self.vol_fader, self.voice_selector):
            w.blockSignals(True)

        self.name_edit.setText(t.name)
        self.mute_btn.setChecked(bool(getattr(t, "is_muted", False)))
        self.solo_btn.setChecked(bool(getattr(t, "is_solo", False)))

        pan_val = int(round(getattr(t, "pan", 0.0) * 100))
        self.pan_slider.setValue(max(-100, min(100, pan_val)))

        vol_val = int(round(getattr(t, "volume", 1.0) * 100))
        self.vol_fader.setValue(max(0, min(100, vol_val)))
        self.vol_label.setText(f"{self.vol_fader.value()}%")

        current_voicebank = str(getattr(t, "voicebank_id", "") or "")
        voice_index = self.voice_selector.findData(current_voicebank)
        if current_voicebank and voice_index < 0:
            self.voice_selector.insertItem(0, f"⚠ 未検出: {current_voicebank}", current_voicebank)
            voice_index = 0
        if voice_index >= 0:
            self.voice_selector.setCurrentIndex(voice_index)

        for w in (self.name_edit, self.mute_btn, self.solo_btn, self.pan_slider, self.vol_fader, self.voice_selector):
            w.blockSignals(False)

        is_vocal = getattr(t, "track_type", "vocal") == "vocal"
        self.voice_selector.setVisible(is_vocal)
        self.voice_selector.setEnabled(is_vocal)
        self.type_badge.setText("V" if is_vocal else "A")
        self.type_badge.setProperty("trackType", "vocal" if is_vocal else "wave")

        color = getattr(t, "color_label", "#64D2FF") or "#64D2FF"
        self.color_bar.setStyleSheet(f"background-color: {QColor(color).name()};")

        self._restyle()

    def set_voice_options(self, voice_options) -> None:
        """Replace selector entries while preserving the track's stable Voicebank ID."""
        current_id = str(getattr(self.track, "voicebank_id", "") or "")
        self.voice_selector.blockSignals(True)
        self.voice_selector.clear()
        for name, voice_id in voice_options:
            self.voice_selector.addItem(str(name), str(voice_id))
        index = self.voice_selector.findData(current_id)
        if current_id and index < 0:
            self.voice_selector.addItem(f"⚠ 未検出: {current_id}", current_id)
            index = self.voice_selector.count() - 1
        if index >= 0:
            self.voice_selector.setCurrentIndex(index)
        elif self.voice_selector.count() > 0:
            self.voice_selector.setCurrentIndex(0)
        self.voice_selector.blockSignals(False)

    def _on_voicebank_changed(self, index: int) -> None:
        voice_id = self.voice_selector.itemData(index)
        if voice_id is not None:
            self.voicebank_changed.emit(self.track, str(voice_id))

    def set_dimmed(self, dimmed: bool):
        """ミュート中 or (他トラックがソロ中でこのトラックはソロでない) 場合に薄暗くする"""
        self.setProperty("dimmed", "true" if dimmed else "false")
        self._restyle()

    def set_current(self, is_current: bool):
        """現在編集対象のトラックであることを示すハイライト"""
        self.setProperty("current", "true" if is_current else "false")
        self._restyle()

    def _restyle(self):
        self.style().unpolish(self)
        self.style().polish(self)
        self.type_badge.style().unpolish(self.type_badge)
        self.type_badge.style().polish(self.type_badge)

    # ------------------------------------------------------------------
    # UIイベント → シグナル
    # ------------------------------------------------------------------
    def _on_mute_toggled(self, checked: bool):
        self.track.is_muted = checked
        self.mute_toggled.emit(self.track, checked)

    def _on_solo_toggled(self, checked: bool):
        self.track.is_solo = checked
        self.solo_toggled.emit(self.track, checked)

    def _on_pan_changed(self, value: int):
        pan = value / 100.0
        self.track.pan = pan
        label = "C" if value == 0 else (f"L{abs(value)}" if value < 0 else f"R{value}")
        self.pan_slider.setToolTip(f"Pan: {label}")
        self.pan_changed.emit(self.track, pan)

    def _on_volume_changed(self, value: int):
        vol = value / 100.0
        self.track.volume = vol
        self.vol_label.setText(f"{value}%")
        self.volume_changed.emit(self.track, vol)

    def _on_name_edited(self):
        new_name = self.name_edit.text().strip()
        if new_name and new_name != self.track.name:
            self.track.name = new_name
            self.name_edited.emit(self.track, new_name)

    def mousePressEvent(self, event):
        # フェーダー/ボタン等の子ウィジェット上でのクリックはそちらが処理するので、
        # ここに来るのは行の余白部分をクリックした場合＝トラック選択の意図
        if event.button() == Qt.MouseButton.LeftButton:
            self.selected.emit(self.track)
        super().mousePressEvent(event)
