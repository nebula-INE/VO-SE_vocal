"""上部コントロールバーの折り返しと、音源パネル（取り込みボタン）の回帰テスト。"""
import itertools
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtWidgets import QApplication, QPushButton, QWidget  # noqa: E402

from modules.gui.flow_layout import FlowLayout  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.unit
def test_flow_layout_wraps_instead_of_overlapping(app):
    host = QWidget()
    lay = FlowLayout(host, h_spacing=10, v_spacing=5)
    btns = []
    for i in range(3):
        b = QPushButton(f"btn{i}")
        b.setFixedSize(100, 30)
        lay.addWidget(b)
        btns.append(b)

    # 3つ並べると 320px 必要。250px しかなければ 2 行になる
    assert lay.heightForWidth(250) == 30 + 5 + 30
    assert lay.heightForWidth(400) == 30
    lay.setGeometry(QRect(0, 0, 250, 100))
    rects = [b.geometry() for b in btns]
    for a, b in itertools.combinations(rects, 2):
        assert not a.intersects(b)
    assert rects[2].y() > rects[0].y(), "3つ目は次の行へ"


@pytest.mark.unit
def test_flow_layout_insert_widget(app):
    host = QWidget()
    lay = FlowLayout(host)
    a, b, c = QPushButton("a"), QPushButton("b"), QPushButton("c")
    lay.addWidget(a)
    lay.addWidget(c)
    lay.insertWidget(1, b)
    assert [lay.itemAt(i).widget() for i in range(lay.count())] == [a, b, c]


@pytest.fixture(scope="module")
def window(app):
    try:
        from modules.gui.themes import apply_theme
        apply_theme("dark")
        from modules.gui.main_window import MainWindow
        w = MainWindow(engine=None, ai=None)
    except Exception as e:  # ネイティブ依存などで起動できない環境では飛ばす
        pytest.skip(f"MainWindow を生成できません: {e}")
    return w


def _rect(w, wd):
    p = wd.mapTo(w, wd.rect().topLeft())
    return QRect(p.x(), p.y(), wd.width(), wd.height())


@pytest.mark.smoke
@pytest.mark.parametrize("size", [(1100, 680), (1200, 832), (1366, 768)])
def test_control_bar_has_no_overlapping_widgets(app, window, size):
    window.resize(*size)
    window.show()
    for _ in range(8):
        app.processEvents()
    names = ["record_button", "character_selector", "midi_port_selector", "open_button",
             "rescan_voices_button", "ai_analyze_button", "auto_lyrics_button", "lyrics_button"]
    rects = {n: _rect(window, getattr(window, n)) for n in names}
    for a, b in itertools.combinations(names, 2):
        inter = rects[a].intersected(rects[b])
        assert inter.width() <= 2 or inter.height() <= 2, f"{a} と {b} が重なっている"
    for n, r in rects.items():
        assert r.right() <= window.width() + 1, f"{n} がウィンドウの外にはみ出している"


@pytest.mark.smoke
def test_piano_roll_is_not_squeezed_by_bottom_panel(app, window):
    window.resize(1200, 832)
    window.show()
    for _ in range(8):
        app.processEvents()
    assert window.timeline_widget.height() >= 400
    # 音源パネルは下ではなく右ドックにある（ピアノロールの縦幅を奪わない）
    from PySide6.QtCore import Qt
    assert window.dockWidgetArea(window.voice_gallery_dock) == Qt.DockWidgetArea.RightDockWidgetArea


@pytest.mark.smoke
def test_voice_panel_has_import_button_with_menu(app, window):
    g = window.voice_gallery
    assert g.import_button.text().strip() == "音源を追加"
    labels = [a.text() for a in g.import_button.menu().actions() if not a.isSeparator()]
    assert any("フォルダ" in t for t in labels)
    assert any("ZIP" in t for t in labels)
    assert any("再スキャン" in t for t in labels)


@pytest.mark.smoke
def test_gallery_columns_follow_width(app, window):
    g = window.voice_gallery
    cols = g._max_columns()
    assert cols >= 1
    # どのカラムにもカードの幅が収まる（右端で切れない）
    assert cols * g.CARD_WIDTH + (cols - 1) * g.GRID_SPACING + 2 * g.GRID_MARGIN \
        <= g.scroll_area.viewport().width() + 1


@pytest.mark.smoke
def test_voice_selector_is_populated_from_gallery(app, window):
    combo = window.character_selector
    assert combo.count() >= 1
    assert combo.findText("VO-SE Official (Embedded)") >= 0
