# modules/utils/pyside_stub.py
"""
PySide6 がインストールされていない環境（Web Studio サーバー環境や headless 環境など）で
モジュールをインポートした際に ModuleNotFoundError にならないためのスタブ。
"""

from importlib.util import find_spec
import sys

def setup_pyside_stub():
    try:
        find_spec("PySide6")
        return
    except ImportError:
        pass

    class DummyMeta(type):
        def __getattr__(cls, name):
            return DummyClass
 
    class DummyClass(metaclass=DummyMeta):
        def __init__(self, *args, **kwargs):
            pass
        def __getattr__(self, name):
            return DummyClass

    class DummySignal:
        def __init__(self, *args, **kwargs):
            self._callbacks = []

        def emit(self, *args, **kwargs):
            for cb in self._callbacks:
                try:
                    cb(*args, **kwargs)
                except Exception:
                    pass

        def connect(self, func):
            if callable(func):
                self._callbacks.append(func)

    def Signal(*args, **kwargs):
        return DummySignal()

    def Slot(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

    import types
    import importlib.machinery as importlib_machinery

    pyside6 = types.ModuleType("PySide6")
    qtcore = types.ModuleType("PySide6.QtCore")
    qtwidgets = types.ModuleType("PySide6.QtWidgets")
    qtgui = types.ModuleType("PySide6.QtGui")
    qtmultimedia = types.ModuleType("PySide6.QtMultimedia")

    pyside6.__spec__ = importlib_machinery.ModuleSpec("PySide6", None)
    qtcore.__spec__ = importlib_machinery.ModuleSpec("PySide6.QtCore", None)
    qtwidgets.__spec__ = importlib_machinery.ModuleSpec("PySide6.QtWidgets", None)
    qtgui.__spec__ = importlib_machinery.ModuleSpec("PySide6.QtGui", None)
    qtmultimedia.__spec__ = importlib_machinery.ModuleSpec("PySide6.QtMultimedia", None)

    # QtCore
    qtcore.QObject = DummyClass
    qtcore.Signal = Signal
    qtcore.Slot = Slot
    qtcore.QThread = DummyClass
    qtcore.QUrl = DummyClass
    qtcore.Qt = DummyClass
    qtcore.QRect = DummyClass
    qtcore.QRectF = DummyClass
    qtcore.QPoint = DummyClass
    qtcore.QPointF = DummyClass
    qtcore.QSize = DummyClass
    qtcore.QTimer = DummyClass
    qtcore.QSettings = DummyClass

    # QtWidgets
    qtwidgets.QApplication = DummyClass
    qtwidgets.QWidget = DummyClass
    qtwidgets.QMainWindow = DummyClass
    qtwidgets.QMessageBox = DummyClass
    qtwidgets.QFileDialog = DummyClass
    qtwidgets.QLabel = DummyClass
    qtwidgets.QPushButton = DummyClass
    qtwidgets.QLineEdit = DummyClass
    qtwidgets.QVBoxLayout = DummyClass
    qtwidgets.QHBoxLayout = DummyClass
    qtwidgets.QFrame = DummyClass
    qtwidgets.QSlider = DummyClass
    qtwidgets.QDockWidget = DummyClass
    qtwidgets.QToolBar = DummyClass
    qtwidgets.QTableWidget = DummyClass
    qtwidgets.QTableWidgetItem = DummyClass
    qtwidgets.QDialog = DummyClass
    qtwidgets.QListWidgetItem = DummyClass
    qtwidgets.QInputDialog = DummyClass

    # QtGui
    qtgui.QIcon = DummyClass
    qtgui.QFont = DummyClass
    qtgui.QPainter = DummyClass
    qtgui.QPen = DummyClass
    qtgui.QBrush = DummyClass
    qtgui.QColor = DummyClass
    qtgui.QPixmap = DummyClass
    qtgui.QAction = DummyClass
    qtgui.QKeySequence = DummyClass

    # QtMultimedia
    qtmultimedia.QMediaPlayer = DummyClass
    qtmultimedia.QAudioOutput = DummyClass

    # Link child modules to parent
    pyside6.QtCore = qtcore
    pyside6.QtWidgets = qtwidgets
    pyside6.QtGui = qtgui
    pyside6.QtMultimedia = qtmultimedia

    sys.modules["PySide6"] = pyside6 # type: ignore[assignment]
    sys.modules["PySide6.QtCore"] = qtcore
    sys.modules["PySide6.QtWidgets"] = qtwidgets
    sys.modules["PySide6.QtGui"] = qtgui
    sys.modules["PySide6.QtMultimedia"] = qtmultimedia

setup_pyside_stub()

HAS_PYSIDE6 = "PySide6" in sys.modules
