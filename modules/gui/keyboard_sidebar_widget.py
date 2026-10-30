#keyboard_sidebar_widget.py

import logging
from typing import Optional
from PySide6.QtWidgets import QWidget
from PySide6.QtGui import (
    QPainter, 
    QColor, 
    QPen, 
    QFont, 
    QPaintEvent, 
    QLinearGradient, 
    QPixmap, 
    QMouseEvent
)
from PySide6.QtCore import Qt, QRect, QSize, Slot, Signal

logger = logging.getLogger(__name__)

class KeyboardSidebarWidget(QWidget):
    """
    [VO-SE Pro: Keyboard Sidebar Widget]
    
    代表、こちらのウィジェットは以下の機能を統合しています：
    - High DPI (Retina/4K) 完全対応のオフスクリーン・レンダリング。
    - 左クリック中のマウス移動による「グリッサンド演奏」への対応。
    - Apple風のネオン・ハイライトとアクセントバーによる視覚効果。
    - C++エンジン層へ即座に伝達するための音響信号（Signal）送出。
    """
    
    # 外部（MainWindowやAudioEngine）へ通知するための信号
    note_pressed = Signal(int)
    note_released = Signal(int)

    def __init__(self, key_height_pixels: float = 20.0, parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        # 基本パラメータ
        self.key_height_pixels: float = key_height_pixels
        self.scroll_y_offset: float = 0.0
        self._last_rendered_height: float = -1.0
        
        # キャッシュ（VRAM/RAM上への事前描画）
        self._cache_pixmap: Optional[QPixmap] = None
        
        # 操作性向上のための固定幅（代表の指定された72pxを採用）
        self.setMinimumWidth(72)
        self.setFixedWidth(72) 

        # フォント設定
        self.label_font = QFont("Segoe UI", 8)
        self.label_font.setBold(True)
        
        # マウスイベントの継続監視（グリッサンドに必須）
        self.setMouseTracking(True)
        self._current_pressed_note: Optional[int] = None

    def sizeHint(self) -> QSize:
        return QSize(72, 600)

    @staticmethod
    def is_black_key(note_number: int) -> bool:
        """MIDIノート番号が黒鍵（C#, D#, F#, G#, A#）かどうかを判定"""
        m = note_number % 12
        return m in (1, 3, 6, 8, 10)

    def resizeEvent(self, event):
        """リサイズ時にキャッシュを破棄し、次回の描画で再生成させる"""
        self._cache_pixmap = None
        super().resizeEvent(event)

    # ============================================================
    # 最速描画ロジック（オフスクリーン・キャッシュ）
    # ============================================================

    def _update_cache(self):
        """
        128音すべての鍵盤を、デバイスの解像度（DPI）に合わせて
        巨大な一枚の画像としてメモリに書き込みます。
        """
        dpr = self.devicePixelRatioF()
        total_height = int(self.key_height_pixels * 128)
        
        # 解像度に合わせてピクセル数を最適化したPixmapを生成
        self._cache_pixmap = QPixmap(int(self.width() * dpr), int(total_height * dpr))
        cache_pixmap = self._cache_pixmap
        if cache_pixmap is None:
            return
        cache_pixmap.setDevicePixelRatio(dpr)
        cache_pixmap.fill(Qt.GlobalColor.transparent)
        
        painter = QPainter(cache_pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # --- レイヤー1: 全白鍵の描画 ---
        for n in range(128):
            if self.is_black_key(n):
                continue
            
            y = (127 - n) * self.key_height_pixels
            rect = QRect(0, int(y), self.width(), int(self.key_height_pixels))
            
            # 質感を出すためのグラデーション
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            grad.setColorAt(0, QColor(222, 225, 232))
            grad.setColorAt(1, QColor(204, 208, 217))
            
            painter.setBrush(grad)
            painter.setPen(QPen(QColor(120, 126, 142), 1))
            painter.drawRect(rect)
            
            # C音のラベル描画（オクターブ位置の把握用）
            if n % 12 == 0:
                octave = (n // 12) - 1
                painter.setFont(self.label_font)
                painter.setPen(QColor(70, 76, 92))
                painter.drawText(
                    rect.adjusted(0, 0, -8, 0), 
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, 
                    f"C{octave}"
                )

        # --- レイヤー2: 全黒鍵の描画 ---
        black_w = int(self.width() * 0.62)
        for n in range(128):
            if not self.is_black_key(n):
                continue
            
            y = (127 - n) * self.key_height_pixels
            rect = QRect(0, int(y), black_w, int(self.key_height_pixels))
            
            # 黒鍵の高級感を出す深みのあるグラデーション
            grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            grad.setColorAt(0, QColor(52, 55, 64))
            grad.setColorAt(1, QColor(20, 21, 26))
            
            painter.setBrush(grad)
            painter.setPen(QPen(Qt.GlobalColor.black, 1))
            painter.drawRect(rect)
            
            # 立体感を出すための上端のハイライト線
            painter.setPen(QPen(QColor(90, 90, 90, 150), 1))
            painter.drawLine(rect.left() + 1, rect.top() + 1, rect.right() - 1, rect.top() + 1)

        painter.end()
        self._last_rendered_height = self.key_height_pixels
        logger.debug("KeyboardSidebar: Cache updated.")

    # ============================================================
    # インタラクション・スロット
    # ============================================================

    @Slot(int)
    def set_vertical_offset(self, offset_pixels: int):
        """タイムラインのスクロールに同期"""
        if self.scroll_y_offset != float(offset_pixels):
            self.scroll_y_offset = float(offset_pixels)
            self.update()

    @Slot(float)
    def set_key_height_pixels(self, height: float):
        """拡大・縮小（ズーム）に対応"""
        if self.key_height_pixels != height:
            self.key_height_pixels = height
            self._cache_pixmap = None  # キャッシュを無効化
            self.update()

    def _y_to_note(self, y: float) -> int:
        """座標からMIDIノート番号を算出"""
        absolute_y = y + self.scroll_y_offset
        note = 127 - int(absolute_y / self.key_height_pixels)
        return max(0, min(127, note))

    # ============================================================
    # イベントハンドラ（演奏ロジック）
    # ============================================================

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.MouseButton.LeftButton:  
            note = self._y_to_note(event.position().y())
            self._current_pressed_note = note
            self.note_pressed.emit(note)
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent):
        # グリッサンド（スライド演奏）の処理
        if event.buttons() & Qt.MouseButton.LeftButton:
            new_note = self._y_to_note(event.position().y())
            if new_note != self._current_pressed_note:
                # 前の音を止めて新しい音を鳴らす
                if self._current_pressed_note is not None:
                    self.note_released.emit(self._current_pressed_note)
                
                self._current_pressed_note = new_note
                self.note_pressed.emit(new_note)
                self.update()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._current_pressed_note is not None:
            self.note_released.emit(self._current_pressed_note)
            self._current_pressed_note = None
            self.update()

    # ============================================================
    # レンダリング（メイン）
    # ============================================================

    def paintEvent(self, event: QPaintEvent):
        """
        高解像度キャッシュを使用したレンダリング。
        Pyrightの型チェック（None安全性）を考慮した実装。
        """
        # 1. キャッシュの整合性チェックと生成
        if self._cache_pixmap is None or self.key_height_pixels != self._last_rendered_height:
            self._update_cache()

        # [Pyright修正] 明示的なNoneチェックを追加し、これ以降のself._cache_pixmapがNon-Nullableであることを保証
        if self._cache_pixmap is None:
            return

        painter = QPainter(self)
        
        # 2. キャッシュされた全鍵盤を「一撃」で転送
        dpr = self.devicePixelRatioF()
        
        # 型安全な描画（self._cache_pixmapはここでは確実にQPixmap型）
        painter.drawPixmap(
            0, 0, 
            self._cache_pixmap, 
            0, int(self.scroll_y_offset * dpr), 
            int(self.width() * dpr), int(self.height() * dpr)
        )

        # 3. 押下状態のハイライト
        if self._current_pressed_note is not None:
            # y座標の計算（floatからintへのキャストを徹底）
            y = (127 - self._current_pressed_note) * self.key_height_pixels - self.scroll_y_offset
            
            # Apple風ネオングリーン
            painter.fillRect(
                QRect(0, int(y), self.width(), int(self.key_height_pixels)), 
                QColor(0, 255, 127, 70)
            )
            
            # 左端の4pxアクセントバー
            painter.fillRect(
                QRect(0, int(y), 4, int(self.key_height_pixels)), 
                QColor(0, 255, 127, 200)
            )

        # 4. タイムラインとの境界線
        painter.setPen(QPen(QColor(0, 0, 0, 50), 1))
        painter.drawLine(self.width() - 1, 0, self.width() - 1, self.height())
        
        painter.end()
