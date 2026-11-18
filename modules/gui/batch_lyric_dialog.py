"""Desktop batch lyric dialog aligned with the Web editor's tokenization options."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from modules.utils.lyric_tokens import assign_lyrics, parse_lyric_tokens


class BatchLyricDialog(QDialog):
    """Preview and apply lyric tokens to notes in chronological order."""

    def __init__(
        self,
        notes: Sequence[object],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("歌詞の一括入力")
        self.setMinimumSize(560, 440)
        self._notes = list(notes)

        layout = QVBoxLayout(self)
        description = QLabel(
            f"時間順に並べた {len(self._notes)} 個のノートへ歌詞を割り当てます。"
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        preset_label = QLabel("クイック入力プリセット")
        layout.addWidget(preset_label)
        presets = [
            ("ドレミファソラシド", "ど れ み ふぁ そ ら し ど"),
            ("かえるのうた", "か え る の う た が き こ え て く る よ"),
            ("キラキラ星", "き ら き ら ひ か る お そ ら の ほ し よ"),
            ("あー (全音符あ)", "あ"),
            ("ららら (ループ用)", "ら ら ら"),
        ]
        preset_grid = QGridLayout()
        for index, (label, text) in enumerate(presets):
            button = QPushButton(label, self)
            button.setMinimumHeight(32)
            button.clicked.connect(lambda _checked=False, value=text: self.input_edit.setPlainText(value))
            preset_grid.addWidget(button, index // 3, index % 3)
        layout.addLayout(preset_grid)

        self.input_edit = QPlainTextEdit(self)
        self.input_edit.setPlaceholderText(
            "例: さくら さくら\n空白区切り・連続入力・1行1音符に対応"
        )
        self.input_edit.setTabChangesFocus(True)
        layout.addWidget(self.input_edit, 1)

        options = QVBoxLayout()
        delimiter_label = QLabel("区切り方法")
        options.addWidget(delimiter_label)
        self.delimiter_combo = QComboBox(self)
        self.delimiter_combo.addItem("自動判別", "auto")
        self.delimiter_combo.addItem("空白・スペース区切り", "space")
        self.delimiter_combo.addItem("1文字・モーラごと", "char")
        self.delimiter_combo.addItem("行ごと（1行1ノート）", "line")
        options.addWidget(self.delimiter_combo)

        fill_label = QLabel("歌詞が足りない場合")
        options.addWidget(fill_label)
        self.fill_combo = QComboBox(self)
        self.fill_combo.addItem("既存の歌詞を維持", "keep")
        self.fill_combo.addItem("先頭から繰り返して埋める", "loop")
        options.addWidget(self.fill_combo)
        layout.addLayout(options)

        self.preview_label = QLabel(self)
        self.preview_label.setWordWrap(True)
        self.preview_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.preview_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Apply,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText("一括適用")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.input_edit.textChanged.connect(self._refresh_preview)
        self.delimiter_combo.currentIndexChanged.connect(self._refresh_preview)
        self.fill_combo.currentIndexChanged.connect(self._refresh_preview)
        self._refresh_preview()

    def _existing_lyrics(self) -> list[str]:
        return [
            str(getattr(note, "lyrics", getattr(note, "lyric", "")) or "")
            for note in self._notes
        ]

    def assigned_lyrics(self) -> list[str]:
        tokens = parse_lyric_tokens(
            self.input_edit.toPlainText(),
            str(self.delimiter_combo.currentData()),
        )
        return assign_lyrics(
            self._existing_lyrics(),
            tokens,
            str(self.fill_combo.currentData()),
        )

    def _refresh_preview(self) -> None:
        tokens = parse_lyric_tokens(
            self.input_edit.toPlainText(),
            str(self.delimiter_combo.currentData()),
        )
        assigned = assign_lyrics(
            self._existing_lyrics(),
            tokens,
            str(self.fill_combo.currentData()),
        )
        changed = sum(
            old != new for old, new in zip(self._existing_lyrics(), assigned)
        )
        preview_items = [
            f"{index + 1}: {lyric or '(空)'}"
            for index, lyric in enumerate(assigned[:16])
        ]
        suffix = (
            f"\n…ほか {len(assigned) - 16} ノート"
            if len(assigned) > 16
            else ""
        )
        self.preview_label.setText(
            f"分割結果: {len(tokens)} トークン / 更新予定: {changed} ノート\n"
            + ("\n".join(preview_items) if preview_items else "歌詞を入力してください")
            + suffix
        )
