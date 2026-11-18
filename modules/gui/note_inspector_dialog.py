"""Desktop note inspector aligned with the Web InspectorPanel."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)


class NoteInspectorDialog(QDialog):
    """Edit core note fields and existing UST intensity/flags metadata."""

    def __init__(self, note: object, tempo: float, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ノート詳細")
        self.setMinimumWidth(420)
        self._tempo = max(1.0, float(tempo))
        self._initial_flags = str(getattr(note, "_ust_flags", "") or "")
        self._initial_intensity = float(getattr(note, "_ust_intensity", 100.0))

        self.lyric_edit = QLineEdit(
            str(getattr(note, "lyrics", getattr(note, "lyric", "")) or ""),
            self,
        )
        self.note_number_spin = QSpinBox(self)
        self.note_number_spin.setRange(0, 127)
        self.note_number_spin.setValue(
            max(0, min(127, int(getattr(note, "note_number", 60))))
        )

        self.length_spin = QSpinBox(self)
        self.length_spin.setRange(1, 999999)
        duration = max(0.0, float(getattr(note, "duration", 0.5)))
        self.length_spin.setValue(
            max(1, min(999999, int(round(duration * self._tempo / 60.0 * 480.0))))
        )

        self.intensity_spin = QDoubleSpinBox(self)
        self.intensity_spin.setRange(0.0, 200.0)
        self.intensity_spin.setDecimals(1)
        self.intensity_spin.setSingleStep(1.0)
        self.intensity_spin.setValue(
            max(0.0, min(200.0, self._initial_intensity))
        )

        self.flags_edit = QLineEdit(self._initial_flags, self)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("歌詞 / 音素", self.lyric_edit)
        form.addRow("音高 (MIDI Note)", self.note_number_spin)
        form.addRow("長さ (Tick)", self.length_spin)
        form.addRow("音量強度 (Intensity 0–200)", self.intensity_spin)
        form.addRow("UTAU Flags", self.flags_edit)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText("適用")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def apply_to_note(self, note: object) -> bool:
        """Apply changed fields only; preserve implicit/inherited UST defaults."""
        changed = False

        lyric = self.lyric_edit.text()
        current_lyric = str(getattr(note, "lyrics", getattr(note, "lyric", "")) or "")
        if lyric != current_lyric:
            note.lyrics = lyric
            changed = True

        note_number = int(self.note_number_spin.value())
        if note_number != int(getattr(note, "note_number", 60)):
            note.note_number = note_number
            changed = True

        duration = (
            int(self.length_spin.value()) * 60.0 / (self._tempo * 480.0)
        )
        if abs(duration - float(getattr(note, "duration", 0.0))) > 1e-9:
            note.duration = duration
            changed = True

        intensity = float(self.intensity_spin.value())
        if abs(intensity - self._initial_intensity) > 1e-9:
            setattr(note, "_ust_intensity", intensity)
            changed = True

        flags = self.flags_edit.text()
        if flags != self._initial_flags:
            setattr(note, "_ust_flags", flags)
            setattr(note, "_ust_flags_present", True)
            changed = True

        return changed
