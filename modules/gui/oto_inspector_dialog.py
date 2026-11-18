"""Desktop UTAU oto.ini inspector and editor."""
from __future__ import annotations

import os

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from modules.data.oto_editor import save_oto_entry
from modules.data.oto_parser import OtoEntry, OtoParser


class OtoInspectorDialog(QDialog):
    """Inspect aliases, edit timing parameters, and preview the source WAV."""

    _FIELDS = (
        ("left_blank", "Offset / 左ブランク (ms)"),
        ("fixed_range", "Consonant / 固定範囲 (ms)"),
        ("right_blank", "Cutoff / 右ブランク (ms)"),
        ("preutterance", "Preutterance / 先行発声 (ms)"),
        ("overlap", "Overlap / オーバーラップ (ms)"),
    )

    def __init__(self, voice_dir: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.voice_dir = os.path.abspath(voice_dir)
        self.setWindowTitle("UTAU 原音設定 (oto.ini Inspector)")
        self.resize(920, 620)
        self._entries: list[OtoEntry] = []
        self._selected: OtoEntry | None = None
        self._player = None
        self._audio_output = None

        root = QVBoxLayout(self)
        root.addWidget(QLabel(f"音源フォルダ: {self.voice_dir}"))
        self.search = QLineEdit(self)
        self.search.setPlaceholderText("エイリアス / WAV ファイル名を検索...")
        self.search.textChanged.connect(self._populate_table)
        root.addWidget(self.search)

        self.table = QTableWidget(0, 8, self)
        self.table.setHorizontalHeaderLabels([
            "Alias", "WAV", "WAV状態", "Offset", "Fixed", "Cutoff",
            "Preutterance", "Overlap",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._select_current)
        self.table.horizontalHeader().setStretchLastSection(True)
        root.addWidget(self.table, 1)

        self.controls: dict[str, QDoubleSpinBox] = {}
        form = QFormLayout()
        for key, label in self._FIELDS:
            spin = QDoubleSpinBox(self)
            spin.setRange(-10000.0, 10000.0)
            spin.setDecimals(2)
            spin.setSingleStep(1.0)
            self.controls[key] = spin
            form.addRow(label, spin)
        root.addLayout(form)

        buttons = QHBoxLayout()
        self.status = QLabel("oto.ini を読み込み中...")
        buttons.addWidget(self.status, 1)
        self.preview_button = QPushButton("WAV試聴", self)
        self.preview_button.clicked.connect(self._preview_current)
        buttons.addWidget(self.preview_button)
        self.save_button = QPushButton("選択エントリを保存", self)
        self.save_button.clicked.connect(self._save_current)
        buttons.addWidget(self.save_button)
        close_button = QPushButton("閉じる", self)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        root.addLayout(buttons)

        self._reload_entries()

    def _reload_entries(self) -> None:
        parser = OtoParser()
        count = parser.load_voice_dir(self.voice_dir, use_cache=False, reset=True)
        self._entries = list(parser._db.values())
        # Scoped aliases can refer to the same OtoEntry; show each physical
        # alias once in this dialog.
        unique: dict[tuple[str, str, str], OtoEntry] = {}
        for entry in self._entries:
            key = (entry.voice_dir, entry.filename, entry.alias)
            unique.setdefault(key, entry)
        self._entries = list(unique.values())
        self._populate_table()
        self.status.setText(f"{len(self._entries)} エントリ / {count} 件解析")

    def _populate_table(self) -> None:
        query = self.search.text().casefold().strip()
        rows = [
            entry for entry in self._entries
            if not query or query in entry.alias.casefold() or query in entry.filename.casefold()
        ]
        self.table.setRowCount(len(rows))
        for row, entry in enumerate(rows):
            values = [
                entry.alias, entry.filename,
                "OK" if os.path.isfile(entry.wav_path) else "WAV未検出",
                f"{entry.left_blank:g}", f"{entry.fixed_range:g}",
                f"{entry.right_blank:g}", f"{entry.preutterance:g}",
                f"{entry.overlap:g}",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(256, (entry.voice_dir, entry.filename, entry.alias))
                self.table.setItem(row, column, item)
        if rows:
            self.table.selectRow(0)
        else:
            self._selected = None
            self.status.setText("該当する oto.ini エントリがありません")

    def _entry_for_row(self, row: int) -> OtoEntry | None:
        item = self.table.item(row, 0)
        identity = item.data(256) if item is not None else None
        if not identity:
            return None
        return next((
            entry for entry in self._entries
            if (entry.voice_dir, entry.filename, entry.alias) == tuple(identity)
        ), None)

    def _select_current(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            self._selected = None
            return
        self._selected = self._entry_for_row(selected_rows[0].row())
        if self._selected is None:
            return
        for key, _label in self._FIELDS:
            self.controls[key].setValue(float(getattr(self._selected, key)))
        self.status.setText(
            f"{self._selected.alias} — {self._selected.filename}"
        )

    def _preview_current(self) -> None:
        entry = self._selected
        if entry is None:
            return
        if not os.path.isfile(entry.wav_path):
            QMessageBox.warning(self, "試聴できません", f"WAV が見つかりません:\n{entry.wav_path}")
            return
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            if self._player is None:
                self._audio_output = QAudioOutput(self)
                self._player = QMediaPlayer(self)
                self._player.setAudioOutput(self._audio_output)
            self._player.setSource(QUrl.fromLocalFile(entry.wav_path))
            self._player.play()
        except Exception as exc:
            QMessageBox.warning(self, "試聴できません", f"音声プレイヤーを初期化できませんでした:\n{exc}")

    def _save_current(self) -> None:
        entry = self._selected
        if entry is None:
            return
        try:
            values = {key: spin.value() for key, spin in self.controls.items()}
            save_oto_entry(entry, **values)
            self.status.setText(f"保存しました: {entry.alias}")
            self._reload_entries()
        except Exception as exc:
            QMessageBox.critical(self, "保存エラー", f"oto.ini を更新できませんでした:\n{exc}")

    def closeEvent(self, event) -> None:
        if self._player is not None:
            self._player.stop()
        super().closeEvent(event)
