# modules/gui/flow_layout.py
"""
幅が足りなくなったら次の行へ折り返す QLayout。

コントロールバー（録音 / Voice / MIDI / 開く / AI Auto Setup ...）のように、
ウィジェット数が多く、ウィンドウ幅やドックの幅によって収まりきらないことがある行に使う。
QHBoxLayout だと最小幅を下回ったときにウィジェットが重なって見えるが、
FlowLayout は heightForWidth を通じて親レイアウトに必要な高さを伝えるため、
重ならずに 2 行以上へ折り返す。

QHBoxLayout と同じ呼び方（addWidget / insertWidget / addSpacing / addStretch）を
受け付けるので、既存のコードからの置き換えが簡単。
"""
from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QLayout, QLayoutItem, QWidget, QWidgetItem


class FlowLayout(QLayout):
    def __init__(self, parent: Optional[QWidget] = None,
                 margin: int = 0, h_spacing: int = 6, v_spacing: int = 6):
        super().__init__(parent)
        self._items: List[QLayoutItem] = []
        self._h_spacing = h_spacing
        self._v_spacing = v_spacing
        self.setContentsMargins(margin, margin, margin, margin)

    # ---- QLayout の必須実装 ------------------------------------------------
    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)
        self.invalidate()

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> Optional[QLayoutItem]:
        if 0 <= index < len(self._items):
            item = self._items.pop(index)
            self.invalidate()
            return item
        return None

    # ---- QHBoxLayout 互換の補助 -------------------------------------------
    def insertWidget(self, index: int, widget: QWidget) -> None:
        """index の位置にウィジェットを差し込む（範囲外は末尾）。"""
        self.addChildWidget(widget)
        item = _widget_item(widget)
        if index < 0 or index > len(self._items):
            index = len(self._items)
        self._items.insert(index, item)
        self.invalidate()

    def addSpacing(self, size: int) -> None:
        """折り返しレイアウトでは固定スペーサーは不要（隙間は spacing で確保される）。"""
        return

    def addStretch(self, stretch: int = 0) -> None:
        """折り返しレイアウトでは伸縮スペーサーは意味を持たないため何もしない。"""
        return

    # ---- サイズ計算 -------------------------------------------------------
    def horizontalSpacing(self) -> int:
        return self._h_spacing

    def verticalSpacing(self) -> int:
        return self._v_spacing

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        # 最小幅は「一番幅の広い 1 個」。それ以上は折り返して吸収する。
        size = QSize()
        for item in self._items:
            if item.isEmpty():
                continue
            size = size.expandedTo(item.minimumSize())
        m: QMargins = self.contentsMargins()
        size += QSize(m.left() + m.right(), m.top() + m.bottom())
        return size

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        m = self.contentsMargins()
        eff = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y = eff.x(), eff.y()
        line_height = 0

        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            w, h = hint.width(), hint.height()
            next_x = x + w + self._h_spacing
            if next_x - self._h_spacing > eff.right() + 1 and line_height > 0:
                # 収まらない → 次の行へ
                x = eff.x()
                y = y + line_height + self._v_spacing
                next_x = x + w + self._h_spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), QSize(w, h)))
            x = next_x
            line_height = max(line_height, h)

        return y + line_height - rect.y() + m.bottom()


def _widget_item(widget: QWidget) -> QLayoutItem:
    return QWidgetItem(widget)


__all__ = ["FlowLayout"]
