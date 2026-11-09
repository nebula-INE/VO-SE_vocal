#main_window.py
# ==========================================================================
# 1. 標準ライブラリ (Standard Libraries)
# ==========================================================================
import os
import sys
import time
import wave
import json
import tempfile
import shutil
import ctypes
import threading
import math
from typing import Tuple
from copy import deepcopy
from scipy.signal import hilbert
from scipy.fft import rfft, rfftfreq
from typing import Any, Callable, List, Dict, Optional, TYPE_CHECKING, cast

# ==========================================================================
# 2. 数値計算・信号処理 (Numerical Processing)
# ==========================================================================
import importlib
import importlib.util
import numpy as np
import soundfile as sf
from modules.ffi import CNoteEvent, as_c_double_array
mido = importlib.import_module("mido") if importlib.util.find_spec("mido") else None
ort = importlib.import_module("onnxruntime") if importlib.util.find_spec("onnxruntime") else None

# ==========================================================================
# 3. GUIライブラリ (PySide6 )
# ==========================================================================
from PySide6.QtCore import (  # noqa: E402
    Qt, Signal, QThread, QTimer,
    QObject, QRunnable, QThreadPool, Slot,
    QElapsedTimer
)
from PySide6.QtWidgets import (  # noqa: E402
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSlider,
    QPushButton, QFileDialog, QScrollBar, QInputDialog, QLineEdit,
    QLabel, QSplitter, QComboBox, QProgressBar, QMessageBox, QToolBar,
    QGridLayout, QFrame, QDialog, QScrollArea, QSizePolicy, QButtonGroup,
    QListWidget, QApplication, 
    QDockWidget     
)
from PySide6.QtGui import (  # noqa: E402
    QAction, QKeySequence, QFont, QColor, QShortcut, QPixmap, 
    QPainter, QPen
)

# ==========================================================================
# 4. 型チェック時のみのインポート (reportAssignmentType エラーを根本解決)
# ==========================================================================
if TYPE_CHECKING:
    # 実行時には無視され、型チェック時にのみ参照される
    try:
        from modules.gui.core_manager import VoseCoreManager as CoreManager # type: ignore
    except ImportError:
        # Pyrightがパスを見失っている場合、Anyで逃がして警告を黙らせる
        CoreManager = Any # type: ignore

# ==========================================================================
# 5. 自作モジュール (実際の読み込み)
# ==========================================================================
# プロジェクトルートを sys.path に追加（GitHub Desktop/CI環境でのパス解決を確実に）
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

try:
    from modules.gui.timeline_widget import TimelineWidget # type: ignore[assignment]
    from modules.gui.graph_editor_widget import GraphEditorWidget # type: ignore[assignment]
    from modules.gui.keyboard_sidebar_widget import KeyboardSidebarWidget # type: ignore[assignment]
    from modules.gui.core_manager import vose_manager, CNoteEvent # type: ignore[assignment]
    from modules.audio.voice_manager import VoiceManager # type: ignore[assignment]
    from modules.data.licensing import LicenseManager # type: ignore[assignment]
    from modules.gui.audio_mixin import AudioOutputMixin # type: ignore[assignment]
    from modules.gui.voice_mixin import VoiceManagerMixin # type: ignore[assignment]
    from modules.gui.mixins.voice_management_mixin import VoiceManagementMixin # type: ignore[assignment]
    from modules.gui.effects_panel import EffectsPanel  # type: ignore[assignment]
    from modules.gui.track_strip import TrackStripWidget # type: ignore[assignment]
    from modules.gui.icons import icon # type: ignore[assignment]
except ImportError as e:
    print(f"⚠️ Absolute import failed, falling back to relative: {e}")
    # フォールバック（相対インポート）
    from .timeline_widget import TimelineWidget
    from .graph_editor_widget import GraphEditorWidget
    from .keyboard_sidebar_widget import KeyboardSidebarWidget
    from .core_manager import vose_manager
    from ..audio.voice_manager import VoiceManager
    from .audio_mixin import AudioOutputMixin
    from .voice_mixin import VoiceManagerMixin

# MainWindow を機能ごとに分割した Mixin 群。
# ここでの import は main_window.py を逆 import しないため、循環 import は発生しない。
from modules.gui.mixins.project_io_mixin import ProjectIOMixin  # noqa: E402

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

# ==========================================================================
# 6. グローバル設定
# ==========================================================================
os.environ["OMP_NUM_THREADS"] = "1"

# ==========================================================================
# 7. GUIセットアップ
# ==========================================================================
def setup_scrolling(self):
    """
    v_scrollbarの値が変化した際、両方のウィジェットのオフセットを更新する
    """
    self.v_scrollbar.setRange(0, 127 * self.note_height - self.timeline_height)
    
    # シグナルとスロットの接続
    self.v_scrollbar.valueChanged.connect(self.sync_vertical_scroll)

def sync_vertical_scroll(self, value):
    """
    垂直スクロールバーの値をピアノロールとタイムラインに伝播させる
    """
    # 鍵盤の描画位置を更新
    self.piano_roll_widget.set_vertical_offset(value)
    # ノートエリアの描画位置を更新
    self.timeline_widget.set_vertical_offset(value)
    
    # 再描画を強制
    self.piano_roll_widget.update()
    self.timeline_widget.update()

try:
    from modules.data.data_models import NoteEvent  # type: ignore
except ImportError:
    class NoteEvent(ctypes.Structure):
        _fields_ = [
            ("wav_path", ctypes.c_char_p),      # 原音キー(phoneme)
            ("pitch_curve", ctypes.POINTER(ctypes.c_double)),
            ("pitch_length", ctypes.c_int),
            ("gender_curve", ctypes.POINTER(ctypes.c_double)),
            ("tension_curve", ctypes.POINTER(ctypes.c_double)),
            ("breath_curve", ctypes.POINTER(ctypes.c_double)),
            ("vibrato_depth_curve", ctypes.POINTER(ctypes.c_double)),
            ("vibrato_rate_curve", ctypes.POINTER(ctypes.c_double)),
            ("vibrato_curve_length", ctypes.c_int),
            # 必要に応じて UTAU用パラメータ(offset等)をここに追加
        ]

        def __init__(self, **kwargs):
            super().__init__()
            # Python側での管理用属性（ctypesのフィールド外）
            self.lyrics = kwargs.get('lyrics', '')
            self.duration = kwargs.get('duration', 0.5)
            self.note_number = kwargs.get('note_number', 60)
            self.phonemes = kwargs.get('phonemes', '')
            self.start_tick = kwargs.get('start_tick', 0)
    
    class PitchEvent:
        def __init__(self, time=0.0, pitch=0.0):
            self.time = time
            self.pitch = pitch
        
        def to_dict(self):
            return {'time': self.time, 'pitch': self.pitch}
        
        @staticmethod
        def from_dict(d):
            return PitchEvent(d.get('time', 0.0), d.get('pitch', 0.0))


# ==========================================================================
# 1.  ProかFreeかの判定ロジック(仮) 
# ==========================================================================

def execute_export_pro_manager(self):
    if LicenseManager.is_pro():
        sr = 96000
        bit = 32
    else:
        sr = 44100
        bit = 16
        # 代表の美学：制限しているのではなく「Free版の標準設定です」と見せる
        print(f"Exporting in Standard Quality ({sr}Hz/{bit}bit)...")


# ==========================================================================
# 1. 外部モジュール読み込み & フォールバック定義
# ==========================================================================
try:
    # 実際の運用環境用
    from .graph_editor_widget import GraphEditorWidget # type: ignore
except ImportError:
    # Actions (Pyright) および開発環境でのインポート失敗対策。
    # main_window.py から呼び出される全ての属性・メソッドを網羅。
    class _GraphEditorWidgetFallback(QWidget):
        pitch_data_updated = Signal(list)
        
        def __init__(self, parent: Optional[QWidget] = None): 
            super().__init__(parent)
            self.tempo: float = 120.0
            # all_parameters 属性を確実に保持
            self.all_parameters: Dict[str, Any] = {}
            # スクロールバー・表示関連のエラー対策
            self.scroll_x_offset: int = 0

        def set_pitch_events(self, events: Any) -> None: 
            pass

        def set_current_time(self, t: float) -> None: 
            pass

        # ログ2401行目対策: 横スクロールオフセット
        def set_horizontal_offset(self, val: int) -> None:
            pass

        # ログ3498行目 / _sample_range 対策
        def get_value_at_time(self, events: Any, t: float) -> float:
            return 0.5

        # ログ3549行目付近 / パラメータ更新メソッド
        def update_parameter(self, name: str, value: Any) -> None:
            pass

        # ログ5152行目付近 / データ一括取得
        def get_all_notes_data(self) -> List[Dict[str, Any]]:
            return []

        # モード切り替え（Pitch, Gender等）
        def set_mode(self, mode: str) -> None:
            pass
    GraphEditorWidget = cast(Any, _GraphEditorWidgetFallback)

# ==========================================================================
# 2. C++連携データ変換関数
# ==========================================================================
def prepare_c_note_event(python_note: dict) -> CNoteEvent:
    """
    UI上のノート情報(Dict)を、C++が解読可能な NoteEvent 構造体に変換する。
    返り値の構造体が参照する配列は、呼び出し元（SynthesisWorker）で保持される。
    """
    # 1. データの取得（デフォルト値で補完）
    pitch_data = python_note.get('pitch_curve') or [440.0]
    gender_data = python_note.get('gender_curve') or [0.5] * len(pitch_data)
    tension_data = python_note.get('tension_curve') or [0.5] * len(pitch_data)
    breath_data = python_note.get('breath_curve') or [0.0] * len(pitch_data)
    vibrato_depth = python_note.get('vibrato_depth_curve') or [0.0] * len(pitch_data)
    vibrato_rate = python_note.get('vibrato_rate_curve') or [5.5] * len(pitch_data)

    # 2. ctypes配列に変換
    pitch_arr = as_c_double_array(pitch_data)
    gender_arr = as_c_double_array(gender_data)
    tension_arr = as_c_double_array(tension_data)
    breath_arr = as_c_double_array(breath_data)
    vib_depth_arr = as_c_double_array(vibrato_depth)
    vib_rate_arr = as_c_double_array(vibrato_rate)

    # 3. 構造体の生成
    c_event = CNoteEvent()
    c_event.wav_path = python_note.get('phoneme', 'a').encode('utf-8')
    c_event.pitch_curve = ctypes.cast(pitch_arr, ctypes.POINTER(ctypes.c_double))
    c_event.pitch_length = len(pitch_data)
    c_event.gender_curve = ctypes.cast(gender_arr, ctypes.POINTER(ctypes.c_double))
    c_event.tension_curve = ctypes.cast(tension_arr, ctypes.POINTER(ctypes.c_double))
    c_event.breath_curve = ctypes.cast(breath_arr, ctypes.POINTER(ctypes.c_double))
    c_event.vibrato_depth_curve = ctypes.cast(vib_depth_arr, ctypes.POINTER(ctypes.c_double))
    c_event.vibrato_rate_curve = ctypes.cast(vib_rate_arr, ctypes.POINTER(ctypes.c_double))
    c_event.vibrato_curve_length = len(pitch_data)

    # Absolute timeline fields. Negative values preserve legacy/fallback behavior.
    raw_start = python_note.get("start_time")
    try:
        start_ms = float(raw_start) * 1000.0 if raw_start is not None else -1.0
    except (TypeError, ValueError):
        start_ms = -1.0
    c_event.start_time_ms = start_ms if np.isfinite(start_ms) and start_ms >= 0.0 else -1.0

    explicit_pre = bool(python_note.get("_ust_preutterance_explicit", False))
    explicit_ov = bool(python_note.get("_ust_overlap_explicit", False))
    c_event.preutterance_ms = (
        float(python_note.get("pre_utterance", 0.0))
        if explicit_pre and python_note.get("pre_utterance") is not None else -1.0
    )
    c_event.overlap_ms = (
        float(python_note.get("overlap", 0.0))
        if explicit_ov and python_note.get("overlap") is not None else -1.0
    )

    # 配列自体は呼び出し元（SynthesisWorker）で keep_alive に保持される
    return c_event



# ==========================================================================
# ハイブリッド・エンジン自動判別システム
# ==========================================================================

class EngineInitializer:
    def __init__(self):
        self.device = "CPU"
        self.provider = "CPUExecutionProvider"

    def detect_best_engine(self):
        """PCの性能をスキャンし、NPU/GPU/CPUから最適なものを選択する"""
        try:
            import onnxruntime as ort
            available = ort.get_available_providers()

            # 1. Mac (Apple Silicon) の NPU/GPU を優先
            if 'CoreMLExecutionProvider' in available:
                self.device = "NPU (Apple Silicon)"
                self.provider = "CoreMLExecutionProvider"
            
            # 2. Windows (DirectML) の NPU/GPU を優先
            elif 'DmlExecutionProvider' in available:
                self.device = "NPU/GPU (DirectML)"
                self.provider = "DmlExecutionProvider"

            # 3. どちらもなければ CPU で堅実に行く
            else:
                self.device = "CPU (High Performance Mode)"
                self.provider = "CPUExecutionProvider"

        except Exception:
            self.device = "CPU (Safe Mode)"
            self.provider = "CPUExecutionProvider"

        return self.device, self.provider

# MainWindowの初期化時にこれを呼び出す
# initializer = EngineInitializer()
# device_name, provider = initializer.detect_best_engine()
# self.statusBar().showMessage(f"Engine: {device_name} 起動完了")                                                                                


# ==========================================================================


# ==========================================================================
# マルチトラック・データ構造
# ==========================================================================


class VoseTrack:
    def __init__(self, name, track_type="vocal"):
        self.name = name
        self.track_type = track_type  # "vocal"（歌声） または "wave"（オケ）
        
        # --- 基本データ ---
        self.notes = []               # 歌声トラック用のノートリスト
        self.audio_path = ""          # オーディオトラック用のファイルパス
        self.vose_peaks = []          # タイムライン描画用の高速キャッシュ
        
        # --- 最高品質のための「ミキシング・パラメータ」 ---
        self.volume = 1.0             # 0.0 ~ 1.0 (音量)
        self.pan = 0.0                # -1.0 (左) ~ 1.0 (右)
        self.is_muted = False
        self.is_solo = False
        self.is_active = True
        
        # --- AI & エフェクト管理（将来の拡張用） ---
        self.engine_type = "Aural"     # このトラックに使うAIエンジンの種類
        self.effects = []              # リバーブやコンプレッサーの設定保持用
        self.playback_path = ""         # レンダー済み再生ファイル（Wave/ボーカル共通）
        self._playback_signature = ""
        self.color_label = "#64D2FF"   # UIで見分けるためのトラックカラー

    def to_dict(self):
        """保存用の辞書データ変換（全パラメータを網羅）"""
        return {
            "name": self.name,
            "type": self.track_type,
            "audio_path": self.audio_path,
            "volume": self.volume,
            "pan": self.pan,
            "is_muted": self.is_muted,
            "is_solo": self.is_solo,
            "engine_type": self.engine_type,
            "color_label": self.color_label,
            # ノートがオブジェクトなら辞書化、そうでなければそのまま
            "notes": [n.to_dict() if hasattr(n, 'to_dict') else n for n in self.notes]
        }


# ==========================================================
# 1. CreditsDialog クラス about画面
# ==========================================================
class CreditsDialog(QDialog):
    def __init__(self, partner_names=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("VO-SE Pro - About & Credits")
        self.setFixedSize(550, 650)
        self.setStyleSheet("background-color: #0d0d0d; color: #e0e0e0;")

        # 名前リストを受け取る（ID: 名前 の辞書形式）
        self.partner_names = partner_names if partner_names else {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)

        # --- ヘッダーエリア ---
        title = QLabel("VO-SE Pro")
        title.setFont(QFont("Segoe UI", 32, QFont.Weight.Bold))
        title.setStyleSheet("color: #00ffcc; letter-spacing: 2px;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        version = QLabel("Version 1.0.0 Alpha | Aura AI Engine Loaded") # エンジン名
        version.setFont(QFont("Consolas", 9))
        version.setStyleSheet("color: #666;")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)

        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #333; margin: 15px 0;")
        layout.addWidget(line)

        # --- パートナーセクション ---
        header_partner = QLabel("AURAL FOUNDING VOICE PARTNERS") # パートナーセクション名
        header_partner.setFont(QFont("Impact", 14))
        header_partner.setStyleSheet("color: #ff007f; margin-bottom: 5px;")
        layout.addWidget(header_partner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border: none; background: transparent;")
        
        container = QWidget()
        self.partners_layout = QVBoxLayout(container)
        self.partners_layout.setSpacing(8)

        # 10枠を生成
        for i in range(1, 11):
            slot = self.create_partner_row(i)
            self.partners_layout.addWidget(slot)

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # --- フッターエリア ---
        footer_line = QFrame()
        footer_line.setFrameShape(QFrame.Shape.HLine)
        footer_line.setStyleSheet("color: #333;")
        layout.addWidget(footer_line)

        dev_info = QLabel("Engineered by [Your Name]\n© 2026 VO-SE Project") # 2026年に更新
        dev_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dev_info.setStyleSheet("color: #444; font-size: 10px; margin-top: 10px;")
        layout.addWidget(dev_info)

    def create_partner_row(self, index):
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: #1a1a1a;
                border: 1px solid #2d2d2d;
                border-radius: 5px;
            }
            QFrame:hover {
                border: 1px solid #00ffcc;
            }
        """)
        row = QHBoxLayout(frame)
        
        id_lbl = QLabel(f"ID-{index:02}")
        id_lbl.setStyleSheet("color: #00ffcc; font-family: 'Consolas'; font-weight: bold;")
        
        # 動的な名前判定
        name = self.partner_names.get(index, "UNDER RECRUITMENT")
        is_recruiting = (name == "UNDER RECRUITMENT")
        
        name_lbl = QLabel(name)
        if is_recruiting:
            name_lbl.setStyleSheet("color: #444; font-style: italic; font-weight: bold;")
        else:
            name_lbl.setStyleSheet("color: #ffffff; font-weight: bold;") # 決まったら白く光らせる
        
        badge = QLabel("DYNAMICS READY")
        badge.setStyleSheet("""
            background-color: #000;
            color: #00ffcc;
            border: 1px solid #00ffcc;
            border-radius: 3px;
            font-size: 8px;
            padding: 2px 5px;
        """)

        row.addWidget(id_lbl)
        row.addWidget(name_lbl, 1)
        row.addWidget(badge)
        
        return frame

# ==========================================================================
# Undo/Redo コマンド管理
# ==========================================================================

class EditCommand:
    """操作一つ分を記録するクラス"""
    def __init__(self, redo_func, undo_func, description=""):
        self.redo_func = redo_func
        self.undo_func = undo_func
        self.description = description

    def redo(self):
        self.redo_func()

    def undo(self):
        self.undo_func()

class HistoryManager:
    """Undo/Redoのスタックを管理する"""
    def __init__(self, max_depth=50):
        self.undo_stack = []
        self.redo_stack = []
        self.max_depth = max_depth

    def execute(self, command):
        command.redo()
        self.undo_stack.append(command)
        self.redo_stack.clear() # 新しい操作をしたらRedoは消去
        if len(self.undo_stack) > self.max_depth:
            self.undo_stack.pop(0)

    def push(self, command):
        """
        既にUI上で実行済みの操作を、redo()を呼ばずに履歴へ積む。

        タイムライン上のドラッグ移動やダブルクリック追加のように、
        「ユーザー操作そのものが既に redo 相当の処理を行っている」場合に使う。
        execute() は内部で command.redo() を呼んでしまうため、
        そうした操作をそのまま execute() に渡すと処理が二重実行されてしまう。
        """
        self.undo_stack.append(command)
        self.redo_stack.clear()
        if len(self.undo_stack) > self.max_depth:
            self.undo_stack.pop(0)

    def undo(self):
        if not self.undo_stack:
            return
        command = self.undo_stack.pop()
        command.undo()
        self.redo_stack.append(command)

    def redo(self):
        if not self.redo_stack: 
            return
        command = self.redo_stack.pop()
        command.redo()
        self.undo_stack.append(command)


# ==========================================================================
# ---------- パフォーマンスモニター用のカスタムウィジェット ----------
# ==========================================================================

class PerformanceMonitorWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._parent_ref = parent
        self.setWindowTitle("パフォーマンスモニター")
        self.setMinimumSize(300, 150)
        
        layout = QVBoxLayout(self)
        
        # CPU
        self.cpu_label = QLabel("CPU: --%")
        layout.addWidget(self.cpu_label)
        self.cpu_bar = QProgressBar()
        self.cpu_bar.setRange(0, 100)
        layout.addWidget(self.cpu_bar)
        
        # メモリ
        self.mem_label = QLabel("メモリ: -- MB")
        layout.addWidget(self.mem_label)
        self.mem_bar = QProgressBar()
        self.mem_bar.setRange(0, 100)
        layout.addWidget(self.mem_bar)
        
        # オーディオバッファ
        self.buffer_label = QLabel("オーディオバッファ: --%")
        layout.addWidget(self.buffer_label)
        self.buffer_bar = QProgressBar()
        self.buffer_bar.setRange(0, 100)
        layout.addWidget(self.buffer_bar)
        
        # 更新タイマー
        self.timer = QTimer(self)
        self.timer.setInterval(1000)  # 1秒ごと
        self.timer.timeout.connect(self.update_stats)
        self.timer.start()
        
        self.setStyleSheet("""
            QProgressBar { text-align: center; height: 20px; }
            QProgressBar::chunk { background-color: #2f81f7; }
        """)
    
    def update_stats(self):
        # CPU
        if PSUTIL_AVAILABLE:
            cpu = psutil.cpu_percent()
            mem = psutil.virtual_memory()
            self.cpu_label.setText(f"CPU: {cpu:.1f}%")
            self.cpu_bar.setValue(int(cpu))
            self.mem_label.setText(f"メモリ: {mem.used // 1024 // 1024} MB / {mem.total // 1024 // 1024} MB")
            self.mem_bar.setValue(int(mem.percent))
        else:
            self.cpu_label.setText("CPU: psutil 未インストール")
            self.mem_label.setText("メモリ: psutil 未インストール")
        
        # オーディオバッファ（AudioOutput のキューサイズを参照）
        buffer_pct = 0
        if self._parent_ref and hasattr(self._parent_ref, 'audio_output'):
            ao = self._parent_ref.audio_output
            if hasattr(ao, 'buffer_queue') and hasattr(ao, 'maxsize'):
                qsize = ao.buffer_queue.qsize()
                maxsize = ao.maxsize if hasattr(ao, 'maxsize') else 64
                buffer_pct = int((qsize / maxsize) * 100) if maxsize > 0 else 0
                self.buffer_label.setText(f"オーディオバッファ: {qsize}/{maxsize} ブロック")
            else:
                self.buffer_label.setText("オーディオバッファ: 取得不可")
        else:
            self.buffer_label.setText("オーディオバッファ: 未接続")
        
        # バッファ不足警告（20%以下で赤色）
        if buffer_pct < 20:
            self.buffer_bar.setStyleSheet("QProgressBar::chunk { background-color: #ff4444; }")
        elif buffer_pct < 40:
            self.buffer_bar.setStyleSheet("QProgressBar::chunk { background-color: #ffaa44; }")
        else:
            self.buffer_bar.setStyleSheet("QProgressBar::chunk { background-color: #2f81f7; }")
        self.buffer_bar.setValue(buffer_pct)


# ==========================================================
#  Pro audio modeling レンダリングボタンを押さなくても、スペースキーで「今あるデータ」を合成して即座に鳴らす機能。
# ==========================================================
        
class ProMonitoringUI(QWidget):  
    def __init__(self, parent=None):
        super().__init__(parent)   # ← super().__init__ が必要
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.current_time = 0.0
        self.rms = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self.update)

    def start(self):
        self._timer.start()

    def stop(self):
        self._timer.stop()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 再生ヘッド（赤い縦線）
        x = int(self.current_time * 100)
        painter.setPen(QPen(QColor("#FF2D55"), 2))
        painter.drawLine(x, 0, x, self.height())

        # レベルメーター
        h = int(self.rms * 100)
        painter.fillRect(10, 110 - h, 10, h, QColor("#34C759"))
        painter.fillRect(25, 110 - h, 10, h, QColor("#34C759"))


class WorkerSignals(QObject):
    """合成ワーカーからメインスレッドへ送信するシグナル群"""
    finished = Signal(str)          # 生成されたWAVパス
    error = Signal(str)             # エラーメッセージ
    progress = Signal(int, float)   # (進捗率 0-100, 推定残り時間 秒)



class SynthesisWorker(QRunnable):
    """
    非同期レンダリングワーカー。
    ノートをチャンク分割し、進捗・ETA・キャンセルに対応する。
    """
    def __init__(
        self,
        vose_core,
        raw_notes,
        output_path,
        is_pro=False,
        parameters=None,
        tempo_bpm: Optional[float] = None,
    ):
        super().__init__()
        self.vose_core = vose_core
        self.raw_notes = raw_notes
        self.output_path = output_path
        self.is_pro = is_pro
        self.parameters = parameters or {}
        self.tempo_bpm = tempo_bpm
        self.signals = WorkerSignals()
        self._cancelled = False
        self._timer = QElapsedTimer()

    def cancel(self):
        """キャンセル要求をセットする（協調的キャンセル）"""
        self._cancelled = True

    def run(self):
        # 通常のデスクトップレンダーは VO_SE_Engine v2 を優先する。
        # v2 が無い古い環境だけ従来の C ABI 経路へフォールバックする。
        export_v2 = cast(
            Optional[Callable[..., str]],
            getattr(self.vose_core, "export_to_wav_v2", None),
        )
        if export_v2 is not None:
            try:
                if self._cancelled:
                    self.signals.error.emit("ユーザーによりキャンセルされました")
                    return
                self.signals.progress.emit(0, 0.0)
                result = export_v2(
                    self.raw_notes,
                    self.parameters,
                    self.output_path,
                    mode_flag=1 if self.is_pro else 0,
                    progress_callback=lambda percent: self.signals.progress.emit(int(percent), 0.0),
                    cancel_check=lambda: self._cancelled,
                    tempo_bpm=self.tempo_bpm,
                )
                if self._cancelled:
                    self.signals.error.emit("ユーザーによりキャンセルされました")
                    return
                if not isinstance(result, (str, bytes, os.PathLike)):
                    raise RuntimeError("レンダリング結果の WAV パスが不正です。")
                if not os.path.exists(result):
                    raise RuntimeError("レンダリング結果の WAV が生成されませんでした。")
                self.signals.progress.emit(100, 0.0)
                self.signals.finished.emit(os.fspath(result))
            except Exception as e:
                import traceback
                traceback.print_exc()
                self.signals.error.emit(str(e))
            return

        temp_dir = None
        try:
            total = len(self.raw_notes)
            if total == 0:
                self.signals.error.emit("レンダリングするノートがありません。")
                return

            # Absolute NoteEvent timing must remain in one core render call.
            # Splitting here would create independent timelines and destroy
            # preutterance/overlap relationships across chunk boundaries.
            chunk_size = total

            temp_dir = tempfile.mkdtemp(prefix="vose_render_")
            combined_audio = []

            self._timer.start()
            processed_count = 0

            for i in range(0, total, chunk_size):
                # --- キャンセルチェック ---
                if self._cancelled:
                    self.signals.error.emit("ユーザーによりキャンセルされました")
                    return

                chunk_notes = self.raw_notes[i:i + chunk_size]
                chunk_count = len(chunk_notes)

                # --- C言語構造体配列の生成 ---
                c_notes_array = (CNoteEvent * chunk_count)()
                keep_alive = []  # NumPy配列等のGC保護

                for idx, note_dict in enumerate(chunk_notes):
                    c_event = prepare_c_note_event(note_dict)
                    c_notes_array[idx] = c_event

                    # ★ 配列を保持する（GCから保護）
                    pitch_arr = as_c_double_array(note_dict.get('pitch_curve') or [440.0])
                    gender_arr = as_c_double_array(note_dict.get('gender_curve') or [0.5] * len(pitch_arr))
                    tension_arr = as_c_double_array(note_dict.get('tension_curve') or [0.5] * len(pitch_arr))
                    breath_arr = as_c_double_array(note_dict.get('breath_curve') or [0.0] * len(pitch_arr))
                    vib_depth_arr = as_c_double_array(note_dict.get('vibrato_depth_curve') or [0.0] * len(pitch_arr))
                    vib_rate_arr = as_c_double_array(note_dict.get('vibrato_rate_curve') or [5.5] * len(pitch_arr))
                    keep_alive.extend([pitch_arr, gender_arr, tension_arr, breath_arr, vib_depth_arr, vib_rate_arr])

                # --- チャンク出力パス ---
                chunk_path = os.path.join(temp_dir, f"chunk_{i:06d}.wav")

                # --- C++ エンジン実行 ---
                mode_flag = 1 if self.is_pro else 0
                self.vose_core.execute_render(
                    c_notes_array,
                    chunk_count,
                    chunk_path.encode('utf-8'),
                    mode_flag
                )

                # --- レンダリング結果の読み込み ---
                if os.path.exists(chunk_path):
                    data, sr = sf.read(chunk_path)
                    combined_audio.append(data)

                # --- 進捗・ETA更新 ---
                processed_count += chunk_count
                progress = int((processed_count / total) * 100)
                elapsed = self._timer.elapsed() / 1000.0
                eta = (elapsed / progress) * (100 - progress) if progress > 0 else 0.0
                self.signals.progress.emit(progress, eta)

            # --- キャンセルチェック ---
            if self._cancelled:
                self.signals.error.emit("ユーザーによりキャンセルされました")
                return

            # --- 全チャンク結合 ---
            if not combined_audio:
                self.signals.error.emit("レンダリングされたオーディオデータがありません。")
                return

            final_audio = np.concatenate(combined_audio)
            sf.write(self.output_path, final_audio, 44100)

            self.signals.finished.emit(self.output_path)

        except Exception as e:
            import traceback
            traceback.print_exc()
            self.signals.error.emit(str(e))
        finally:
            # ★★★ temp_dir が None でない場合のみ削除 ★★★
            if temp_dir is not None and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                except Exception:
                    pass

class AutoOtoEngine:
    """高度な音響特徴量（スペクトル・減衰率）を利用した適応型oto.iniジェネレーター"""

    def __init__(self, sample_rate=44100):
        self.sample_rate = sample_rate
        # ノイズフロア判定閾値（-50dB）
        self.noise_floor_db = -50.0

    def analyze_wav(self, file_path: str) -> Dict[str, float]:
        """
        WAVを解析し、音声学的に最適化されたotoパラメータを返す。
        戻り値: offset, preutter, overlap, constant, blank (全てms単位)
        """
        samples, sr = self._load_wav(file_path)
        if len(samples) == 0:
            return self._fallback_params()

        # 1. 基本エンベロープ（振幅）の計算
        envelope = self._get_envelope(samples, sr)
        
        # 2. 時間領域特徴量（ZCR）と周波数領域特徴量（スペクトル重心）の抽出
        zcr = self._get_zcr(samples, sr)
        spectral_centroid = self._get_spectral_centroid(samples, sr)
        
        # 3. 音の立ち上がり（Offset）の検出（従来の2%閾値を維持）
        offset_idx = self._detect_onset(envelope)
        offset_ms = (offset_idx / sr) * 1000.0

        # 4. ★核心改善①: 子音の種類をスペクトル重心とZCRの組み合わせで分類
        #   分析区間: 立ち上がりから 50ms 後までの領域
        analysis_end = min(offset_idx + int(sr * 0.05), len(samples))
        if analysis_end - offset_idx < 10:
            consonant_type = "unvoiced_stop"  # 極短い無声破裂音（デフォルト）
        else:
            # その区間の平均スペクトル重心（Hz）と平均ZCRを算出
            mean_centroid = np.mean(spectral_centroid[offset_idx:analysis_end])
            mean_zcr = np.mean(zcr[offset_idx:analysis_end])
            
            # 判定ロジック（日本語音声学に基づく経験則）
            if mean_centroid > 5500 and mean_zcr > 0.35:
                consonant_type = "fricative"      # 摩擦音 (s, sh, h, f)
            elif mean_centroid > 3000 and mean_zcr > 0.25:
                consonant_type = "affricate"       # 破擦音 (ts, ch, j)
            elif mean_centroid < 1500 and mean_zcr < 0.15:
                consonant_type = "voiced_stop"     # 有声破裂音 (b, d, g)
            else:
                consonant_type = "unvoiced_stop"   # 無声破裂音 (k, t, p) およびその他

        # 5. ★核心改善②: 先行発声（Preutterance）の決定
        #   母音が安定するポイント = スペクトル重心が急激に下降し、ZCRが閾値を下回る地点
        stable_vowel_idx = self._detect_vowel_stability(envelope, zcr, spectral_centroid, offset_idx, sr)
        preutter_ms = ((stable_vowel_idx - offset_idx) / sr) * 1000.0
        
        # 安全ガード（極端に短い/長い場合の補正）
        preutter_ms = np.clip(preutter_ms, 10.0, 350.0)

        # 6. ★核心改善③: 子音種別に応じたオーバーラップと固定範囲の最適値
        #   オーバーラップ = 先行発声の長さ × 子音別係数
        if consonant_type == "fricative":
            overlap_ratio = 0.25   # 摩擦音は重ねるとノイズが強調されるので短め
            constant_ratio = 1.2
        elif consonant_type == "affricate":
            overlap_ratio = 0.35
            constant_ratio = 1.4
        elif consonant_type == "voiced_stop":
            overlap_ratio = 0.45   # 有声破裂音は声帯振動が入るので長めに取る
            constant_ratio = 1.6
        else:  # unvoiced_stop / その他
            overlap_ratio = 0.4
            constant_ratio = 1.5

        overlap_ms = preutter_ms * overlap_ratio
        constant_ms = preutter_ms * constant_ratio

        # 7. ★核心改善④: 右ブランクを動的に計算（固定-10msを廃止）
        #   末尾から遡ってRMSがノイズフロアを超えなくなる地点を検出
        blank_ms = self._calc_dynamic_blank(samples, sr)

        return {
            "offset": int(offset_ms),
            "preutter": int(preutter_ms),
            "overlap": int(overlap_ms),
            "constant": int(constant_ms),
            "blank": int(blank_ms)  # 負の値を取ることも許容
        }

    # ========================================================================
    # 内部ヘルパーメソッド（詳細実装）
    # ========================================================================

    def _load_wav(self, path: str) -> Tuple[np.ndarray, int]:
        with wave.open(path, 'rb') as f:
            sr = f.getframerate()
            nch = f.getnchannels()
            frames = f.readframes(f.getnframes())
            data = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
            if nch == 2:
                data = data[::2]  # モノラル化
            return data, sr

    def _get_envelope(self, x: np.ndarray, sr: int) -> np.ndarray:
        """ヒルベルト変換による解析信号の包絡線（より正確な振幅変動）"""
        analytic = hilbert(x)  # type: ignore
        envelope: np.ndarray = np.abs(analytic) # type: ignore
        # 移動平均で平滑化（5ms窓）
        win = int(sr * 0.005)
        if win < 1:
            win = 1
        kernel = np.ones(win, dtype=np.float64) / win
        return np.convolve(envelope, kernel, mode='same')

    def _get_zcr(self, x: np.ndarray, sr: int) -> np.ndarray:
        """フレーム単位のゼロ交差率（5msフレーム） - ベクトル化版"""
        frame_len = int(sr * 0.005)
        if frame_len < 1:
            frame_len = 1
        n_frames = max(1, (len(x) - frame_len) // frame_len + 1)
        
        # 各フレームを抽出（ビュー）
        frames = np.lib.stride_tricks.sliding_window_view(x, frame_len)[::frame_len]
        frames = frames[:n_frames]  # 端数調整
        
        # ゼロ交差率をベクトル計算
        signs = np.sign(frames).astype(np.float64)
        diff_signs = np.abs(np.diff(signs, axis=1)) / 2.0
        zcr_frame = np.sum(diff_signs, axis=1) / frame_len
        
        # 元の長さに補間して戻す
        if len(zcr_frame) < 2:
            return np.zeros_like(x, dtype=np.float64)
        x_axis = np.linspace(0, len(x), len(zcr_frame))
        return np.interp(np.arange(len(x), dtype=np.float64), x_axis, zcr_frame.astype(np.float64))

    def _get_spectral_centroid(self, x: np.ndarray, sr: int) -> np.ndarray:
        """フレームごとのスペクトル重心（周波数の重み付き平均） - ベクトル化版"""
        frame_len = int(sr * 0.01)  # 10ms窓
        if frame_len < 1:
            frame_len = 1
        hop = max(1, frame_len // 2)
        n_frames = max(1, (len(x) - frame_len) // hop + 1)
        
        # フレーム抽出（ビュー）
        frames = np.lib.stride_tricks.sliding_window_view(x, frame_len)[::hop]
        frames = frames[:n_frames]
        
        # ハニング窓を適用
        window = np.hanning(frame_len).astype(np.float64)
        frames_windowed = frames * window
        
        # FFT（バッチ処理）
        fft_vals = np.abs(rfft(frames_windowed, axis=1)) # type: ignore
        freqs = rfftfreq(frame_len, d=1.0/sr).astype(np.float64)
        
        # 重心計算（分母がゼロの場合は0）
        sum_fft = np.sum(fft_vals, axis=1)
        centroid = np.zeros(len(sum_fft), dtype=np.float64)
        mask = sum_fft > 1e-6
        centroid[mask] = np.sum(freqs * fft_vals[mask], axis=1) / sum_fft[mask]
        
        # 元の長さに補間
        if len(centroid) < 2:
            return np.zeros_like(x, dtype=np.float64)
        x_axis = np.linspace(0, len(x), len(centroid))
        return np.interp(np.arange(len(x), dtype=np.float64), x_axis, centroid)

    def _detect_onset(self, envelope: np.ndarray) -> int:
        """振幅が最大値の2%を超える最初のインデックス"""
        if len(envelope) == 0:
            return 0
        threshold = float(np.max(envelope)) * 0.02
        mask = envelope > threshold
        idx = np.argmax(mask) if np.any(mask) else 0
        return int(idx)

    def _detect_vowel_stability(
        self,
        envelope: np.ndarray,
        zcr: np.ndarray,
        centroid: np.ndarray,
        onset_idx: int,
        sr: int
    ) -> int:
        """
        母音安定点の検出:
        1. 音量が最大値の70%以上に達したポイント
        2. ZCRが0.2を下回る
        3. スペクトル重心の変動率（微分）が最小になる地点
        """
        # 探索範囲（onset_idx から 300ms 後まで）
        search_end = min(onset_idx + int(sr * 0.3), len(envelope))
        search_len = search_end - onset_idx
        if search_len <= 0:
            return onset_idx

        # 各条件を配列で評価
        env_slice = envelope[onset_idx:search_end]
        zcr_slice = zcr[onset_idx:search_end]
        cent_slice = centroid[onset_idx:search_end]

        vol_condition = env_slice > (np.max(envelope) * 0.7)
        zcr_condition = zcr_slice < 0.2

        # 重心の変動が最小になるインデックス
        if len(cent_slice) > 1:
            cent_diff = np.abs(np.diff(cent_slice))
            stable_idx_in_range = int(np.argmin(cent_diff))
        else:
            stable_idx_in_range = search_len // 2

        # 3条件を合成（重心変動を最も重視）
        for i in range(search_len):
            idx = onset_idx + i
            if vol_condition[i] and zcr_condition[i] and i >= stable_idx_in_range:
                return idx

        # フォールバック
        return onset_idx + search_len // 2

    def _calc_dynamic_blank(self, x: np.ndarray, sr: int) -> float:
        """
        末尾のRMSを計算し、ノイズフロア（-50dB）を下回った時点を検出。
        その地点を「末尾からの距離」として負のブランク値を返す。
        """
        frame_len = int(sr * 0.01)
        if frame_len < 1:
            frame_len = 1
        # 末尾から遡る（最大1秒まで）
        search_len = min(len(x), sr * 1)
        if search_len < frame_len:
            return -5.0

        # 末尾からフレーム単位でRMSを計算（ベクトル化）
        n_frames = max(1, search_len // frame_len)
        rms_vals = np.zeros(n_frames, dtype=np.float64)
        for i in range(n_frames):
            start = len(x) - (i + 1) * frame_len
            end = len(x) - i * frame_len
            segment = x[start:end]
            if len(segment) > 0:
                rms_vals[i] = float(np.sqrt(np.mean(segment.astype(np.float64) ** 2)))

        # ノイズフロア（-50dB）を線形値に変換
        noise_floor_linear = float(10.0 ** (self.noise_floor_db / 20.0))

        # RMSがノイズフロアを下回る最初のインデックス
        below_noise = np.argmax(rms_vals < noise_floor_linear)
        if below_noise == 0 and rms_vals[0] > noise_floor_linear:
            return -10.0

        blank_ms = -(below_noise * frame_len / sr) * 1000.0
        return max(float(blank_ms), -200.0)

    def _fallback_params(self) -> Dict[str, float]:
        """WAV読み込み失敗時のデフォルト値"""
        return {"offset": 0, "preutter": 50, "overlap": 20, "constant": 70, "blank": -10}

    def generate_oto_text(self, wav_name: str, params: Dict[str, float]) -> str:
        """改良版パラメータをoto.ini一行形式に変換"""
        alias = os.path.splitext(wav_name)[0]
        return (f"{wav_name}={alias},"
                f"{params['offset']:.0f},{params['constant']:.0f},"
                f"{params['blank']:.0f},{params['preutter']:.0f},{params['overlap']:.0f}")

    
#----------
# 1. パス解決用の関数
#    (modules/gui/shared.py に移動。Mixin群と共有するためここではimportのみ)
#----------


try:
    # 既に冒頭でインポートしている、あるいはここで使わない場合は削除
    # もし動的にチェックしたいだけなら importlib を使うのが「製品」の作法です
    import importlib.util
    engine_exists = importlib.util.find_spec("gui.vo_se_engine") is not None
except ImportError:
    engine_exists = False


try:
    from .timeline_widget import TimelineWidget
except ImportError:
    class _TimelineWidgetFallback(QWidget):
        notes_changed_signal = Signal()
        def __init__(self): 
            super().__init__()
            self.notes_list = []
            self.tempo = 120
            self.key_height_pixels = 20
            self.pixels_per_beat = 40
            self.pixels_per_second = 50
            self.lowest_note_display = 21
            self._current_playback_time = 0.0
            self.note_color = "#FF0000"
            self.note_border_color = "#000000"
            self.text_color = "#FFFFFF"
        def get_notes_data(self): return self.notes_list
        def get_all_notes_data(self): return self.notes_list
        def get_all_notes(self): return self.notes_list
        def set_notes(self, notes): self.notes_list = notes
        def get_selected_notes_range(self): return (0.0, 10.0)
        def set_current_time(self, t): pass
        def set_recording_state(self, state, time): pass
        def delete_selected_notes(self): pass
        def set_vertical_offset(self, offset): pass
        def set_horizontal_offset(self, offset): pass
        def copy_selected_notes_to_clipboard(self): pass
        def paste_notes_from_clipboard(self): pass
        def get_max_beat_position(self): return 100
        def seconds_to_beats(self, sec): return sec * self.tempo / 60
        def beats_to_pixels(self, beats): return beats * self.pixels_per_beat
        def note_to_y(self, note_num): return (127 - note_num) * self.key_height_pixels
        def get_pitch_data(self): return []
        def get_audio_peaks(self, file_path, num_peaks=2000): return []
        def set_pitch_data(self, data): pass
        def add_note_from_midi(self, note_num, velocity): pass
        def update(self, *args, **kwargs): super().update()
    TimelineWidget = cast(Any, _TimelineWidgetFallback)

try:
    from .keyboard_sidebar_widget import KeyboardSidebarWidget
except ImportError:
    class _KeyboardSidebarWidgetFallback(QWidget):
        def __init__(self, height, lowest): super().__init__()
        def set_key_height_pixels(self, h): pass
        def set_vertical_offset(self, offset_pixels: int): pass
    KeyboardSidebarWidget = cast(Any, _KeyboardSidebarWidgetFallback)

try:
    from .midi_manager import load_midi_file, MidiInputManager # type: ignore
except ImportError:
    def load_midi_file(path): return []
    class MidiInputManager:
        def __init__(self, port): pass
        def start(self): pass
        def stop(self): pass


try:
    from .voice_manager import VoiceManager # type: ignore
except ImportError:
    class _VoiceManagerFallback:
        def __init__(self, ai):
            self.voices: Dict[str, Dict] = {}
            self.internal_voice_dir = "voice_banks"
        def first_run_setup(self): pass
        def get_current_voice_path(self): return "voice_banks/default"
        def run_batch_voice_analysis(self, dir, callback): return {}
        def scan_utau_voices(self): pass
        def install_voice_from_zip(self, path): return "NewVoice"
        def get_character_color(self, path): return "#4A90E2"
    VoiceManager = cast(Any, _VoiceManagerFallback)

try:
    import importlib
    AudioOutput = importlib.import_module("modules.audio.output").AudioOutput  # type: ignore[attr-defined]
except Exception:
    class AudioOutput:
        def __init__(self): pass
        def play_se(self, path): pass

try:
    from modules.backend.intonation import IntonationAnalyzer
except ImportError:
    class _IntonationAnalyzerFallback:
        def analyze(self, text): return []
        def parse_trace_to_notes(self, trace): return []
        def analyze_to_pro_events(self, text): return []
    IntonationAnalyzer = cast(Any, _IntonationAnalyzerFallback)

try:
    from modules.backend.audio_player import AudioPlayer
except ImportError:
    class _AudioPlayerFallback:
        def __init__(self, volume=0.8): pass
        def play_file(self, path): pass
        def play(self, data): pass
    AudioPlayer = cast(Any, _AudioPlayerFallback)

# DynamicsAIEngine の定義(動的import + フォールバック)は
# modules/gui/shared.py に切り出し済み。Mixin群とここで共有する。


# ==============================================================================
# 設定管理クラス（モック実装）
# ==============================================================================

class ConfigHandler:
    """設定ファイルの読み書き。保存時は一時ファイルから安全に置き換える。"""

    def __init__(self, config_path=None):
        if config_path is None:
            # Keep the GUI's settings path identical to main.py's ConfigHandler.
            # This matters because main.py also saves window.config after app.exec().
            if sys.platform == "win32":
                config_root = os.getenv("APPDATA") or os.path.expanduser("~")
                config_dir = os.path.join(config_root, "VO-SE Pro")
            elif sys.platform == "darwin":
                config_dir = os.path.expanduser(
                    "~/Library/Application Support/VO-SE Pro"
                )
            else:
                config_root = os.getenv("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
                config_dir = os.path.join(config_root, "vo-se-pro")
            self.config_path = os.path.join(config_dir, "config.json")
        else:
            self.config_path = os.fspath(config_path)

    def load_config(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                if not isinstance(config, dict):
                    raise ValueError("設定ファイルのルートはオブジェクトである必要があります。")
                return config
            except Exception as exc:
                print(f"設定読み込みエラー: {exc}")
        return {"default_voice": "標準ボイス", "volume": 0.8}

    def save_config(self, config: Dict[str, Any]) -> None:
        output_dir = os.path.dirname(os.path.abspath(self.config_path))
        os.makedirs(output_dir, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=".vose-config-",
            suffix=".tmp",
            dir=output_dir,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary_path, self.config_path)
        except Exception as exc:
            try:
                if os.path.exists(temporary_path):
                    os.remove(temporary_path)
            except OSError:
                pass
            print(f"設定保存エラー: {exc}")

# ==============================================================================
# ボイスカードウェイジェイト
# ==============================================================================

class VoiceCardWidget(QFrame):
    clicked = Signal()

    def __init__(self, display_name: str, icon_path: str, base_color: str,
                 is_recruiting: bool = False, category: str = "utau", parent=None):
        super().__init__(parent)

        # --- 1. 属性の代入 ---
        self.display_name = display_name
        self.is_recruiting = is_recruiting
        self.base_color = base_color
        # "official" | "utau" | "recruiting" — 検索/フィルター用の分類
        self.category = category
        self._search_key = display_name.lower()

        # --- 2. UIの基本設定 ---
        self.setFixedSize(124, 122)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.card_layout = QVBoxLayout(self)
        self.card_layout.setContentsMargins(8, 8, 8, 8)
        self.card_layout.setSpacing(6)

        # --- 3. アイコンエリアの構築 ---
        self.icon_label = QLabel()
        self.icon_label.setFixedSize(62, 62)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("background-color: rgba(0, 0, 0, 40); border-radius: 8px;")

        pixmap = QPixmap(icon_path)
        if pixmap.isNull():
            pixmap = QPixmap(110, 110)
            pixmap.fill(QColor(base_color).darker(150))

        self.icon_label.setPixmap(pixmap.scaled(
            110, 110,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation
        ))

        if self.is_recruiting:
            overlay_layout = QVBoxLayout(self.icon_label)
            overlay_layout.setContentsMargins(0, 0, 0, 0)
            self.recruit_text = QLabel("UNDER\nRECRUITMENT")
            self.recruit_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.recruit_text.setStyleSheet("""
                color: #00FFCC; font-weight: bold; font-size: 8px;
                background-color: rgba(0, 20, 20, 180); border-radius: 4px;
            """)
            overlay_layout.addWidget(self.recruit_text)

        self.card_layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignCenter)

        # --- 4. ラベルエリアの構築 ---
        self.name_label = QLabel(display_name)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setWordWrap(True)
        self.name_label.setStyleSheet(f"""
            color: {'#888' if is_recruiting else 'white'};
            font-weight: {'normal' if is_recruiting else 'bold'};
            font-size: 11px;
        """)
        self.card_layout.addWidget(self.name_label)

        self.set_selected(False)

    def matches(self, query: str, category_filter: str) -> bool:
        """検索語(部分一致・大小無視)とカテゴリフィルターの両方に合致するか判定する"""
        if category_filter != "all" and self.category != category_filter:
            return False
        if query and query.lower() not in self._search_key:
            return False
        return True

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            super().mousePressEvent(event)

    def set_selected(self, selected: bool):
        self._selected = selected
        border_color = "#00FFCC" if selected else "#333333"
        bg_color = self.base_color if not selected else QColor(self.base_color).lighter(120).name()
        opacity = "1.0" if not self.is_recruiting else "0.7"

        self.setStyleSheet(f"""
            VoiceCardWidget {{
                background-color: {bg_color};
                border: 2px solid {border_color};
                border-radius: 12px;
                opacity: {opacity};
            }}
            VoiceCardWidget:hover {{
                border: 2px solid #00FFCC;
                background-color: {QColor(bg_color).lighter(110).name()};
            }}
        """)


# ==============================================================================
# ボイスカードギャラリー（実音源優先 ＋ 募集枠を最下段に配置）
# ==============================================================================

class _VoiceImportWorker(QThread):
    """音源のコピー（数百MBになることがある）を UI を止めずに行うスレッド。"""
    succeeded = Signal(str)
    failed = Signal(str)

    def __init__(self, func, parent=None):
        super().__init__(parent)
        self._func = func

    def run(self):
        try:
            name = self._func()
        except Exception as e:  # noqa: BLE001 - 利用者へメッセージを返すため広く捕捉する
            self.failed.emit(str(e) or e.__class__.__name__)
            return
        self.succeeded.emit(str(name))


class VoiceCardGallery(QWidget):
    """
    音源カードを並べて表示するメインコンテナ。
    上部の検索バー・カテゴリチップで絞り込みができ、
    「音源を追加」から UTAU 音源（フォルダ / ZIP）を取り込める。
    カードの列数は表示幅に合わせて自動で変わる（右ドックのような狭い場所でも崩れない）。
    """
    voice_selected = Signal(str, str)  # (表示名, 内部ID)
    voice_imported = Signal(str)       # 取り込み完了（表示名）
    gallery_rebuilt = Signal()         # 一覧を作り直した（Voice 選択ボックスの更新用）
    clicked = Signal()

    CATEGORIES = [
        ("all", "すべて"),
        ("official", "内蔵"),
        ("utau", "UTAU音源"),
        ("recruiting", "募集枠"),
    ]

    CARD_WIDTH = 140
    GRID_SPACING = 14
    GRID_MARGIN = 16

    def __init__(self, voice_manager):
        super().__init__()

        self.manager = voice_manager
        self.cards = {}            # internal_id -> VoiceCardWidget
        self.partner_data = {}
        self._entries = []         # [internal_id, ...] 生成順（フィルター再配置に使う）
        self._active_category = "all"
        self._empty_label = None
        self._last_columns = None
        self._import_worker = None

        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(0, 0, 0, 0)
        self.main_layout.setSpacing(0)

        # --- 検索・フィルターバー ---
        self.main_layout.addWidget(self._build_filter_bar())

        # --- スクロールエリアとコンテナ ---
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setObjectName("GalleryScrollArea")
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setStyleSheet(
            "QScrollArea#GalleryScrollArea { border: none; background: transparent; }"
            "QWidget#GalleryContainer { background: transparent; }"
        )
        self.scroll_area.viewport().setAutoFillBackground(False)

        self.container = QWidget()
        self.container.setObjectName("GalleryContainer")
        self.grid = QGridLayout(self.container)
        self.grid.setSpacing(self.GRID_SPACING)
        self.grid.setContentsMargins(self.GRID_MARGIN, 8, self.GRID_MARGIN, 8)

        self.scroll_area.setWidget(self.container)
        self.scroll_area.viewport().installEventFilter(self)
        self.main_layout.addWidget(self.scroll_area, 1)

    def _build_filter_bar(self) -> QWidget:
        from modules.gui.flow_layout import FlowLayout
        from PySide6.QtWidgets import QMenu

        bar = QWidget()
        bar.setObjectName("GalleryFilterBar")
        outer = QVBoxLayout(bar)
        outer.setContentsMargins(12, 10, 12, 6)
        outer.setSpacing(8)

        # --- 1行目: 検索 ＋ 音源を追加 ---
        top = QHBoxLayout()
        top.setSpacing(8)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("SearchField")
        self.search_input.setPlaceholderText("音源を検索…")
        self.search_input.setClearButtonEnabled(False)  # 自前のクリアアクションを使う
        self.search_input.addAction(icon("search"), QLineEdit.ActionPosition.LeadingPosition)
        clear_action = self.search_input.addAction(icon("clear_x"), QLineEdit.ActionPosition.TrailingPosition)
        clear_action.triggered.connect(self.search_input.clear)
        self.search_input.textChanged.connect(lambda _: self._apply_filter())
        self.search_input.setMinimumWidth(80)
        top.addWidget(self.search_input, 1)

        self.import_button = QPushButton(icon("open_folder"), " 音源を追加")
        self.import_button.setObjectName("PrimaryButton")
        self.import_button.setToolTip(
            "UTAU音源を取り込みます。\noto.ini が入っているフォルダ、またはそのZIPを選んでください。"
        )
        menu = QMenu(self.import_button)
        act_folder = menu.addAction("UTAU音源のフォルダを選ぶ…")
        act_zip = menu.addAction("ZIPファイルから追加…")
        menu.addSeparator()
        act_rescan = menu.addAction("音源を再スキャン")
        act_folder.triggered.connect(self.import_voice_folder)
        act_zip.triggered.connect(self.import_voice_zip)
        act_rescan.triggered.connect(self.setup_gallery)
        self.import_button.setMenu(menu)
        top.addWidget(self.import_button)
        outer.addLayout(top)

        # --- 2行目: カテゴリチップ（幅が足りなければ折り返す） ---
        chips = FlowLayout(h_spacing=6, v_spacing=6)
        self.category_group = QButtonGroup(self)
        self.category_group.setExclusive(True)
        for key, label in self.CATEGORIES:
            chip = QPushButton(label)
            chip.setObjectName("FilterChip")
            chip.setCheckable(True)
            chip.setChecked(key == "all")
            chip.clicked.connect(lambda checked, k=key: self._on_category_selected(k))
            self.category_group.addButton(chip)
            chips.addWidget(chip)
        outer.addLayout(chips)

        return bar

    def _on_category_selected(self, key: str):
        self._active_category = key
        self._apply_filter()

    def _select_category(self, key: str):
        """プログラムからカテゴリチップを切り替える。"""
        for btn, (k, _label) in zip(self.category_group.buttons(), self.CATEGORIES):
            btn.setChecked(k == key)
        self._on_category_selected(key)

    def set_partner_data(self, partners: dict):
        self.partner_data = partners

    # ------------------------------------------------------------------
    # 音源の取り込み（フォルダ / ZIP）
    # ------------------------------------------------------------------
    def import_voice_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "UTAU音源のフォルダを選択（oto.ini が入っているフォルダ）")
        if not folder:
            return
        self._run_import(lambda: self.manager.install_voice_from_folder(folder),
                         "音源をコピーしています…")

    def import_voice_zip(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "UTAU音源のZIPを選択", "", "ZIPファイル (*.zip);;すべてのファイル (*)")
        if not path:
            return
        self._run_import(lambda: self.manager.install_voice_from_zip(path),
                         "音源を展開しています…")

    def _run_import(self, func, label: str):
        from PySide6.QtWidgets import QProgressDialog
        if self._import_worker is not None and self._import_worker.isRunning():
            return

        dlg = QProgressDialog(label, "", 0, 0, self.window())
        dlg.setWindowTitle("音源を追加")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setCancelButton(None)
        dlg.setMinimumDuration(0)
        dlg.show()

        worker = _VoiceImportWorker(func, self)
        worker.succeeded.connect(lambda name, d=dlg: self._on_import_done(name, d))
        worker.failed.connect(lambda msg, d=dlg: self._on_import_failed(msg, d))
        worker.finished.connect(worker.deleteLater)
        worker.finished.connect(lambda: setattr(self, "_import_worker", None))
        self._import_worker = worker
        worker.start()

    def _on_import_done(self, name: str, dlg):
        dlg.close()
        self.setup_gallery()
        self._select_category("utau")
        # 取り込んだ音源をそのまま選択状態にして、見える位置までスクロールする
        voice_path = self.manager.voices.get(name)
        if isinstance(voice_path, dict):
            voice_path = voice_path.get("path")
        if voice_path and voice_path in self.cards:
            self.on_card_clicked(name, voice_path)
            self.scroll_area.ensureWidgetVisible(self.cards[voice_path])
        self.voice_imported.emit(name)

    def _on_import_failed(self, message: str, dlg):
        dlg.close()
        QMessageBox.warning(self.window(), "音源を追加できませんでした", message)

    # ------------------------------------------------------------------
    # 一覧の構築
    # ------------------------------------------------------------------
    def setup_gallery(self):
        """全音源を再スキャンしてカードを作り直す（音源追加/削除があった時に呼ぶ）"""
        selected_id = None
        for internal_id, card in self.cards.items():
            if getattr(card, "_selected", False):
                selected_id = internal_id
        if self.grid is not None:
            while self.grid.count() > 0:
                item = self.grid.takeAt(0)
                if item is not None:
                    widget = item.widget()
                    if widget is not None:
                        widget.setParent(None)
                        widget.deleteLater()

        self.cards.clear()
        self._entries.clear()

        all_voices = self.manager.scan_voices()

        for display_name, internal_id in all_voices.items():
            if internal_id.startswith("__INTERNAL__"):
                id_parts = internal_id.split(":", 1)
                char_dir = id_parts[1] if len(id_parts) > 1 else display_name
                base_path = getattr(self.manager, 'base_path', os.getcwd())
                icon_path = os.path.join(base_path, "assets", "official_voices", char_dir, "icon.png")
                card_color = "#3A3A4A"
                category = "official"
            else:
                icon_path = os.path.join(internal_id, "icon.png")
                card_color = "#2D2D2D"
                category = "utau"

            card = VoiceCardWidget(display_name, icon_path, card_color,
                                    is_recruiting=False, category=category)
            self._register_card(card, display_name, internal_id)

        loop_range = self.partner_data.keys() if self.partner_data else range(1, 11)
        for i in loop_range:
            display_name = f"PARTNER ID-{i:02d}"
            internal_id = f"__RECRUITING__:ID-{i:02d}"
            base_path = getattr(self.manager, 'base_path', os.getcwd())
            icon_path = os.path.join(base_path, "assets", "icons", "recruiting_placeholder.png")
            card_color = "#1A2222"
            card = VoiceCardWidget(display_name, icon_path, card_color,
                                    is_recruiting=True, category="recruiting")
            self._register_card(card, display_name, internal_id)

        if selected_id in self.cards:
            self.cards[selected_id].set_selected(True)

        self._apply_filter()
        self.gallery_rebuilt.emit()

    def _register_card(self, card, display_name, internal_id):
        card.clicked.connect(
            lambda d=display_name, i=internal_id: self.on_card_clicked(d, i)
        )
        self.cards[internal_id] = card
        self._entries.append(internal_id)

    def _max_columns(self) -> int:
        """表示幅に収まるカードの列数（最低 1）。"""
        if not hasattr(self, "scroll_area"):
            return 1
        width = self.scroll_area.viewport().width()
        # 縦スクロールバーが出ても出なくても同じ列数になるよう、常にバー分を引いておく
        # （出入りで列数が行き来して再配置が繰り返されるのを防ぐ）
        vbar = self.scroll_area.verticalScrollBar()
        if not vbar.isVisible():
            width -= vbar.sizeHint().width()
        avail = max(0, width - 2 * self.GRID_MARGIN)
        return max(1, (avail + self.GRID_SPACING) // (self.CARD_WIDTH + self.GRID_SPACING))

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if (hasattr(self, "scroll_area") and obj is self.scroll_area.viewport()
                and event.type() == QEvent.Type.Resize and self.cards
                and self._max_columns() != self._last_columns):
            QTimer.singleShot(0, self._apply_filter)
        return super().eventFilter(obj, event)

    def _apply_filter(self):
        """検索語・カテゴリに合致するカードだけをグリッドに再配置する（再スキャンなし）"""
        query = self.search_input.text().strip() if hasattr(self, 'search_input') else ""

        # 1. グリッドから全ウィジェットを外す（削除はしない・使い回す）
        while self.grid.count() > 0:
            item = self.grid.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)
                    if widget.objectName() == "GalleryEmptyState":
                        widget.deleteLater()

        # 2. 合致するカードだけ拾う
        visible_ids = [
            internal_id for internal_id in self._entries
            if internal_id in self.cards and self.cards[internal_id].matches(query, self._active_category)
        ]

        max_columns = self._max_columns()
        self._last_columns = max_columns
        for i in range(max_columns + 1):
            self.grid.setColumnStretch(i, 0)

        if not visible_ids:
            if self._active_category == "utau" and not query:
                text = ("UTAU音源がまだありません。\n"
                        "右上の「音源を追加」から、oto.ini の入ったフォルダかZIPを選んでください。")
            else:
                text = "該当する音源が見つかりません"
            empty = QLabel(text)
            empty.setObjectName("GalleryEmptyState")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            self.grid.addWidget(empty, 0, 0, 1, max_columns)
            return

        row, col = 0, 0
        for internal_id in visible_ids:
            card = self.cards[internal_id]
            card.setParent(self.container)
            card.setVisible(True)
            self.grid.addWidget(card, row, col)
            col += 1
            if col >= max_columns:
                col = 0
                row += 1

        # 余った幅は右側の空白に回す（カードを引き伸ばさない）
        self.grid.setColumnStretch(max_columns, 1)
        self.grid.setRowStretch(row + 1, 1)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            super().mousePressEvent(event)

    def on_card_clicked(self, name, internal_id):
        for card_widget in self.cards.values():
            card_widget.set_selected(False)

        if internal_id in self.cards:
            self.cards[internal_id].set_selected(True)

        print(f"DEBUG: Gallery selection -> {name} ({internal_id})")
        self.voice_selected.emit(name, internal_id)


# ==============================================================================
# バックグラウンドスレッド
# ==============================================================================

class AnalysisThread(QThread):
    """AI解析をバックグラウンドで実行するスレッド"""
    progress = Signal(int, str)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, voice_manager, target_dir):
        super().__init__()
        self.voice_manager = voice_manager
        self.target_dir = target_dir

    def run(self):
        try:
            results = self.voice_manager.run_batch_voice_analysis(
                self.target_dir,
                self.progress.emit
            )
            self.finished.emit(results)
        except Exception as e:
            self.error.emit(str(e))


# ==============================================================================
# メインウィンドウクラス
# ==============================================================================

class _VocalPlaybackPreparationThread(QThread):
    """Render playback WAVs off the GUI thread without touching Qt widgets."""

    completed = Signal(object)
    failed = Signal(object)
    progress = Signal(str)

    def __init__(self, engine: Any, jobs: List[Dict[str, Any]], parent=None):
        super().__init__(parent)
        self.engine = engine
        self.jobs = jobs
        self._cancel_event = threading.Event()

    def request_cancel(self) -> None:
        """Ask the preparation loop and native renderer to stop safely."""
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        current_track = None
        try:
            export_v2 = getattr(self.engine, "export_to_wav_v2", None)
            needs_render = any(job["notes"] for job in self.jobs)
            if needs_render and not callable(export_v2):
                raise RuntimeError("VCV対応の export_to_wav_v2 が利用できません。")

            results = []
            for job in self.jobs:
                if self._cancel_event.is_set():
                    raise RuntimeError("再生準備がキャンセルされました")
                track = job["track"]
                current_track = track
                track_index = int(job["index"])
                notes = job["notes"]
                if not notes:
                    results.append((track, "", ""))
                    continue

                note_state = []
                for note in notes:
                    to_dict = getattr(note, "to_dict", None)
                    note_state.append(to_dict() if callable(to_dict)
                                      else getattr(note, "__dict__", repr(note)))

                signature = repr((
                    note_state,
                    job["parameters"],
                    job["voice_id"],
                    float(job["tempo"]),
                ))
                previous_path = str(job.get("previous_path") or "")
                if (job.get("previous_signature") == signature
                        and previous_path and os.path.isfile(previous_path)):
                    results.append((track, previous_path, signature))
                    self.progress.emit(f"Vocal {track_index + 1}: キャッシュを使用")
                    continue

                cache_dir = os.path.join(tempfile.gettempdir(), "vose_playback_cache")
                os.makedirs(cache_dir, exist_ok=True)
                import hashlib
                cache_key = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:24]
                cache_path = os.path.join(
                    cache_dir, f"track_{track_index}_{cache_key}.wav"
                )

                self.progress.emit(
                    f"Vocal {track_index + 1} をバックグラウンドでレンダリング中..."
                )
                # Narrow the optional engine method before calling it.
                if not callable(export_v2):
                    raise RuntimeError("VCV対応の export_to_wav_v2 が利用できません。")
                result = export_v2(
                    notes,
                    job["parameters"],
                    cache_path,
                    mode_flag=0,
                    cancel_check=self._cancel_event.is_set,
                )
                if not result:
                    raise RuntimeError(
                        f"Vocal {track_index + 1} のWAV生成に失敗しました。"
                    )
                result_path = os.path.abspath(str(result))
                if not os.path.isfile(result_path):
                    raise RuntimeError(
                        f"Vocal {track_index + 1} のWAV生成に失敗しました。"
                    )
                results.append((track, result_path, signature))

            self.completed.emit(results)
        except Exception as exc:
            self.failed.emit({
                "message": str(exc),
                "track": current_track,
            })


class MainWindow(
    ProjectIOMixin,
    AudioOutputMixin,
    VoiceManagerMixin,
    VoiceManagementMixin,   
    QMainWindow
):

    """VO-SE Pro  メインウィンドウ"""
    
    # === メインUIウィジェット系（遅延生成 → Optional） ===
    timeline_widget: Any
    graph_editor_widget: Any
    keyboard_sidebar: Any
    keyboard_sidebar_widget: Any

    # === スクロール・ボリュームUI ===
    vertical_scroll: Any
    v_scrollbar: Any
    h_scrollbar: Any
    vol_slider: Any
    vol_label: Any

    # === タイマー（__init__で必ず実体化） ===
    render_timer: QTimer
    playback_timer: QTimer

    # === 再生・音声系 ===
    player: Optional[Any]
    audio_player: Any
    audio_output: Any
    audio_mixer: Any

    # === AI / エンジン系（実体保証できないため Any） ===
    vo_se_engine: Any
    vose_core: Optional[Any]
    dynamics_ai: Any
    voice_manager: Any
    analyzer: Any
    talk_manager: Any
    text_analyzer: Optional[Any]

    # === 再生状態フラグ ===
    is_playing_state: bool
    is_playing: bool
    is_recording: bool
    is_looping: bool
    is_looping_selection: bool

    # === トラック・データ管理 ===
    current_track_idx: int
    tracks: List[Any]
    notes: List[Any]
    pitch_data: List[Any]
    playing_notes: Dict[int, Any]

    oto_dict: Dict[str, Any]
    current_oto_data: Dict[str, Any]

    current_voice: str
    volume: float
    current_playback_time: float

    # === UIコントロール ===
    tempo_input: Any
    play_button: Any
    play_btn: Any
    record_button: Any
    loop_button: Any
    render_button: Any

    btn_mute: Any
    btn_solo: Any

    track_list_widget: Any
    progress_bar: Any
    status_label: Any

    character_selector: Any
    midi_port_selector: Any

    toolbar: Any
    main_layout: Any
    voice_grid: Any
    voice_cards: List[Any]

    # === 描画・キャンバス ===
    canvas: Any
    piano_roll_scene: Any

    # === スレッド・排他制御 ===
    playback_thread: Optional[threading.Thread]
    analysis_thread: Any
    _playback_lock: threading.Lock

    # === 履歴・設定 ===
    history: Any
    config_manager: Any
    config: Any

    # === パラメータ管理 ===
    input_fields: List[Any]
    parameters: Dict[str, Any]
    all_parameters: Dict[str, Any]
    sync_notes: bool

    vowel_groups: Dict[str, str]
    confirmed_partners: Dict[int, str]

    # === デバイス情報 ===
    active_device: str
    active_provider: str
    device_status_label: Any
    ai_manager: Any

    def __init__(self, parent=None, engine=None, ai=None, config=None):
        super().__init__(parent)

        self.vol_slider = None
        self.vol_label = None
        self.timeline_widget = cast(Any, None)
        self.graph_editor_widget = cast(Any, None)
        self.voice_manager = cast(Any, None) 

        self.status_label = QLabel("")
        self.voice_grid = QGridLayout()
        
        self.status_bar = self.statusBar()

        self.toolbar = QToolBar("Main Toolbar")
        self.addToolBar(self.toolbar)

        
        # --- 2. 属性の初期化（AttributeError 対策） ---
        self._init_attributes(engine, ai, config)
        
        # --- 3. エンジンの実体化（ImportError ガード付き） ---
        self._init_engines(engine, ai)

        # オーディオ再生エンジン(self.player)の構築。
        # 以前はここが呼ばれておらず、合成結果を再生しようとしても
        # 「プレイヤーが初期化されていません」という警告だけが出て
        # 無音のまま何も起きない状態だった。
        self.setup_audio_interface()

        # --- 4. UI構築と起動シーケンス ---
        self.init_ui()
        self.setup_connections()
        self.setup_vose_shortcuts()
        self.setup_vose_keyboard_navigation()

        # パフォーマンスモード切替トグルをツールバーに追加。
        # setup_toolbar (init_ui内) の後である必要がある。
        self.setup_performance_toggle()

        # ---- キャンセルボタン ----
        self.cancel_render_btn = QPushButton("✖ キャンセル")
        self.cancel_render_btn.setObjectName("DangerButton")
        self.cancel_render_btn.setVisible(False)   # ← 初期は非表示
        self.cancel_render_btn.setEnabled(False)
        self.cancel_render_btn.clicked.connect(self.cancel_current_render)
        self.toolbar.addWidget(self.cancel_render_btn)

        # ETAラベル（ステータスバー）
        self.render_eta_label = QLabel("")
        self.statusBar().addPermanentWidget(self.render_eta_label)

        # 実行中ワーカーの参照とキャンセル状態
        self.current_worker: Optional["SynthesisWorker"] = None
        self._render_cancelled = False

        # リアルタイムモニタリングの有効化。
        self.setup_realtime_monitoring()

        self.perform_startup_sequence()

        # AIダイナミクス推論セッション(self.ai_session)の構築。
        # active_provider の診断が終わった後である必要があるため
        # perform_startup_sequence の後に呼ぶ。
        # 以前はこれが呼ばれておらず、predict_dynamics() を呼ぶと
        # self.ai_session が存在せず AttributeError になっていた。
        self.setup_aural_ai()

        self.setWindowTitle("VO-SE vocal")
        self.resize(1200, 800)

    def _init_attributes(self, engine: Any, ai: Any, config: Any):
        """
        すべての属性に初期値を代入。
        (Pylance の reportAttributeAccessIssue を根絶する完全版)
        """
        # --- 1. Required宣言されているものへの代入 (Anyキャストで矛盾回避) ---
        self.timeline_widget = cast(Any, None)
        self.graph_editor_widget = cast(Any, None)
        self.keyboard_sidebar = cast(Any, None)
        self.keyboard_sidebar_widget = cast(Any, None)
        self.vertical_scroll = cast(QSlider, None)
        self.v_scrollbar = cast(QSlider, None)
        self.h_scrollbar = cast(QScrollBar, None)
        self.vol_slider = cast(QSlider, None)
        self.vol_label = cast(QLabel, None)
        
        # --- 2. Optional宣言またはAny型のもの ---
        self.player = None
        self.talk_manager = None   # ← 追加
        self.midi_manager = None  
        self.audio_output = None
        self.audio_player = None
        self.audio_mixer = None
        self.vose_core = None
        self.text_analyzer = None
        self.playback_thread = None
        self.analysis_thread = cast(QThread, None)
        
        # エンジン類
        self.vo_se_engine = engine
        self.dynamics_ai = ai
        self.voice_manager = None
        self.analyzer = None
        
        # 状態フラグ
        self.is_playing_state = False
        self.is_playing = False
        self.is_recording = False
        self.is_looping = False
        self.is_looping_selection = False
        
        # データリスト・辞書
        self.tracks = []
        self.notes = []
        self.pitch_data = []
        self.playing_notes = {}
        self.oto_dict = {}
        self.current_oto_data = {}
        
        self.current_track_idx = 0
        self.selected_index = -1 
        self.current_voice = "標準ボイス"
        self.volume = 0.8
        self.current_playback_time = 0.0
        self.playback_start_time = 0.0
        self.playback_end_time = 0.0
        self.playback_started_monotonic = 0.0
        
        # UIポインタ (Optional群)
        self.tempo_input = cast(QLineEdit, None)
        self.time_display_label = cast(QLabel, None)
        self.play_button = cast(QPushButton, None)
        self.play_btn = cast(QPushButton, None)
        self.record_button = cast(QPushButton, None)
        self.loop_button = cast(QPushButton, None)
        self.render_button = cast(QPushButton, None)
        self.status_label = cast(QLabel, None)
        self.btn_mute = cast(QPushButton, None)
        self.btn_solo = cast(QPushButton, None)
        self.track_list_widget = cast(QListWidget, None)
        self.progress_bar = cast(QProgressBar, None)
        self.character_selector = cast(QComboBox, None)
        self.midi_port_selector = cast(QComboBox, None)
        self.toolbar = cast(QToolBar, None)
        self.device_status_label = cast(QLabel, None)
        self.main_layout = cast(QVBoxLayout, None)
        self.voice_grid = cast(QGridLayout, None)
        self.voice_cards = []
        self.canvas = None
        self.piano_roll_scene = None

        # タイマーはここで実体化させる (Noneアクセスを未然に防ぐ)
        self.render_timer = QTimer(self)
        self.playback_timer = QTimer(self)
        # 再生位置UIは約30fpsで十分。start()単体だと既定の0ms間隔になり、
        # イベントループを不必要に占有するため明示的に設定する。
        self.playback_timer.setInterval(33)
        
        # ロック
        import threading
        self._playback_lock = threading.Lock()
        self._playback_prepare_thread = None
        self._pending_playback_tracks = None
        self._pending_playback_start_time = 0.0
        self._playback_preparing = False
        
        # 外部マネージャー
        self.history = HistoryManager()
        self.config_manager = cast(Any, ConfigHandler())
        loaded_config = self.config_manager.load_config()
        self.config = dict(config) if isinstance(config, dict) else loaded_config
        self.all_parameters = {}
        self.sync_notes = True
        self.input_fields = []
        self.ai_manager = None
        self.parameters = {}
        
        self.vowel_groups = {
            'a': 'あかさたなはまやらわがざだばぱぁゃ',
            'i': 'いきしちにひみりぎじぢびぴぃ',
            'u': 'うくすつぬふむゆるぐずづぶぷぅゅ',
            'e': 'えけせてねへめれげぜでべぺぇ',
            'o': 'おこそとのほもよろをごぞどぼぽぉょ',
            'n': 'ん'
        }
        self.confirmed_partners = {}
        self.active_device = "CPU (Standard)"
        self.active_provider = "CPUExecutionProvider"
        
        self.timeline_widget = cast(Any, None)
        self.timeline = cast(Any, None)  # ★ setup_main_editor_area() の末尾でエイリアス設定
        self.voice_gallery = cast(Any, None)
        self.current_voice_id = "__INTERNAL__:standard"

        self.confirmed_partners = {i: "UNDER RECRUITMENT" for i in range(1, 11)}
        
        # 現在選択されているボイス情報
        self.current_voice = "未選択"
        self.current_voice_id = "NONE"

    def _init_engines(self, engine, ai):
        """
        [VO-SE Pro: Core Integration Complete Edition]
        全てのエンジンとマネージャーを初期化します。
        DLLやモジュールが欠落していても、Mockオブジェクトにより起動を阻止しません。
        """
        # --- 1. VOSE Core Engine (C++ DLL) の統合 ---
        # 旧 VoseEngine() を廃止し、シングルトンから取得します
        self.vose_core = vose_manager.get_lib()
        
        if not self.vose_core:
            # DLLが読み込めない場合のセーフティネット
            class MockCore:
                def __getattr__(self, name):
                    def _missing_core(*args, **kwargs):
                        raise RuntimeError(
                            f"VOSE Core がロードされていないため {name} を実行できません。"
                        )
                    return _missing_core
            self.vose_core = MockCore()
            print("⚠️ VOSE Core DLL is missing. Rendering is disabled.")
        else:
            self.statusBar().showMessage("VOSE Core Engine: Online", 3000)

        # --- 2. モジュール・ロード用ヘルパー ---
        def safe_import(module_path, class_name, fallback_class):
            try:
                import importlib
                mod = importlib.import_module(module_path)
                return getattr(mod, class_name)
            except (ImportError, AttributeError) as e:
                print(f"Engine Load Warning: {module_path}.{class_name} not found. ({e})")
                return fallback_class

        # --- 3. Mockクラスの定義（実体がない場合の身代わり） ---
        class MockDynamicsAI:
            def generate_emotional_pitch(self, f0, strength=0.8): return f0
            def get_baked_pitch(self, nid, f0, s=0.8): return f0

        class MockVoiceManager:
            def __init__(self, ai=None): self.voices = {}
            def get_current_voice_path(self): return ""
            def scan_utau_voices(self): pass
            def get_character_color(self, path): return "#4A90E2"

        class MockAudioPlayer:
            def __init__(self, volume=0.8): self.vol = volume
            def play_file(self, path): print(f"Mock Play: {path}")
            def stop(self): pass

        # --- 4. 実体化プロセスの実行 ---
        # AIエンジン
        DynamicsAIEngine = safe_import("modules.utils.dynamics_ai", "DynamicsAIEngine", MockDynamicsAI)
        self.dynamics_ai = ai if ai else DynamicsAIEngine()

        # ボイスマネージャー
        VoiceManager = safe_import("modules.audio.voice_manager", "VoiceManager", MockVoiceManager)
        try:
            self.voice_manager = cast(Any, VoiceManager(self.dynamics_ai))
        except TypeError:
            self.voice_manager = cast(Any, VoiceManager())

        # 実デスクトップ再生は setup_audio_interface() で構築する
        # AudioMixer に統一する。ここでは旧 AudioPlayer を生成して
        # 別の再生経路を残さない。

        # トーク解析系（Talk機能用）
        IntonationAnalyzer = safe_import("modules.talk.talk_manager", "IntonationAnalyzer", lambda: None)
        self.analyzer = IntonationAnalyzer() if IntonationAnalyzer else None
        self.text_analyzer = self.analyzer

        # --- 5. プロジェクト・パートナー枠の定義 ---
        # 代表の指定通り、ここでID管理を確定させます
        self.confirmed_partners = {
            1: "UNDER RECRUITMENT", # ID-01
            2: "UNDER RECRUITMENT", # ID-02
            3: "UNDER RECRUITMENT", # ID-03
        }
        
        print("✅ Startup Sequence: All engines initialized.")
        

    def get_all_actions(self) -> list:
        """アプリ内の全QActionを検出し、名前が空のオブジェクトに対して一意の識別名を動的生成・付与する"""
        import re
        from PySide6.QtGui import QAction
        
        actions = []
        for action in self.findChildren(QAction):
            text = action.text().strip()
            # 内部セパレータや文字のないシステムアクションはスキップ
            if not text:
                continue
            
            # 🌟 致命的バグの修正：objectName が無ければ、テキストから安全な識別子を生成して注入する
            key = action.objectName()
            if not key:
                # 記号や空白を除去して安全な英数記号文字列にする
                sanitized = re.sub(r'[^\w]', '', text)
                key = f"action_{sanitized}"
                action.setObjectName(key)  # その場で確定させることで、保存・復元キーの不一致を完全防止
                
            actions.append((text, action))
        return actions

    def open_shortcut_settings(self) -> None:
        """ショートカット設定ダイアログを展開する"""
        from modules.gui.settings_dialog import ShortcutSettingsDialog
        dlg = ShortcutSettingsDialog(self)
        dlg.exec()

    def load_shortcut_settings(self) -> None:
        """起動時にQSettingsからすべてのカスタムショートカットを完全に復元する"""
        from PySide6.QtCore import QSettings
        from PySide6.QtGui import QKeySequence
        from PySide6.QtGui import QAction
        
        # 1. 復元前にアプリ内の全QActionのobjectNameのネームスペースを確定させる
        self.get_all_actions()
        
        # 2. 保存データを巡回してショートカットをマッピング
        settings = QSettings("VO-SE", "vocal")
        for action in self.findChildren(QAction):
            key = f"shortcuts/{action.objectName()}"
            if settings.contains(key):
                saved_value = settings.value(key)
                action.setShortcut(QKeySequence(str(saved_value)))
                
    def execute_render(self):
        """現在のデスクトップ用レンダリング処理へ委譲する。"""
        render_fn = getattr(self, "on_render_button_clicked", None)
        if not callable(render_fn):
            raise RuntimeError("レンダリング処理が利用できません。")
        return render_fn()

    def toggle_recording(self):
        """録音トグルの互換エントリポイント。"""
        record_handler = cast(Callable[[], Any], self.on_record_toggled)
        return record_handler()

    def update_playback_ui(self):
        """再生位置・時間表示・タイムラインのプレイヘッドを同期する。"""
        if getattr(self, 'is_playing', False):
            mixer = getattr(self, "audio_mixer", None)

            # AudioMixer は全バッファ終了時に CallbackStop で自然停止する。
            # ループ中はここで再スタートし、通常再生時だけ停止処理へ進む。
            # 先に stop_and_clear_playback() を呼ぶとループ状態まで解除されるため、
            # 自然終了とユーザー操作による停止を明確に分ける。
            if mixer is not None and not bool(getattr(mixer, "is_playing", False)):
                if getattr(self, "is_looping", False):
                    try:
                        mixer.play(0.0)
                        self.current_playback_time = 0.0
                        self.playback_start_time = 0.0
                        self.playback_started_monotonic = time.monotonic()
                        self._set_transport_time(0.0)
                        return
                    except Exception as exc:
                        self.statusBar().showMessage(
                            f"ループ再生の再開に失敗しました: {exc}", 5000
                        )
                self.stop_and_clear_playback()
                return

            elapsed = max(0.0, time.monotonic() - float(getattr(self, 'playback_started_monotonic', 0.0)))
            current_time = float(getattr(self, 'playback_start_time', 0.0)) + elapsed
            end_time = max(float(getattr(self, 'playback_end_time', 0.0)), self._get_project_duration_seconds())

            if end_time > 0.0 and current_time >= end_time:
                if getattr(self, 'is_looping', False):
                    current_time = 0.0
                    self.playback_start_time = 0.0
                    self.playback_started_monotonic = time.monotonic()
                else:
                    self.stop_and_clear_playback()
                    return

            self._set_transport_time(current_time)
        else:
            self._set_transport_time(float(getattr(self, 'current_playback_time', 0.0)))

    def _format_timecode(self, seconds: float) -> str:
        seconds = max(0.0, float(seconds))
        minutes = int(seconds // 60)
        whole_seconds = int(seconds % 60)
        millis = int(round((seconds - int(seconds)) * 1000))
        if millis >= 1000:
            whole_seconds += 1
            millis -= 1000
        return f"{minutes:02d}:{whole_seconds:02d}.{millis:03d}"

    def _get_project_duration_seconds(self) -> float:
        timeline = getattr(self, 'timeline_widget', None)
        notes = list(getattr(timeline, 'notes_list', []) or [])
        if not notes:
            return 8.0
        last_note_end = max(
            float(getattr(note, 'start_time', 0.0)) + float(getattr(note, 'duration', 0.0))
            for note in notes
        )
        return max(1.0, last_note_end + 1.0)

    def _set_transport_time(self, seconds: float) -> None:
        seconds = max(0.0, float(seconds))
        self.current_playback_time = seconds

        timeline = getattr(self, 'timeline_widget', None)
        if timeline is not None:
            if hasattr(timeline, 'set_playback_time'):
                timeline.set_playback_time(seconds)
            elif hasattr(timeline, 'set_current_time'):
                timeline.set_current_time(seconds)

        graph = getattr(self, 'graph_editor_widget', None)
        if graph is not None and hasattr(graph, 'set_current_time'):
            graph.set_current_time(seconds)

        label = getattr(self, 'time_display_label', None)
        if label is not None:
            total = self._get_project_duration_seconds()
            label.setText(f"{self._format_timecode(seconds)} / {self._format_timecode(total)}")

    def setup_voice_gallery(self):
        """
        ボイスギャラリーのセットアップ。
        ここで self.confirmed_partners の情報をギャラリーに反映させる。
        """
        # ギャラリーウィジェットのインスタンス化
        self.voice_gallery = VoiceCardGallery(self.voice_manager)
    
        # パートナー情報を設定
        self.voice_gallery.set_partner_data(self.confirmed_partners)
    
        # ギャラリーのセットアップ実行
        self.voice_gallery.setup_gallery()
    
        # ボイス選択シグナルを接続
        self.voice_gallery.voice_selected.connect(self.on_voice_changed)
        # 上部コントロールバーの「Voice」ボックスをギャラリーと連動させる
        self.voice_gallery.voice_selected.connect(self._sync_voice_selector)
        self.voice_gallery.gallery_rebuilt.connect(self._populate_voice_selector)
        self.voice_gallery.voice_imported.connect(
            lambda name: self.statusBar().showMessage(f"音源「{name}」を追加しました", 5000))
        if getattr(self, "character_selector", None) is not None:
            self.character_selector.activated.connect(self._on_voice_selector_activated)
        self._populate_voice_selector()

        # ギャラリーウィジェットのインスタンス化（MainWindowが保持）
        if hasattr(self, 'main_layout') and self.main_layout:
            self.voice_gallery_dock = QDockWidget("音源", self)
            self.voice_gallery_dock.setObjectName("VoiceGalleryDock")
            self.voice_gallery_dock.setWidget(self.voice_gallery)
            # 音源パネルは下ではなく右ドック（エフェクトチェーンのタブ）に置く。
            # 下に置くとカード分の縦幅（約250px）がピアノロールから奪われるため。
            self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.voice_gallery_dock)
            effects = getattr(self, "effects_dock", None)
            if effects is not None:
                self.tabifyDockWidget(effects, self.voice_gallery_dock)
            from PySide6.QtWidgets import QTabWidget
            self.setTabPosition(Qt.DockWidgetArea.RightDockWidgetArea, QTabWidget.TabPosition.North)
            self.voice_gallery_dock.raise_()
            self.resizeDocks([self.voice_gallery_dock], [350], Qt.Orientation.Horizontal)
            # ギャラリーに対して「募集枠の情報」を渡して更新をかける
            # 内部で先ほどの refresh_gallery(self.confirmed_partners) が呼ばれる
            self.voice_gallery.set_partner_data(self.confirmed_partners)
            self.voice_gallery.setup_gallery()

    def _populate_voice_selector(self) -> None:
        """コントロールバーの Voice ボックスを、ギャラリーの実音源（募集枠を除く）で埋める。"""
        combo = getattr(self, "character_selector", None)
        gallery = getattr(self, "voice_gallery", None)
        if combo is None or gallery is None:
            return
        current = getattr(self, "current_voice_id", None)
        combo.blockSignals(True)
        combo.clear()
        for internal_id in gallery._entries:
            card = gallery.cards.get(internal_id)
            if card is None or getattr(card, "is_recruiting", False):
                continue
            combo.addItem(card.display_name, internal_id)
        idx = combo.findData(current) if current is not None else -1
        combo.setCurrentIndex(idx)   # 未選択のときは空欄のまま（勝手に切り替えない）
        combo.blockSignals(False)

    def _sync_voice_selector(self, display_name: str, internal_id: str) -> None:
        combo = getattr(self, "character_selector", None)
        if combo is None:
            return
        idx = combo.findData(internal_id)
        if idx >= 0 and idx != combo.currentIndex():
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def _on_voice_selector_activated(self, index: int) -> None:
        combo = self.character_selector
        internal_id = combo.itemData(index)
        if internal_id is None:
            return
        self.voice_gallery.on_card_clicked(combo.itemText(index), internal_id)

    @Slot(str, str)
    def on_voice_changed(self, display_name: str, internal_id: str):
        """
        キャラクター選択が変更された際の統合ハンドラ。
        1. MainWindowの状態を更新
        2. Cエンジン(VoSeEngine)に音源をロード
        3. 既存のノートのキャッシュを新キャラクター用に更新
        """
        # --- 1. MainWindowの状態保持 ---
        self.current_voice = display_name
        self.current_voice_id = internal_id
        
        # --- 2. C++エンジンへのキャラクター適用 ---
        # VoSeEngine側で __INTERNAL__ (公式) か フォルダパス (UTAU) かを判別
        if hasattr(self, 'vo_se_engine') and self.vo_se_engine:
            try:
                # UTAU音源は表示名ではなく登録済みの実フォルダをエンジンへ渡す。
                entry = self.voice_manager.voices.get(display_name, {})
                voice_path = entry.get("path", "") if isinstance(entry, dict) else str(entry or "")
                if voice_path and os.path.isdir(voice_path):
                    self.vo_se_engine.set_voice_library(voice_path)
                    oto_data = self.parse_oto_ini(voice_path)
                    if not oto_data:
                        self.statusBar().showMessage(f"oto.ini を確認できません: {voice_path}", 5000)
                else:
                    # 内蔵音源など、フォルダ型でない音源は既存のライブラリを維持。
                    refresh = getattr(self.vo_se_engine, "refresh_voice_library", None)
                    if callable(refresh):
                        refresh()
            except Exception as e:
                self.statusBar().showMessage(f"音源切替エラー: {e}", 5000)
                print(f"Engine voice switch failed: {e}")

        # --- 3. UIへのフィードバック ---
        if self.status_label:
            self.status_label.setText(f"歌手: {display_name}")
        elif self.statusBar():
            self.statusBar().showMessage(f"Voice Loaded: {display_name}", 3000)

        # --- 4. 既存ノートの先行レンダリング(キャッシュ)更新 ---
        # 声が変わったため、裏で作っていたキャッシュを新しい声で作り直す
        # これにより、切り替え直後に再生しても「新しい声」で即座に鳴る
        if hasattr(self, 'on_timeline_updated'):
            self.on_timeline_updated()

        # --- 5. 10枠のパートナーリスト(confirmed_partners)との照合 ---
        # 選択されたIDが募集中のID（1, 2, 3...）に含まれる場合、特別なフラグ処理
        # ここに将来的なUIエフェクトや、募集中の透かしを消す処理などを追加可能
        for partner_id, status in self.confirmed_partners.items():
            if str(partner_id) in internal_id:
                print(f"INFO: Partner ID-{partner_id:02d} ({status}) has been selected.")

        print(f"✅ Character management: '{display_name}' is now active.")
        

    def load_theme_setting(self):
        """起動時にQSettingsからテーマを読み込み適用する"""
        from PySide6.QtCore import QSettings
        from modules.gui.themes import apply_theme  # 💡 インポート漏れを防ぐため関数内でも安全に確保
        
        # 💡 "vocal" に統一して設定の不一致を防ぐ
        settings = QSettings("VO-SE", "vocal")
        
        # ⭕ 修正: 変数名を theme_value に統一
        theme_value = settings.value("theme", "dark")
        
        # 明示的に str にキャストしてグローバル関数を呼び出す
        apply_theme(str(theme_value))

    def toggle_theme(self):
        """テーマの切り替えと保存、UIの更新を行う"""
        from modules.gui.themes import apply_theme
        from modules.gui.icons import icon
        from PySide6.QtCore import QSettings

        settings = QSettings("VO-SE", "vocal")
        current = settings.value("theme", "dark")
        new_theme = "light" if current == "dark" else "dark"

        if apply_theme(new_theme):  # 内部で icons.set_icon_theme() も呼ばれる
            settings.setValue("theme", new_theme)
            if self.statusBar():
                self.statusBar().showMessage(f"テーマを {new_theme} に切り替えました", 3000)

            if hasattr(self, 'theme_btn') and self.theme_btn is not None:
                self.theme_btn.setText(" ダークモードへ" if new_theme == "light" else " ライトモードへ")
                self.theme_btn.setIcon(icon("theme_moon") if new_theme == "light" else icon("theme_sun"))

            # ツールバーの主要アイコンも塗り直す
            for attr, name in (
                ("play_btn", "play"), ("stop_btn", "stop"), ("loop_btn", "loop"),
                ("talk_button", "talk"), ("open_wav_btn", "open_folder"),
                ("render_btn", "render_export"),
            ):
                btn = getattr(self, attr, None)
                if btn is not None:
                    btn.setIcon(icon(name))


    def init_ui(self) -> None:

        from PySide6.QtWidgets import QWidget, QVBoxLayout
        
        # 1. ウィンドウ基本設定
        self.setWindowTitle("VO-SE Engine DAW Pro")
        self.setGeometry(60, 40, 1560, 940)
        self.setMinimumSize(1100, 680)
        
        # 2. セントラルウィジェットとメインレイアウトの確定
        central_widget = QWidget()
        # 💡 重要: QSS側で背景色を安全に適用するため、オブジェクト名を設定します
        central_widget.setObjectName("centralWidget") 
        
        self.setCentralWidget(central_widget)
        self.main_layout = QVBoxLayout(central_widget)
        self.main_layout.setContentsMargins(8, 6, 8, 6)
        self.main_layout.setSpacing(6)

        # エフェクトパネルを右側にドッキング
        self.effects_dock = QDockWidget("エフェクトチェーン", self)
        self.effects_panel = EffectsPanel(self)
        self.effects_dock.setWidget(self.effects_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.effects_dock)

        # 3. 各セクションの順次セットアップ
        self.setup_main_editor_area()  # 1. timeline_widget生成
        self.setup_actions()           # 2. QAction定義
        self.setup_menus()             # 3. QActionをメニューに登録
        
        # 💡 注意: setup_toolbar 内で self.toggle_theme を呼び出すため、
        # ツールバー構築の前にテーマ管理メソッドがクラスに定義されている必要があります。
        self.setup_toolbar()
        
        self.setup_control_panel()
        self.setup_bottom_panel()
        self.setup_status_bar()
        self._set_transport_time(0.0)
        self.setup_voice_gallery()
        QTimer.singleShot(0, self._init_piano_roll_view)

        # 4. 新しいスタイル（外部QSSテーマ）と初期状態の適用
        # 🔄 古い self.apply_apple_refined_style() を廃止し、永続化されたテーマをロード
        self.load_theme_setting()
        
        # その他の初期スタイル調整があれば実行
        self._apply_initial_styles()


    def _refresh_transport_button_states(self) -> None:
        """再生/停止/ループの視覚状態を現在の内部状態へ同期。"""
        play_btn = cast(QPushButton, getattr(self, 'play_btn', None))
        stop_btn = cast(QPushButton, getattr(self, 'stop_btn', None))
        loop_btn = cast(QPushButton, getattr(self, 'loop_btn', None))
        play_button = cast(QPushButton, getattr(self, 'play_button', None))
        loop_button = cast(QPushButton, getattr(self, 'loop_button', None))

        is_playing = bool(getattr(self, 'is_playing', False))
        is_looping = bool(getattr(self, 'is_looping', False))

        if play_btn is not None:
            play_btn.setChecked(is_playing)
            play_btn.setText("⏸ 停止" if is_playing else "▶ 再生")
        if play_button is not None:
            play_button.setChecked(is_playing)
            play_button.setText("⏸ 停止" if is_playing else "▶ 再生")

        if stop_btn is not None:
            stop_btn.setChecked(False)

        if loop_btn is not None:
            loop_btn.setChecked(is_looping)
            loop_btn.setText("↻ ループON" if is_looping else "↻ ループ")
        if loop_button is not None:
            loop_button.setChecked(is_looping)
            loop_button.setText("ループ: ON" if is_looping else "ループ: OFF")


    def _apply_initial_styles(self) -> None:
        """初期スタイル適用の安全な実行"""
        # ログ 2620 等の「未定義属性アクセス」を防ぐため、メソッドの存在を確実に担保
        if hasattr(self, 'update_timeline_style'):
            # 代表が定義したタイムラインの視覚効果を適用
            self.update_timeline_style()

    
        self._refresh_transport_button_states()
        # ステータスバーへの初期メッセージ
        if self.statusBar():
            self.statusBar().showMessage("Engine Initialized. Ready for production.")


    def open_audio(self) -> None:
        """WAVファイルを選択し、TimelineWidgetに波形を描画させる"""
        from PySide6.QtWidgets import QFileDialog
        
        # ファイル選択ダイアログを開く
        file_path, _ = QFileDialog.getOpenFileName(
            self, 
            "オーディオファイルを選択", 
            "", 
            "Wave Files (*.wav)"
        )
        
        if file_path:
            # 1. 属性にパスを保存（TimelineWidgetがこれを参照する）
            self.current_audio_path = file_path
            
            # 2. TimelineWidgetにキャッシュを捨てさせて再描画を促す
            if hasattr(self.timeline, '_wave_cache_path'):
                del self.timeline._wave_cache_path
            
            self.timeline.update()
            
            # 3. ステータスバーに通知
            self.statusBar().showMessage(f"読み込み完了: {os.path.basename(file_path)}", 3000)
            
    # ==========================================================================
    # UI セクション構築
    # ==========================================================================
    def setup_toolbar(self):
        """上部ツールバー：再生・録音・テンポ・ファイル操作（アイコン統一版）"""
        from PySide6.QtWidgets import QToolBar, QPushButton, QLabel, QLineEdit, QWidget
        from PySide6.QtCore import QSettings, QSize, Qt
        from PySide6.QtGui import QAction
        from modules.gui.icons import icon

        ICON_SIZE = QSize(16, 16)

        self.toolbar = QToolBar("Main Toolbar")
        self.addToolBar(self.toolbar)
        self.toolbar.setMovable(False)
        self.toolbar.setIconSize(ICON_SIZE)

        # 1. 再生コントロール（セグメント連結）
        self.play_btn = QPushButton(icon("play"), " 再生")
        self.play_btn.setObjectName("SegmentLeft")
        self.play_btn.setCheckable(True)
        self.play_btn.clicked.connect(self.on_play_pause_toggled)
        self.toolbar.addWidget(self.play_btn)

        self.stop_btn = QPushButton(icon("stop"), " 停止")
        self.stop_btn.setObjectName("SegmentMid")
        # Stop is an action, not a persistent toggle.
        self.stop_btn.setCheckable(False)
        self.stop_btn.clicked.connect(self.stop_and_clear_playback)
        self.toolbar.addWidget(self.stop_btn)

        self.loop_btn = QPushButton(icon("loop"), " ループ")
        self.loop_btn.setObjectName("SegmentRight")
        self.loop_btn.setCheckable(True)
        self.loop_btn.clicked.connect(self.on_loop_button_toggled)
        self.toolbar.addWidget(self.loop_btn)

        self.toolbar.addSeparator()

        # 時刻表示（インラインstyleSheetを廃止 → objectNameでQSS管理）
        self.time_display_label = QLabel("00:00.000 / 00:00.000")
        self.time_display_label.setObjectName("TimeDisplay")
        self.time_display_label.setMinimumWidth(150)
        self.time_display_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.toolbar.addWidget(self.time_display_label)

        self.toolbar.addSeparator()

        # 読み上げ
        self.talk_button = QPushButton(icon("talk"), " 読み上げ")
        self.talk_button.clicked.connect(self.on_talk)
        self.toolbar.addWidget(self.talk_button)

        # 2. テンポ設定
        self.toolbar.addWidget(QLabel(" Tempo: "))
        self.tempo_input = QLineEdit("120")
        self.tempo_input.setFixedWidth(44)
        self.tempo_input.returnPressed.connect(self.update_tempo_from_input)
        self.toolbar.addWidget(self.tempo_input)

        self.toolbar.addSeparator()

        # 3. WAVファイル読み込み
        self.open_wav_btn = QPushButton(icon("open_folder"), " OPEN WAV")
        self.open_wav_btn.setObjectName("SecondaryButton")
        self.open_wav_btn.clicked.connect(self.open_audio)
        self.toolbar.addWidget(self.open_wav_btn)

        # 4. Cエンジン・レンダリング
        self.render_btn = QPushButton(icon("render_export"), " RENDER")
        self.render_btn.setObjectName("PrimaryButton")
        self.render_btn.clicked.connect(self.on_render_button_clicked)
        self.toolbar.addWidget(self.render_btn)

        # ペンモードトグル
        self.pen_mode_action = QAction(icon("pen"), " ペンモード", self)
        self.pen_mode_action.setCheckable(True)
        self.pen_mode_action.triggered.connect(self.on_pen_mode_toggled)
        self.toolbar.addAction(self.pen_mode_action)

        # ピッチカーブ表示トグル
        self.pitch_overlay_action = QAction(icon("pitch_curve"), " ピッチカーブ表示", self)
        self.pitch_overlay_action.setCheckable(True)
        self.pitch_overlay_action.setChecked(True)
        self.pitch_overlay_action.toggled.connect(
            lambda checked: self.timeline_widget.set_show_parameter_overlay(checked)
            if self.timeline_widget else None
        )
        self.toolbar.addAction(self.pitch_overlay_action)
    
        # オートチューン
        auto_tune_action = QAction(icon("auto_tune"), " オートチューン", self)
        auto_tune_action.triggered.connect(self.auto_tune_selected)
        self.toolbar.addAction(auto_tune_action)

        # 右端を整えるためのスペーサー
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred))
        self.toolbar.addWidget(spacer)

        settings = QSettings("VO-SE", "vocal")
        current_theme = settings.value("theme", "dark")
        theme_icon = icon("theme_moon") if current_theme == "light" else icon("theme_sun")
        theme_text = " ダークモードへ" if current_theme == "light" else " ライトモードへ"

        self.theme_btn = QPushButton(theme_icon, theme_text)
        self.theme_btn.setObjectName("SecondaryButton")
        self.theme_btn.clicked.connect(self.toggle_theme)
        self.toolbar.addWidget(self.theme_btn)

    def setup_main_editor_area(self):
        """メインエディタエリア（トラックリスト + タイムライン）"""
        from PySide6.QtWidgets import QFrame
        from PySide6.QtCore import Qt

        # --- スプリッターの生成（一度だけ） ---
        self.editor_splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- 左側：トラック管理パネル ---
        self.track_panel = QFrame()
        self.track_panel.setObjectName("TrackPanel")
        self.track_panel.setFrameShape(QFrame.Shape.StyledPanel)
        self.track_panel.setMinimumWidth(240)
        self.track_panel.setMaximumWidth(400)

        track_layout = QVBoxLayout(self.track_panel)
        track_layout.setContentsMargins(5, 5, 5, 5)

        self.track_list_widget = QListWidget()
        self.track_list_widget.setObjectName("TrackList")
        self.track_list_widget.currentRowChanged.connect(self.switch_track)

        btn_layout = QHBoxLayout()
        self.btn_add_vocal = QPushButton("+ Vocal")
        self.btn_add_wave = QPushButton("+ Audio")
        self.btn_add_vocal.clicked.connect(lambda: self.add_track("vocal"))
        self.btn_add_wave.clicked.connect(lambda: self.add_track("wave"))
        btn_layout.addWidget(self.btn_add_vocal)
        btn_layout.addWidget(self.btn_add_wave)

        tracks_heading = QLabel("TRACKS")
        tracks_heading.setObjectName("SectionHeading")
        track_layout.addWidget(tracks_heading)
        track_layout.addWidget(self.track_list_widget)
        track_layout.addLayout(btn_layout)

        # --- 右側：タイムライン（ここで一度だけ生成） ---
        right_container = QWidget()
        right_layout = QVBoxLayout(right_container)
        right_layout.setContentsMargins(0, 0, 0, 0)

        timeline_row = QHBoxLayout()
        self.timeline_widget = TimelineWidget()
        self.keyboard_sidebar = KeyboardSidebarWidget(
            key_height_pixels=self.timeline_widget.key_height_pixels
        )
        self.keyboard_sidebar_widget = self.keyboard_sidebar
    
        # ↓ ここで初期化（setup_timeline_area から移植）
        self.v_scrollbar = QSlider(Qt.Orientation.Vertical, self)
        self.v_scrollbar.setRange(0, int(128 * self.timeline_widget.key_height_pixels))
        self.v_scrollbar.setInvertedAppearance(True)   # つまみを上に動かす＝高い音域へ
        self.v_scrollbar.setValue(0)
        self.v_scrollbar.valueChanged.connect(self.timeline_widget.set_vertical_offset)

        timeline_row.addWidget(self.keyboard_sidebar)
        timeline_row.addWidget(self.timeline_widget)
        timeline_row.addWidget(self.v_scrollbar)
    
        self.h_scrollbar = QScrollBar(Qt.Orientation.Horizontal)
        self.h_scrollbar.valueChanged.connect(self.timeline_widget.set_horizontal_offset)
    
        # タイムライン上下分割スプリッター
        timeline_splitter = QSplitter(Qt.Orientation.Vertical)

        # 上段：タイムライン本体
        timeline_container = QWidget()
        # ピアノロール本体に最低高さを設定し、グラフエディタに押しつぶされないようにする。
        # グラフ側の最低高さ(100px)とは別に、タイムライン本体を最低400px確保する。
        self.timeline_widget.setMinimumHeight(400)
        timeline_container_layout = QHBoxLayout(timeline_container)
        timeline_container_layout.setContentsMargins(0, 0, 0, 0)
        timeline_container_layout.addLayout(timeline_row)
        timeline_splitter.addWidget(timeline_container)

        # 下段：グラフエディタ
        self.graph_editor_widget = GraphEditorWidget()
        self.graph_editor_widget.pixels_per_beat = self.timeline_widget.pixels_per_beat
        self.graph_editor_widget.parameters_changed.connect(self.on_graph_parameters_changed)
        graph_container = QWidget()
        self._graph_container_layout = QVBoxLayout(graph_container)
        self._graph_container_layout.setContentsMargins(0, 4, 0, 0)
        self._graph_container_layout.setSpacing(4)
        self._graph_container_layout.addWidget(self.graph_editor_widget, 1)
        timeline_splitter.addWidget(graph_container)

        # ピアノロール側にGraphEditorWidgetの実データを見せる
        self.timeline_widget.set_parameter_source(self.graph_editor_widget)

        # 初期比率（タイムライン7：グラフ3）
        timeline_splitter.setStretchFactor(0, 4)   # ピアノロール優先
        timeline_splitter.setStretchFactor(1, 1)
        timeline_splitter.setChildrenCollapsible(False)
        self.graph_editor_widget.setMinimumHeight(100)
        timeline_splitter.setSizes([780, 130])

        right_layout.addWidget(timeline_splitter)
        right_layout.addWidget(self.h_scrollbar)

        # --- スプリッターに追加（それぞれ一度だけ） ---
        self.editor_splitter.addWidget(self.track_panel)
        self.editor_splitter.addWidget(right_container)

        # --- メインレイアウトに追加（一度だけ） ---
        if self.main_layout is not None:
            self.main_layout.addWidget(self.editor_splitter, 1)  # 余白は全部ピアノロールへ
            self.editor_splitter.setStretchFactor(0, 0)
            self.editor_splitter.setStretchFactor(1, 1)
            self.editor_splitter.setSizes([260, 1200])

        # --- 初期リスト更新 ---
        self.refresh_track_list_ui()

        # ★ ここでエイリアスを設定（ウィジェットが実体化された後）
        self.timeline = self.timeline_widget

    def _init_piano_roll_view(self) -> None:
        """起動直後のピアノロール表示位置を C5 付近に合わせ、スクロール範囲を実サイズに合わせる。"""
        tl = getattr(self, "timeline_widget", None)
        sb = getattr(self, "v_scrollbar", None)
        if tl is None or sb is None:
            return
        kh = float(tl.key_height_pixels)
        max_off = max(0, int(128 * kh - tl.height()))
        sb.setRange(0, max_off)
        target = int((127 - 72) * kh - tl.height() / 2)   # ノート72(C5)を中央に
        sb.setValue(max(0, min(max_off, target)))

    def setup_bottom_panel(self):
        """下部：歌詞入力などのツール"""
        bottom_box = QHBoxLayout()
        bottom_box.setContentsMargins(12, 6, 12, 6)
        bottom_box.setSpacing(10)

        from modules.gui.icons import icon
        from PySide6.QtCore import QSize
        self.lyrics_button = QPushButton(" 歌詞一括入力")
        self.lyrics_button.setIcon(icon("pen"))
        self.lyrics_button.setIconSize(QSize(18, 18))
        self.lyrics_button.setObjectName("PrimaryButton")
        self.lyrics_button.setFixedHeight(36)
        self.lyrics_button.clicked.connect(self.on_click_apply_lyrics_bulk)
        cp = getattr(self, "_control_panel_layout", None)
        if cp is not None:
            # 専用の1行を使わず、コントロールバーの末尾（stretchの手前）に入れる
            self.lyrics_button.setFixedHeight(30)
            cp.addWidget(self.lyrics_button)
        else:
            bottom_box.addWidget(self.lyrics_button)
            bottom_box.addStretch()
            self.main_layout.addLayout(bottom_box)


    
    def setup_control_panel(self):
        """
        上部コントロールパネル(セカンドツールバー)の構築（PATCH_GUIDE 4番準拠）
        """
        from modules.gui.icons import icon
        from modules.gui.flow_layout import FlowLayout
        # 幅が足りないときは重ならずに次の行へ折り返す（QHBoxLayout だと
        # 最小幅を下回ったとき、ボタン同士が重なって見えていた）
        panel_layout = FlowLayout(margin=0, h_spacing=8, v_spacing=6)

        # 録音コントロール（危険色はQSS側の #RecordButton が担当）
        self.record_button = QPushButton(icon("record"), " 録音")
        self.record_button.setObjectName("RecordButton")
        self.record_button.setCheckable(True)
        self.record_button.clicked.connect(self.on_record_toggled)
        panel_layout.addWidget(self.record_button)

        # キャラクター選択（ラベルと対で折り返されるよう 1 つのまとまりにする）
        voice_group = QWidget()
        voice_row = QHBoxLayout(voice_group)
        voice_row.setContentsMargins(0, 0, 0, 0)
        voice_row.setSpacing(6)
        voice_row.addWidget(QLabel("Voice:"))
        self.character_selector = QComboBox()
        self.character_selector.setMinimumWidth(170)
        self.character_selector.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.character_selector.setToolTip("使用する音源。音源の追加は右の「音源」パネルから行えます")
        voice_row.addWidget(self.character_selector)
        panel_layout.addWidget(voice_group)

        # MIDIポート選択
        midi_group = QWidget()
        midi_row = QHBoxLayout(midi_group)
        midi_row.setContentsMargins(0, 0, 0, 0)
        midi_row.setSpacing(6)
        midi_row.addWidget(QLabel("MIDI:"))
        self.midi_port_selector = QComboBox()
        self.midi_port_selector.setMinimumWidth(130)
        midi_row.addWidget(self.midi_port_selector)
        panel_layout.addWidget(midi_group)
        self.midi_port_selector.addItem("ポートなし", None)
        self.midi_port_selector.currentIndexChanged.connect(self.on_midi_port_changed)
        
        # ファイルを開く(MIDI/UST等)
        self.open_button = QPushButton(icon("open_folder"), " 開く")
        self.open_button.setObjectName("SecondaryButton")
        self.open_button.clicked.connect(self.open_file_dialog_and_load_midi)
        panel_layout.addWidget(self.open_button)

        # 音源フォルダの再スキャン
        self.rescan_voices_button = QPushButton(icon("rescan"), " 音源再スキャン")
        self.rescan_voices_button.setObjectName("SecondaryButton")
        self.rescan_voices_button.clicked.connect(self.refresh_voice_list)
        panel_layout.addWidget(self.rescan_voices_button)

        # AI解析ボタン（インライン直書き廃止 → PrimaryButton）
        self.ai_analyze_button = QPushButton(icon("ai_wand"), " AI Auto Setup")
        self.ai_analyze_button.setObjectName("PrimaryButton")
        self.ai_analyze_button.clicked.connect(self.start_batch_analysis)
        panel_layout.addWidget(self.ai_analyze_button)
        
        # AI歌詞配置ボタン
        self.auto_lyrics_button = QPushButton(icon("lyrics"), " 自動歌詞配置")
        self.auto_lyrics_button.setObjectName("SecondaryButton")
        self.auto_lyrics_button.clicked.connect(self.on_click_auto_lyrics)
        panel_layout.addWidget(self.auto_lyrics_button)

        # --- パラメーター切り替えボタン ---
        param_row = QHBoxLayout()
        param_row.setContentsMargins(8, 0, 8, 0)
        param_row.setSpacing(6)
        param_row.addWidget(QLabel("Edit Mode:"))
        
        # ボタングループで「どれか1つが選択されている状態」を作る
        self.param_group = QButtonGroup(self)
        self.param_buttons = {}
        
        param_list = [
            ("Pitch", "pitch"),
            ("Gender", "gender"),
            ("Tension", "tension"),
            ("Breath", "breath"),
        ]
        
        for name, color_key in param_list:
            btn = QPushButton(name)
            btn.setCheckable(True)
            btn.setProperty("role", "paramToggle")
            btn.setProperty("paramColor", color_key)

            # 動的プロパティを使ったQSSセレクタはtoggle時に自動で再評価されない場合が
            # あるため、チェック状態が変わるたびに明示的に再ポリッシュする
            def _repolish(checked, b=btn):
                b.style().unpolish(b)
                b.style().polish(b)
            btn.toggled.connect(_repolish)

            if name == "Pitch":
                btn.setChecked(True)
            
            param_row.addWidget(btn)
            self.param_group.addButton(btn)
            self.param_buttons[name] = btn

        # ボタンがクリックされたらグラフエディタのモードを切り替える
        self.param_group.buttonClicked.connect(self.on_param_mode_changed)

        param_row.addStretch()
        gcl = getattr(self, "_graph_container_layout", None)
        if gcl is not None:
            gcl.insertLayout(0, param_row)      # グラフエディタの真上
        else:
            self.main_layout.addLayout(param_row)   # フォールバック
        self._control_panel_layout = panel_layout
        self.main_layout.insertLayout(0, panel_layout)   # エディタの上に配置



    def open_file_dialog_and_load_midi(self) -> None:
        """ファイルダイアログからプロジェクト/音声ファイルを拡張子に応じて読み込む。"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "プロジェクトを開く",
            "",
            "VO-SE Files (*.vose *.json *.ust *.ustx *.vsqx *.mid *.midi);;"
            "MIDI Files (*.mid *.midi);;"
            "UTAU Files (*.ust *.ustx);;"
            "VSQX Files (*.vsqx);;"
            "VO-SE/JSON Files (*.vose *.json);;"
            "All Files (*)",
        )
        if not file_path:
            return

        loader = getattr(self, "load_file_from_path", None)
        if callable(loader):
            loader(file_path)
            return

        ext = os.path.splitext(file_path)[1].lower()
        if ext in (".mid", ".midi"):
            midi_loader = getattr(self, "load_midi_file_from_path", None)
            if callable(midi_loader):
                midi_loader(file_path)
                return
        if ext == ".ust":
            ust_loader = getattr(self, "load_ust_file", None)
            if callable(ust_loader):
                ust_loader(file_path)
                return

        QMessageBox.warning(
            self,
            "読み込みエラー",
            "このファイル形式を読み込む機能がまだ初期化されていません。",
        )


    def setup_status_bar(self):
        """ステータスバーの構築 (Pyright/Pylance 完全対応版)"""
        
        # 1. 自身の statusBar オブジェクトを取得し、存在と型を確定させる
        # これにより "addWidget is not a known attribute of None" を一掃します
        status_bar = self.statusBar()
        if not status_bar:
            return

        # 2. ラベルの生成と追加
        self.status_label = QLabel("準備完了")
        status_bar.addWidget(self.status_label)
        
        # 3. プログレスバーの生成と追加
        self.progress_bar = QProgressBar()
        
        # 型を明示的にキャストしてアクセスすることで、以降の hide/show での警告を防ぎます
        prog_bar = cast(QProgressBar, self.progress_bar)
        prog_bar.hide()
        
        # ステータスバーの右側に常駐させる
        status_bar.addPermanentWidget(prog_bar)

    def setup_actions(self):
        """アクションの定義"""
        self.copy_action = QAction("コピー", self)
        self.copy_action.setShortcuts(QKeySequence.StandardKey.Copy)
        self.copy_action.triggered.connect(
            self.timeline_widget.copy_selected_notes_to_clipboard
        )
        
        self.paste_action = QAction("ペースト", self)
        self.paste_action.setShortcuts(QKeySequence.StandardKey.Paste)
        self.paste_action.triggered.connect(
            self.timeline_widget.paste_notes_from_clipboard
        )
        
        self.save_action = QAction("保存(&S)", self)
        self.save_action.setShortcuts(QKeySequence.StandardKey.Save)
        self.save_action.triggered.connect(self.save_file_dialog_and_save_midi)

        # Undo / Redo
        # (ショートカット自体は setup_vose_shortcuts の QShortcut で結線済み。
        #  ここでは編集メニューに表示するための QAction のみ用意し、
        #  ショートカットの二重登録は避ける)
        self.undo_action = QAction("元に戻す(&U)", self)
        self.undo_action.triggered.connect(self.undo)

        self.redo_action = QAction("やり直し(&R)", self)
        self.redo_action.triggered.connect(self.redo)

    def setup_menus(self):
        """メニューバーの構築"""
        # ファイルメニュー
        file_menu = self.menuBar().addMenu("ファイル(&F)")
        file_menu.addAction(self.save_action)
        
        export_action = QAction("WAV書き出し...", self)
        export_action.triggered.connect(self.on_export_button_clicked)
        file_menu.addAction(export_action)
        
        export_midi_action = QAction("MIDI書き出し...", self)
        export_midi_action.triggered.connect(self.export_to_midi_file)
        file_menu.addAction(export_midi_action)

        # 編集メニュー
        edit_menu = self.menuBar().addMenu("編集(&E)")
        edit_menu.addAction(self.undo_action)
        edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator()
        edit_menu.addAction(self.copy_action)
        edit_menu.addAction(self.paste_action)

    def setup_connections(self):
        """
        シグナル/スロット接続の完全版（省略なし）。
        UI、エンジン、および各ウィジェット間の通信を確立します。
        """
        # --- 1. スクロール同期（垂直：鍵盤とノート領域） ---
        if self.v_scrollbar and self.keyboard_sidebar and self.timeline_widget:
            self.v_scrollbar.valueChanged.connect(self.keyboard_sidebar.set_vertical_offset)
            
        if self.keyboard_sidebar is not None:
            self.keyboard_sidebar.note_pressed.connect(
                lambda note: self.handle_midi_realtime(note, 100, "on")
            )
            self.keyboard_sidebar.note_released.connect(
                lambda note: self.handle_midi_realtime(note, 0, "off")
            )

        # --- 2. スクロール同期（水平：ノートとピッチグラフ領域） ---
        if self.h_scrollbar and self.timeline_widget and self.graph_editor_widget:
            
            self.h_scrollbar.valueChanged.connect(self.graph_editor_widget.set_horizontal_offset)
            self.timeline_widget.scroll_synced_signal.connect(self._sync_horizontal_scrollbar_from_timeline)

        # --- 3. タイムライン・データ更新の同期 ---
        if self.timeline_widget:
            # ノートが動いたときにメインウィンドウ側で受け取る
            self.timeline_widget.notes_changed_signal.connect(self.on_timeline_updated)
            # タイムラインからグラフエディタへ通知（ピッチ描画の基準更新）
            if self.graph_editor_widget is not None:
                self.timeline_widget.notes_changed_signal.connect(
                    self.graph_editor_widget.sync_with_notes
                )
            # ノート編集（追加/削除/移動/リサイズ/ペースト/複製/歌詞編集）が
            # 確定したタイミングでUndo履歴に登録する
            self.timeline_widget.edit_committed_signal.connect(
                self.on_timeline_edit_committed
            )
        # --- 4. テンポ入力の確定（Returnキー押下で反映） ---
        if self.tempo_input:
            self.tempo_input.returnPressed.connect(self.update_tempo_from_input)

        # ---5,グラフエディタの編集履歴を Undo/Redo に登録
        if self.graph_editor_widget is not None:
            self.graph_editor_widget.edit_committed_signal.connect(
                self.on_graph_edit_committed
            )

        # ---6.エフェクトパネルのパラメータ変更をエンジンに反映（例）
        if hasattr(self, 'effects_panel'):
            self.effects_panel.effect_parameters_changed.connect(
                self.on_effect_parameters_changed
            )

        # 音源選択シグナルは setup_voice_gallery() 側で一度だけ接続する。
        # ここで再接続すると1回の選択で音源再ロードとキャッシュ更新が二重実行される。

        # --- 8.再生・停止・録音ボタンの制御 ---
        if self.play_button:
            self.play_button.clicked.connect(self.toggle_playback)
        # record_button は生成時に on_record_toggled() へ接続済み。
        # ここで再接続すると1クリックで開始→停止まで2回反転するため、
        # 二重接続は行わない。

        # --- 9. エンジンからのフィードバック（再生位置の同期） ---
        if self.playback_timer:
            self.playback_timer.timeout.connect(self.update_playback_ui)

        print("✅ All internal signals and slots have been connected.")
        

    def setup_formant_slider(self):
        """フォルマントスライダーの設定"""
        from PySide6.QtWidgets import QSlider
        
        self.formant_label = QLabel("声の太さ (Formant)")
        self.formant_slider = QSlider(Qt.Orientation.Horizontal)
        self.formant_slider.setRange(-100, 100)
        self.formant_slider.setValue(0)
        self.formant_slider.setMaximumWidth(150)
        self.formant_slider.valueChanged.connect(self.on_formant_changed)
        
        self.toolbar.addWidget(self.formant_label)
        self.toolbar.addWidget(self.formant_slider)

    def on_formant_changed(self, value):
        """フォルマント変更時の処理"""
        shift = value / 100.0
        if hasattr(self.vo_se_engine, 'vose_set_formant'):
            self.vo_se_engine.vose_set_formant(shift)

    def init_pro_talk_ui(self):
        """Talk入力UI初期化"""
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText("喋らせたい文章を入力（Enterで展開）...")
        self.text_input.setFixedWidth(300)
        self.text_input.returnPressed.connect(self.on_talk_execute)
        
        self.toolbar.addWidget(QLabel("Talk:"))
        self.toolbar.addWidget(self.text_input)

    def on_talk_execute(self):
        """Talk実行処理（省略なし完全版）"""
        # 1. 入力チェック（Noneガード付き）
        if not hasattr(self, 'text_input') or self.text_input is None:
            return
            
        text = self.text_input.text()
        if not text:
            return
        
        # 2. 解析と反映
        if hasattr(self, 'analyzer') and self.analyzer:
            new_events = self.analyzer.analyze_to_pro_events(text)
            
            tw = getattr(self, 'timeline_widget', None)
            if tw:
                if hasattr(tw, 'set_notes'):
                    tw.set_notes(new_events)
                tw.update()
            
            # 3. 通知とクリア
            status_bar = self.statusBar()
            if status_bar:
                status_bar.showMessage(f"Talkモード: '{text}' を展開しました")
            self.text_input.clear()

    def on_talk(self):
        text_input = getattr(self, 'text_input', None)
        if text_input is None:
            return
        text = text_input.text()
        if not text:
            return
        if self.talk_manager is None:
            print("WARNING: talk_manager が初期化されていません")
            return
        self.talk_manager.speak(text)
    
    @Slot(object)
    def on_param_mode_changed(self, button):
        """パラメーター切り替えボタン処理（省略なし完全版）"""
        if not button:
            return
            
        # button.text() でモード名を取得
        mode = button.text()
        
        # グラフエディタへ通知
        ge = getattr(self, 'graph_editor_widget', None)
        if ge and hasattr(ge, 'set_mode'):
            ge.set_mode(mode)
            
        status_bar = self.statusBar()
        if status_bar:
            status_bar.showMessage(f"編集モード: {mode}")

        if self.timeline_widget is not None:
            self.timeline_widget.update()

    def toggle_playback(self, event=None):
        """現在のトラックを再生/一時停止する互換再生経路。"""
        with self._playback_lock:
            monitoring = getattr(self, "pro_monitoring", None)
            is_playing = (
                getattr(monitoring, "is_playing", False)
                if monitoring is not None and not isinstance(monitoring, bool)
                else bool(getattr(self, "is_playing", False))
            )

            if is_playing:
                player = getattr(self, "audio_player", None)
                pause = getattr(player, "pause", None)
                if callable(pause):
                    pause()
                self.is_playing = False
                if monitoring is not None and not isinstance(monitoring, bool):
                    setattr(monitoring, "is_playing", False)
                self._refresh_transport_button_states()
                self.statusBar().showMessage("一時停止", 2000)
                return

            tracks = list(getattr(self, "tracks", []) or [])
            idx = int(getattr(self, "current_track_idx", 0))
            if not (0 <= idx < len(tracks)):
                self.statusBar().showMessage("再生するトラックがありません。", 2000)
                return

            current_track = tracks[idx]
            timeline = getattr(self, "timeline_widget", None)
            start_time = float(
                getattr(timeline, "_current_playback_time", 0.0)
                if timeline is not None
                else 0.0
            )

            if getattr(current_track, "track_type", "vocal") == "wave":
                audio_path = str(getattr(current_track, "audio_path", "") or "")
                player = getattr(self, "audio_player", None)
                play_file = getattr(player, "play_file", None)

                if not audio_path or not os.path.exists(audio_path):
                    self.statusBar().showMessage("オーディオファイルが見つかりません。", 3000)
                    return
                if not callable(play_file):
                    self.statusBar().showMessage("オーディオプレイヤーを初期化できません。", 3000)
                    return

                play_file(audio_path)
                set_position = getattr(player, "set_position", None)
                if callable(set_position):
                    set_position(int(start_time * 1000))
            else:
                engine = getattr(self, "vo_se_engine", None)
                notes = list(getattr(timeline, "notes_list", []) or []) if timeline is not None else []
                play_audio = getattr(engine, "play_audio", None)
                if notes and callable(play_audio):
                    threading.Thread(target=play_audio, daemon=True).start()

            self.is_playing = True
            if monitoring is not None and not isinstance(monitoring, bool):
                setattr(monitoring, "is_playing", True)
            self.current_playback_time = start_time
            self._refresh_transport_button_states()
            self.statusBar().showMessage(
                f"再生中: {self._format_timecode(start_time)}", 2000
            )

    def refresh_canvas(self):
        """キャンバス（描画領域）を再描画する"""
        if hasattr(self, 'timeline_widget'):
            if self.timeline_widget: 
                assert self.timeline_widget is not None
                self.timeline_widget.update()

    def sync_ui_to_selection(self):
        """選択されたアイテムに合わせてUI表示を同期する"""
        # ここに選択状態の同期処理を書く
        pass

    def setup_vose_shortcuts(self):
        """ショートカットキーの設定 (PySide6方式)"""        
        # Spaceキーで再生/停止
        self.play_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self.play_shortcut.activated.connect(self.toggle_playback)

        # Undo / Redo
        # (HistoryManager / EditCommand は実装済みだが、ショートカットが
        #  どこにも結線されておらず Ctrl+Z / Ctrl+Y が機能していなかったため追加)
        self.undo_shortcut = QShortcut(QKeySequence.StandardKey.Undo, self)
        self.undo_shortcut.activated.connect(self.undo)

        self.redo_shortcut = QShortcut(QKeySequence.StandardKey.Redo, self)
        self.redo_shortcut.activated.connect(self.redo)
        # Windows/Linuxでは Ctrl+Y も Redo として広く使われているため追加で割り当てる
        self.redo_shortcut_alt = QShortcut(QKeySequence("Ctrl+Y"), self)
        self.redo_shortcut_alt.activated.connect(self.redo)


    def perform_startup_sequence(self):
        """[完全版] 起動時のハードウェア診断とエンジン最適化"""
        # 1. UIの初期化（ステータスバーにラベルを追加）
        if not hasattr(self, 'device_status_label'):
            from PySide6.QtWidgets import QLabel
            self.device_status_label = QLabel(self)
            self.statusBar().addPermanentWidget(self.device_status_label)

        self.statusBar().showMessage("Initializing VO-SE Engine...")
        
        # 2. ハードウェア診断ロジック
        try:
            # 外部ライブラリがあるか、どのハードが使えるかチェック
            import onnxruntime as ort
            providers = ort.get_available_providers()
            
            if 'DmlExecutionProvider' in providers:
                self.active_device = "GPU (DirectML)"
                self.active_provider = "DmlExecutionProvider"
            elif 'CoreMLExecutionProvider' in providers:
                self.active_device = "Neural Engine (Apple)"
                self.active_provider = "CoreMLExecutionProvider"
            elif 'CUDAExecutionProvider' in providers:
                self.active_device = "NVIDIA GPU (CUDA)"
                self.active_provider = "CUDAExecutionProvider"
            else:
                self.active_device = "CPU (Standard)"
                self.active_provider = "CPUExecutionProvider"
                
        except ImportError:
            # ライブラリが見つからない場合は安全なCPUモードへ
            self.active_device = "CPU (Safe Mode)"
            self.active_provider = "CPUExecutionProvider"

        # 3. 診断結果をUIに反映
        if self.device_status_label is not None:
            self.device_status_label.setText(f" [ {self.active_device} ] ")
        self.statusBar().showMessage(f"Engine Ready: {self.active_device}", 5000)
        
        # アップデート確認（CI/スモークテストでは環境変数で無効化可能）
        skip_update_check = os.environ.get("VOSE_SKIP_UPDATE_CHECK", "").lower() in {
            "1", "true", "yes", "on"
        }
        if not skip_update_check:
            QTimer.singleShot(3000, self._check_for_updates)

        # 起動時にトラックが1件も無いと、ユーザーは「+ Vocal」ボタンを
        # 自分で押すまで何も編集できない。多くのDAW/エディタと同様、
        # 最低1トラックがある状態で起動するようにする。
        # add_track() はUndo履歴に積む設計のため、ユーザー操作ではない
        # この自動生成では使わず、直接 tracks に追加する。
        if not self.tracks:
            initial_track = VoseTrack("Vocal 1", "vocal")
            self.tracks.append(initial_track)
            self.refresh_track_list_ui()
            self.current_track_idx = 0
            self.track_list_widget.setCurrentRow(0)

        # ★ パフォーマンスモニターを起動
        self.setup_performance_monitoring()   
        # ★ バッファ調整スライダー（オプション）
        self.setup_performance_slider()

    @Slot(bool)
    def on_pen_mode_toggled(self, checked: bool) -> None:
        """ペンモードのオン/オフ切り替え"""
        self.pen_mode_enabled = checked
        if hasattr(self, 'timeline_widget') and hasattr(self.timeline_widget, 'set_pen_mode'):
            self.timeline_widget.set_pen_mode(checked)
        if hasattr(self, 'statusBar'):
            self.statusBar().showMessage(
                "ペンモード: ON" if checked else "ペンモード: OFF", 3000
            )

    def setup_performance_monitoring(self) -> None:
        """パフォーマンスモニターをドッキング可能なウィジェットとして追加"""
        # ドッキングウィジェットとして作成
        from PySide6.QtWidgets import QDockWidget

        dock = QDockWidget("パフォーマンス", self)
        dock.setObjectName("PerformanceDock")
        monitor = PerformanceMonitorWidget(self)
        dock.setWidget(monitor)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, dock)
        gal = getattr(self, "voice_gallery_dock", None)
        if gal is not None:
            self.tabifyDockWidget(gal, dock)   # 音源タブの裏に格納（縦幅を食わない）
            gal.raise_()

        # ステータスバーに常時表示する1行サマリー
        try:
            import psutil as _ps
            self._perf_summary = QLabel("CPU --% · MEM --%")
            self._perf_summary.setObjectName("PerfSummary")
            self.statusBar().addPermanentWidget(self._perf_summary)
            def _tick(_ps=_ps, lbl=self._perf_summary):
                lbl.setText(f"CPU {_ps.cpu_percent():.0f}% · MEM {_ps.virtual_memory().percent:.0f}%")
            self._perf_summary_timer = QTimer(self)
            self._perf_summary_timer.setInterval(1000)
            self._perf_summary_timer.timeout.connect(_tick)
            self._perf_summary_timer.start()
        except Exception:
            pass

        # ツールバーに表示/非表示トグルを追加
        view_menu = self.menuBar().addMenu("表示(&V)")
        toggle_action = dock.toggleViewAction()
        toggle_action.setText("パフォーマンスモニター")
        view_menu.addAction(toggle_action)

    # 負荷調整スライダーを setup_control_panel または setup_toolbar に追加
    def setup_performance_slider(self) -> None:
        """バッファサイズ調整スライダー（パフォーマンス調整）"""
        from PySide6.QtWidgets import QSlider, QLabel, QHBoxLayout

        # すでに setup_control_panel で追加しても良いが、ここでは独立したメソッドとして提供
        # 既存の main_layout に追加する例
        perf_layout = QHBoxLayout()
        perf_layout.addWidget(QLabel("バッファサイズ:"))

        self.buffer_slider = QSlider(Qt.Orientation.Horizontal)
        self.buffer_slider.setRange(1, 10)  # 1=最小(低遅延), 10=最大(安定)
        self.buffer_slider.setValue(4)      # デフォルト中間
        self.buffer_slider.setMaximumWidth(150)
        self.buffer_slider.valueChanged.connect(self.on_buffer_size_changed)
        perf_layout.addWidget(self.buffer_slider)

        self.buffer_label = QLabel("256 samples")
        perf_layout.addWidget(self.buffer_label)

        # 専用の1行を使わずステータスバー右側へ
        from PySide6.QtWidgets import QWidget as _QW
        holder = _QW()
        perf_layout.setContentsMargins(0, 0, 0, 0)
        holder.setLayout(perf_layout)
        self.statusBar().addPermanentWidget(holder)

    @Slot(int)
    def on_buffer_size_changed(self, value: int) -> None:
        """バッファサイズ変更（C++エンジンと AudioOutput に反映）"""
        # マッピング: 1→64, 2→128, 3→256, 4→512, ... 10→8192
        sizes = [64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768]
        idx = max(0, min(value - 1, len(sizes) - 1))
        new_size = sizes[idx]

        self.buffer_label.setText(f"{new_size} samples")

        # 1. AudioOutput に反映
        if hasattr(self, 'audio_output'):
            self.audio_output.block_size = new_size

        # 2. C++ エンジンに反映（存在する場合）
        if hasattr(self, 'vo_se_engine') and hasattr(self.vo_se_engine, 'lib'):
            try:
                if hasattr(self.vo_se_engine.lib, 'vose_set_buffer_size'):
                    self.vo_se_engine.lib.vose_set_buffer_size(new_size)
            except Exception:
                pass

        self.statusBar().showMessage(f"バッファサイズ: {new_size} samples")

    def log_startup(self, message):
        """標準出力へのログ記録）""" 
        timestamp = time.strftime('%H:%M:%S')
        print(f"[{timestamp}] [BOOT] {message}")
        """起動ログ（デバッグ用）"""
        print(f"[{time.strftime('%H:%M:%S')}] VO-SE Boot: {message}")

    def setup_vose_keyboard_navigation(self):
        """高度なキーボードナビゲーションの設定"""
        from PySide6.QtGui import QShortcut, QKeySequence
        # 1. 1音移動 (Alt + Left/Right)
        QShortcut(QKeySequence("Alt+Right"), self).activated.connect(self.select_next_note)
        QShortcut(QKeySequence("Alt+Left"), self).activated.connect(self.select_prev_note)

        # 2. 削除 (Delete / Backspace)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self).activated.connect(self.delete_selected_note)
        QShortcut(QKeySequence(Qt.Key.Key_Backspace), self).activated.connect(self.delete_selected_note)

        # 3. Tabキーによる歌詞入力フォーカス移動
        QShortcut(QKeySequence(Qt.Key.Key_Tab), self).activated.connect(self.focus_next_note_input)

    # --- 動作ロジック ---

    def select_next_note(self):
        """
        Alt+Right で次のノートを選択する。

        以前は self.notes (timeline_widget.notes_list とは別管理の
        ミラーリスト) を操作しており、画面の実データと食い違っていたため
        何も起きないように見えていた。timeline_widget 側の選択状態を
        直接操作する形に修正した。
        """
        if self.timeline_widget:
            self.timeline_widget.select_next_note_in_time()

    def select_prev_note(self):
        """Alt+Left で前のノートを選択する。"""
        if self.timeline_widget:
            self.timeline_widget.select_prev_note_in_time()

    def delete_selected_note(self):
        """
        Delete/Backspace で選択中のノートを削除する。

        以前は self.notes.pop(...) で別管理のミラーリストを操作するだけで
        画面（timeline_widget.notes_list）には反映されず、Undo履歴にも
        積まれなかった。timeline_widget.delete_selected() を呼ぶことで、
        実データの削除と Undo 履歴への登録の両方を行う。
        """
        if self.timeline_widget:
            self.timeline_widget.delete_selected()

    def focus_next_note_input(self):
        """Tabキーで次の入力欄へ。Pro Audio的な爆速入力を実現"""
        if not hasattr(self, 'input_fields') or not self.input_fields:
            return
        
        # 現在フォーカスされているウィジェットを確認
        current = self.focusWidget()
        if isinstance(current, QLineEdit) and current in self.input_fields:
            idx = self.input_fields.index(current)
            next_idx = (idx + 1) % len(self.input_fields)
            self.input_fields[next_idx].setFocus()
            self.input_fields[next_idx].selectAll()

    def draw_pro_grid(self):
        """プロ仕様のグリッド（背景線）を描画"""
        # 代表のコードをここに配属
        # 縦線（時間軸）
        for x in range(0, 10000, 50):
            grid_color = "#3A3A3C" if x % 200 == 0 else "#242424"
            if hasattr(self, 'canvas'):
                self.canvas.draw_line(x, grid_color)
        
        # 横線（音階軸）
        for y in range(0, 1000, 40):
            if hasattr(self, 'canvas'):
                pass

    # --- 1. データ・ファイル管理系 ---
    # (このセクションの主要メソッドは modules/gui/mixins/project_io_mixin.py へ移動済み)

    def update_timeline_with_notes(self, notes_data: list):
        """解析したノートデータをタイムラインウィジェットにセットする"""
        if hasattr(self, 'timeline_widget'):
            # notes_data は辞書のリストを想定
            self.timeline_widget.set_notes(notes_data)
            self.refresh_voice_ui()

        # 実装詳細は midi_manager の保存機能に依存

    # --- 2. 音声・AI処理系 ---

    def preprocess_lyrics(self, text: str, notes: Optional[List[Any]] = None):
        """歌詞の事前処理（平仮名化など）を実行"""
        if hasattr(self, 'text_analyzer') and self.text_analyzer is not None:
            processed = self.text_analyzer.analyze_text(text)
            print(f"歌詞を解析しました: {text} -> {len(processed)}音素")
            return processed
        return []

    def refresh_voice_ui(self):
        """音声設定やタイムラインの表示を最新状態に更新する"""
        self.update() # 再描画
        print("UIをリフレッシュしました。")

    # --- 3. エンジン・モニタリング系 ---

    def run_engine(self, alias: Optional[str] = None, params: Optional[Any] = None):
        """旧API互換のレンダリング入口。実処理はデスクトップUIの共通経路へ委譲する。"""
        del alias, params
        render_fn = getattr(self, "on_render_button_clicked", None)
        if not callable(render_fn):
            raise RuntimeError("レンダリング処理が利用できません。")
        return render_fn()

    @property
    def pro_monitoring(self):
        """プロフェッショナル・モニタリング設定の参照用プロパティ"""
        # エラーログで self.pro_monitoring へのアクセスがあったため定義
        return getattr(self, "_pro_monitoring_enabled", False)

    @pro_monitoring.setter
    def pro_monitoring(self, value: bool):
        self._pro_monitoring_enabled = value
        print(f"Pro Monitoring: {value}")


  #=======================================================
    #ai処理接続

    def on_ai_auto_setup(self):
        """おまかせ調声ボタン"""
        if not self.ai_manager.is_model_loaded():
            QMessageBox.warning(self, "AI未準備", 
                "AIモデルが見つかりません。\n"
                "train_aural_model.py を実行してください。")
            return
    
        notes = self.timeline_widget.get_all_notes()
        if not notes:
            return
    
        self.status_bar.showMessage("AI調声中...")
        for note in notes:
            result = self.ai_manager.predict(note)
            note.pre_utterance = result["pre_utterance"]
            note.overlap        = result["overlap"]
            note.consonant      = result["consonant"]
    
        self.timeline_widget.update()
        self.status_bar.showMessage("AI調声完了")

        
    #=======================================================
    # --- Undo / Redo スロット ---
    #======================================================-
    
    @Slot()
    def undo(self):
        """Ctrl+Z で呼び出し"""
        self.history.undo()
        self.statusBar().showMessage("Undo executed")

    @Slot()
    def redo(self):
        """Ctrl+Y で呼び出し"""
        self.history.redo()
        self.statusBar().showMessage("Redo executed")

    def register_edit(self, old_state, new_state, description):
        """状態変化を履歴に登録"""
        def redo_fn(): self.apply_state(new_state)
        def undo_fn(): self.apply_state(old_state)
        self.history.execute(EditCommand(redo_fn, undo_fn, description))

    def apply_state(self, state):
        """状態（ノートリストなど）を反映"""
        assert self.timeline_widget is not None
        self.timeline_widget.set_notes(deepcopy(state))

    @Slot(object, object, str)
    def on_timeline_edit_committed(self, before_state, after_state, description):
        """
        タイムライン上のノート編集（追加/削除/移動/リサイズ/ペースト/複製/歌詞編集）
        が確定したときに、その操作をUndo履歴に積む。

        この時点でノート編集自体はすでにUI上で実行済みのため、
        HistoryManager.execute() ではなく push() を使う
        (execute() は内部で redo_func() を呼んでしまい、操作が二重に
        実行されてしまうため)。
        """
        def redo_fn():
            self.timeline_widget._restore_notes_snapshot(after_state)

        def undo_fn():
            self.timeline_widget._restore_notes_snapshot(before_state)

        self.history.push(EditCommand(redo_fn, undo_fn, description))

    # --- マルチトラック操作 ---

    def add_track(self, t_type="vocal"):
        """新規トラックの追加と履歴登録"""
        count = len(self.tracks) + 1
        name = f"Vocal {count}" if t_type == "vocal" else f"Audio {count}"
        new_track = VoseTrack(name, t_type)

        def redo_fn():
            self.tracks.append(new_track)
            self.refresh_track_list_ui()
            new_idx = len(self.tracks) - 1
            # setCurrentRow() は currentRowChanged 経由で switch_track() を
            # 暗黙的に再発火させてしまう場合があり、下の明示的な switch_track()
            # 呼び出しと二重実行になって notes が壊れることがあるため、
            # blockSignals で選択行の更新だけ行い、画面同期は明示的な呼び出しに一本化する
            self.track_list_widget.blockSignals(True)
            self.track_list_widget.setCurrentRow(new_idx)
            self.track_list_widget.blockSignals(False)
            self.switch_track(new_idx)
            if t_type == "wave":
                self.load_audio_for_track(new_track)

        def undo_fn():
            if new_track in self.tracks:
                removed_idx = self.tracks.index(new_track)
                was_current = (removed_idx == self.current_track_idx)
                self.tracks.remove(new_track)
                self.refresh_track_list_ui()

                if self.tracks:
                    if was_current:
                        # 削除したトラックがまさに選択中だった場合、
                        # timeline_widget の中身は「削除されたトラックの
                        # 残骸」であり、どのトラックの内容としても正しくない。
                        # switch_track() 経由だとこの残骸が退避先トラックの
                        # notes を上書きしてしまうため、退避処理を経由せず
                        # 直接ロードのみ行う。
                        new_idx = min(removed_idx, len(self.tracks) - 1)
                        self.current_track_idx = new_idx
                        # setCurrentRow() が switch_track() を暗黙的に再発火させ、
                        # 直後の直接ロードと二重実行になって notes を壊すことを防ぐ
                        self.track_list_widget.blockSignals(True)
                        self.track_list_widget.setCurrentRow(new_idx)
                        self.track_list_widget.blockSignals(False)
                        target_tr = self.tracks[new_idx]
                        if self.timeline_widget:
                            self.timeline_widget.set_notes(target_tr.notes)
                    else:
                        # 選択中のトラックは削除されていないので、通常の
                        # switch_track() で安全に退避→ロードできる。
                        new_idx = min(self.current_track_idx, len(self.tracks) - 1)
                        self.track_list_widget.blockSignals(True)
                        self.track_list_widget.setCurrentRow(new_idx)
                        self.track_list_widget.blockSignals(False)
                        self.switch_track(new_idx)
                else:
                    # 最後の1トラックを削除した場合、選択中のインデックスも
                    # 安全な値に戻す(refresh_ui側でも防御しているが、
                    # ここで揃えておくことでUIの選択状態を正しく保つ)
                    self.current_track_idx = 0
                    if self.timeline_widget:
                        self.timeline_widget.set_notes([])

        # 履歴にコマンドを登録して実行
        self.history.execute(EditCommand(redo_fn, undo_fn, f"Add {name}"))

    def switch_track(self, index: int) -> None:
        """
        トラック切り替え時のデータ保護と読み込み。
        代表の設計に基づき、編集中のデータを退避させてから新しいトラックをロードします。
        """
        # 1. 境界チェック（絶対に安全に）
        # self.tracks がリストであることを型ヒントで保証
        tracks_list: List[Any] = getattr(self, 'tracks', [])
        if index < 0 or index >= len(tracks_list):
            return

        # 2. 現在の編集状態を今のトラックに退避
        # self.current_track_idx の妥当性をチェック
        curr_idx: int = getattr(self, 'current_track_idx', 0)
        if 0 <= curr_idx < len(tracks_list):
            current_tr = tracks_list[curr_idx]
            # timeline_widget の存在を確認してデータをコピー
            t_widget = getattr(self, 'timeline_widget', None)
            if t_widget is not None:
                # deepcopyにより、切り替え後に元データが壊れるのを防ぐ（代表の安全設計）
                current_tr.notes = deepcopy(t_widget.notes_list)

        # 3. 新しいトラックの取得とインデックス更新
        self.current_track_idx = index
        target_tr = tracks_list[index]

        # 4. タイムラインへデータをロード
        # 1140行目のエラー対策: target_tr が辞書ではなく、
        # プロパティ(notes, name等)を持つオブジェクトであることを確実にする
        if hasattr(self, 'timeline_widget') and self.timeline_widget is not None:
            self.timeline_widget.set_notes(target_tr.notes)
            # 背景の波形などを再描画
            if self.timeline_widget: 
                self.timeline_widget.update()

        # 5. 各ストリップの選択枠・ディミング状態を更新
        self._sync_track_strips()

        # 6. ステータスバー更新
        tr_name = getattr(target_tr, 'name', f"Track {index+1}")
        msg = f"Track: {tr_name}"
        
        # track_type が wave の場合はファイル名も表示
        tr_type = getattr(target_tr, 'track_type', "midi")
        tr_audio = getattr(target_tr, 'audio_path', "")
        if tr_type == "wave" and tr_audio:
            msg += f" (File: {os.path.basename(tr_audio)})"
            
        self.statusBar().showMessage(msg)

    def load_audio_for_track(self, track):
        """Audioトラックにファイルを読み込み、波形解析をキックする"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "伴奏を選択", "", "Audio Files (*.wav *.mp3)"
        )
        if file_path:
            track.audio_path = file_path
            track.name = os.path.basename(file_path)
            
            # 重要：読み込み時に一度解析させてキャッシュを作る
            # TimelineWidgetのメソッドを呼び出してピークを取得
            track.vose_peaks = self.timeline_widget.get_audio_peaks(file_path)
            
            self.refresh_track_list_ui()
            if self.timeline_widget: 
                self.timeline_widget.update()
            self.statusBar().showMessage(f"Loaded: {track.name}")

    def refresh_ui(self):
        """Undo/Redo後に現在のトラック状態を画面に同期"""
        if not self.tracks:
            # トラックが1件も無い状態（全トラックがUndoで消えた等）。
            # タイムラインを空にして安全に終了する。
            if self.timeline_widget:
                self.timeline_widget.set_notes([])
            self.update()
            return

        # current_track_idx がトラック削除等で範囲外になっている場合に備える
        if self.current_track_idx >= len(self.tracks):
            self.current_track_idx = len(self.tracks) - 1

        current_notes = self.tracks[self.current_track_idx].notes
        self.timeline_widget.set_notes(current_notes)
        self.update()

    # --- 保存（マルチトラック対応） ---
    # (旧: save_project は modules/gui/mixins/project_io_mixin.py へ移動済み)

    def get_active_tracks(self):
        """現在鳴らすべきトラックのリストを返す（再生エンジン用）"""
        # ソロがあるかチェック
        solo_exists = any(t.is_solo for t in self.tracks)
        
        active_tracks = []
        for t in self.tracks:
            if solo_exists:
                # ソロがあるなら、ソロがONかつミュートでないものだけ
                if t.is_solo and not t.is_muted:
                    active_tracks.append(t)
            else:
                # ソロがないなら、ミュートでないものすべて
                if not t.is_muted:
                    active_tracks.append(t)
        return active_tracks

    def refresh_track_list_ui(self):
        """
        トラックリストの全項目を TrackStripWidget で再構築する。
        トラック追加・削除・並べ替えのタイミングで呼ばれる。
        """
        if not self.track_list_widget:
            return

        from PySide6.QtWidgets import QListWidgetItem
        from PySide6.QtCore import QSize

        self.track_list_widget.blockSignals(True)
        self.track_list_widget.clear()

        solo_exists = any(t.is_solo for t in self.tracks)

        for i, t in enumerate(self.tracks):
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, 64))
            self.track_list_widget.addItem(item)

            strip = TrackStripWidget(t, self)
            strip.selected.connect(self._on_strip_selected)
            strip.mute_toggled.connect(self._on_strip_mute)
            strip.solo_toggled.connect(self._on_strip_solo)
            strip.volume_changed.connect(self._on_strip_volume)
            strip.pan_changed.connect(self._on_strip_pan)
            strip.name_edited.connect(self._on_strip_renamed)

            # 選択中ハイライトとディミングの初期状態を反映
            strip.set_current(i == self.current_track_idx)
            strip.set_dimmed(t.is_muted or (solo_exists and not t.is_solo))

            self.track_list_widget.setItemWidget(item, strip)

        # 現在行を同期
        if 0 <= self.current_track_idx < self.track_list_widget.count():
            self.track_list_widget.setCurrentRow(self.current_track_idx)

        self.track_list_widget.blockSignals(False)

    def _sync_track_strips(self):
        """全ストリップの選択状態とディミング（M/S）を再計算して更新する"""
        if not self.track_list_widget:
            return
        solo_exists = any(t.is_solo for t in self.tracks)
        for i in range(self.track_list_widget.count()):
            item = self.track_list_widget.item(i)
            strip = self.track_list_widget.itemWidget(item)
            if strip and isinstance(strip, TrackStripWidget):
                t = self.tracks[i] if i < len(self.tracks) else None
                strip.set_current(i == self.current_track_idx)
                if t:
                    strip.set_dimmed(t.is_muted or (solo_exists and not t.is_solo))

    def _on_strip_selected(self, track):
        if track in self.tracks:
            idx = self.tracks.index(track)
            if idx != self.current_track_idx:
                self.track_list_widget.setCurrentRow(idx)

    def _record_track_property_change(self, track, attr: str, old_value: Any, new_value: Any, description: str) -> None:
        """Mixer/track property変更を既に適用済みの状態からUndo履歴へ登録する。"""
        if old_value == new_value:
            return

        def apply(value):
            setattr(track, attr, value)
            self.refresh_track_list_ui()
            if track in self.tracks and self.tracks.index(track) == self.current_track_idx:
                if attr in {"volume", "pan", "is_muted", "is_solo"}:
                    mixer = getattr(self, "audio_mixer", None)
                    if mixer is not None:
                        mixer.update_tracks(self.tracks)

        def redo():
            apply(new_value)

        def undo():
            apply(old_value)

        self.history.push(EditCommand(redo, undo, description))

    def _on_strip_mute(self, track, is_muted: bool):
        if track in self.tracks:
            old_value = bool(getattr(track, "is_muted", False))
            new_value = bool(is_muted)
            track.is_muted = new_value
            self._sync_track_strips()
            self._record_track_property_change(
                track, "is_muted", old_value, new_value, "トラックMute変更"
            )
            self.statusBar().showMessage(f"{track.name} Muted: {new_value}")

    def _on_strip_solo(self, track, is_solo: bool):
        if track in self.tracks:
            old_value = bool(getattr(track, "is_solo", False))
            new_value = bool(is_solo)
            track.is_solo = new_value
            self._sync_track_strips()
            self._record_track_property_change(
                track, "is_solo", old_value, new_value, "トラックSolo変更"
            )
            self.statusBar().showMessage(f"{track.name} Solo: {new_value}")

    def _on_strip_volume(self, track, value: float):
        if track in self.tracks:
            old_value = float(getattr(track, "volume", 1.0))
            new_value = float(value)
            track.volume = new_value
            mixer = getattr(self, "audio_mixer", None)
            if mixer is not None:
                mixer.update_tracks(self.tracks)
            self._record_track_property_change(
                track, "volume", old_value, new_value, "トラックVolume変更"
            )
            self.statusBar().showMessage(f"{track.name} Volume: {int(new_value * 100)}%")

    def _on_strip_pan(self, track, value: float):
        if track in self.tracks:
            old_value = float(getattr(track, "pan", 0.0))
            new_value = max(-1.0, min(1.0, float(value)))
            track.pan = new_value

            mixer = getattr(self, "audio_mixer", None)
            if mixer is not None:
                mixer.update_tracks(self.tracks)

            p_str = (
                f"L{int(abs(new_value) * 100)}"
                if new_value < -0.01
                else (f"R{int(new_value * 100)}" if new_value > 0.01 else "C")
            )
            self._record_track_property_change(
                track, "pan", old_value, new_value, "トラックPan変更"
            )
            self.statusBar().showMessage(f"{track.name} Pan: {p_str}")

    def _on_strip_renamed(self, track, new_name: str):
        if track in self.tracks:
            old_value = str(getattr(track, "name", ""))
            new_value = str(new_name).strip()
            if not new_value:
                return
            track.name = new_value
            self._record_track_property_change(
                track, "name", old_value, new_value, "トラック名変更"
            )
            self.statusBar().showMessage(f"Track renamed to: {new_value}")


    def init_audio_playback(self):
        """Compatibility entry point for the unified desktop AudioMixer."""
        self.setup_audio_interface()
    def sync_ui_to_audio(self, ms: int) -> None:
        """Synchronize the timeline cursor with the unified mixer position."""
        mixer = getattr(self, "audio_mixer", None)
        if mixer is None or not getattr(mixer, "is_playing", False):
            return
        current_sec = float(ms) / 1000.0
        timeline = getattr(self, "timeline_widget", None)
        if timeline is not None:
            timeline._current_playback_time = current_sec
            timeline.update()

    @Slot(object)
    def on_playback_state_changed(self, state: Any) -> None:
        """Compatibility callback for legacy QMediaPlayer callers."""
        is_playing = bool(state)
        self.is_playing = is_playing
        play_btn = getattr(self, "play_btn", None)
        if play_btn is not None:
            play_btn.setText("⏸ 停止" if is_playing else "▶ 再生")

    def setup_audio_interface(self) -> None:
        """Initialize the single desktop callback-based AudioMixer path."""
        from modules.backend.audio_mixer import AudioMixer

        self.audio_mixer = AudioMixer(sample_rate=44100)
        self.audio_player = self.audio_mixer
        self.audio_output = None
    def get_current_playback_state(self) -> bool:
        """Return whether the unified desktop mixer is actively playing."""
        mixer = getattr(self, "audio_mixer", None)
        return bool(mixer is not None and getattr(mixer, "is_playing", False))

    def on_synthesize(self, notes):
        """旧API互換の合成入口。レンダーは1回だけ共通経路へ委譲する。"""
        if not notes:
            return None

        render_fn = getattr(self, "on_render_button_clicked", None)
        if not callable(render_fn):
            raise RuntimeError("レンダリング処理が利用できません。")
        return render_fn()

    def init_vcv_logic(self):
        """起動時に一度だけ。MainWindowの__init__から呼び出してください"""
        self.vowel_groups = {
            'a': 'あかさたなはまやらわがざだばぱぁゃ',
            'i': 'いきしちにひみりぎじぢびぴぃ',
            'u': 'うくすつぬふむゆるぐずづぶぷぅゅ',
            'e': 'えけせてねへめれげぜでべぺぇ',
            'o': 'おこそとのほもよろをごぞどぼぽぉょ',
            'n': 'ん'
        }

    # =============================================================
    # 診断されたプロバイダーを使用してAIモデルをロードする                                      
    # =============================================================

    def setup_aural_ai(self):
        """診断されたプロバイダーを使用してAIモデルをロードする"""
        import os
        # モデル未搭載環境でも再生UI全体が壊れないよう、常に明示的なNone状態を持つ。
        self.ai_session = None
        model_path = "models/aural_dynamics.onnx"

        if ort is None:
            self.log_startup("Aural AI disabled: onnxruntime is not installed.")
            return
    
        if not os.path.exists(model_path):
            self.statusBar().showMessage("Error: Aural AI model not found.")
            return

        try:
            # 1. 診断済みのプロバイダー（NPU等）をセッションに渡す
            # セッションオプションの設定（スレッド数などをCore i3向けに最適化）
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1  # 信号処理との競合を避けるため1に固定
        
            self.ai_session = ort.InferenceSession(
                model_path, 
                sess_options=options,
                providers=[self.active_provider, 'CPUExecutionProvider'] # NPUがダメならCPU
            )
        
            self.log_startup(f"Aural AI binding successful on {self.active_provider}")
        
        except Exception as e:
            self.log_startup(f"AI Binding Failed: {e}")
            # 最終防衛線としてCPUで再試行
            self.ai_session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])

    # =============================================================
    # DSP CONTROL: PRECISION EQUALIZER (No-Noise Logic)
    # =============================================================

    def apply_dsp_equalizer(self, frequency=8000.0, gain=3.0, Q=1.0):
        """
        DSP技術による「無ノイズ」イコライザー設定。
        AI合成で発生しがちな「高域のチリチリ音」を物理数学的に除去します。
        """
        # 1. サンプリングレート取得 (44100Hz等)
        fs = 44100.0
    
        # 2. DSPフィルタ係数の計算 (Bi-quad Filter設計)
        A = math.pow(10, gain / 40)
        omega = 2 * math.pi * frequency / fs
        sn = math.sin(omega)
        cs = math.cos(omega)
        alpha = sn / (2 * Q)

        # フィルタの「キレ」を決める5つの係数
        b0 = A * ((A + 1) + (A - 1) * cs + 2 * math.sqrt(A) * alpha)
        b1 = -2 * A * ((A - 1) + (A + 1) * cs)
        b2 = A * ((A + 1) + (A - 1) * cs - 2 * math.sqrt(A) * alpha)
        a0 = (A + 1) - (A - 1) * cs + 2 * math.sqrt(A) * alpha
        a1 = 2 * ((A - 1) - (A + 1) * cs)
        a2 = (A + 1) - (A - 1) * cs - 2 * math.sqrt(A) * alpha

        # 3. C++エンジンへ係数を転送
        if hasattr(self, 'vo_se_engine') and hasattr(self.vo_se_engine, 'lib'):
            self.vo_se_engine.lib.vose_update_dsp_filter(
                float(b0/a0), float(b1/a0), float(b2/a0), 
                float(a1/a0), float(a2/a0)
            )
    
        self.statusBar().showMessage(f"DSP EQ Active: {frequency}Hz Optimized.")

    #===========================================================
    #エンジン接続関係
    #===========================================================

    def init_vose_engine(self):
        """C++エンジンのロードと初期設定（OS横断・core_manager経由）"""
        self.engine_dll = vose_manager.get_lib()
        if self.engine_dll:
            print("✅ Engine Loaded Successfully.")
        else:
            print("❌ Engine DLL not found or failed to load.")

    def generate_pitch_curve(self, note, prev_note=None):
        """
        [完全版] AI予測ピッチ + 黄金比ポルタメント + ビブラート
        """
        import numpy as np      
        import math
        # 1. 基礎となる音程（Hz）の計算
        target_hz = 440.0 * (2.0 ** ((note.note_number - 69) / 12.0))
        
        # フレーム数計算（5ms = 1フレーム。1.0秒なら200フレーム）
        num_frames = max(1, int((note.duration * 1000.0) / 5.0))
        
        # AIが予測したピッチ曲線があればそれをベースにし、なければ定数で初期化
        if hasattr(note, 'dynamics') and 'pitch' in note.dynamics:
            curve = np.array(note.dynamics['pitch'], dtype=np.float64)
        else:
            curve = np.ones(num_frames, dtype=np.float64) * target_hz

        # 2. ポルタメント（前の音からの滑らかな接続）
        if prev_note:
            prev_hz = 440.0 * (2.0 ** ((prev_note.note_number - 69) / 12.0))
            # ノートの最初の15%を使って滑らかに繋ぐ（黄金比的な減衰）
            port_len = min(int(num_frames * 0.15), 40)
            if port_len > 0:
                # 指数関数的にターゲットに近づけることで人間らしさを出す
                t = np.linspace(0, 1, port_len)
                curve[:port_len] = prev_hz + (target_hz - prev_hz) * (1 - np.exp(-5 * t))

        # 3. ビブラート・ロジック
        vibrato_depth = 6.0  # Hz単位の揺れ幅
        vibrato_rate = 5.5   # 1秒間に5.5回
        
        # ノートの後半50%からビブラートを開始
        vib_start = int(num_frames * 0.5)
        for i in range(vib_start, num_frames):
            time_sec = i * 0.005 # 5ms単位
            osc = math.sin(2 * math.pi * vibrato_rate * time_sec)
            curve[i] += osc * vibrato_depth

        return curve

    def get_notes_from_timeline(self):
        """
        [完全実装] ピアノロール上の全音符をスキャンし、演奏データへと変換する
        """
        note_events = []
        
        # 1. ピアノロールの「シーン」から全アイテムを取得
        if not hasattr(self, 'piano_roll_scene') or self.piano_roll_scene is None:
            self.log_startup("Error: Piano roll scene not initialized.")
            return []

        all_items = self.piano_roll_scene.items()
        
        # 2. 音符アイテム（NoteItemクラス）だけをフィルタリング
        raw_notes = []
        for item in all_items:
            if hasattr(item, 'is_note_item') and item.is_note_item:
                raw_notes.append(item)

        # 3. 時間軸（X座標）でソート
        raw_notes.sort(key=lambda x: x.x())

        # 4. GUI上の物理量を「音楽的データ」に変換
        for item in raw_notes:
            start_time = item.x() / 100.0  
            duration = item.rect().width() / 100.0
            
            # 歌詞（あ）を音素（a）に変換
            phoneme_label = self.convert_lyrics_to_phoneme(item.lyrics)

            # C++構造体 NoteEvent を作成
            event = NoteEvent(
                phonemes=phoneme_label,
                note_number=item.note_number,
                duration=duration,
                start_time=start_time,
                velocity=item.velocity
            )
            note_events.append(event)

        self.log_startup(f"Timeline Scan: {len(note_events)} notes collected.")
        return note_events

    def convert_lyrics_to_phoneme(self, lyrics):
        """簡単な歌詞→音素変換（辞書）"""
        dic = {"あ": "a", "い": "i", "う": "u", "え": "e", "お": "o"}
        return dic.get(lyrics, "n") # 見つからなければ「ん」にする

    def handle_playback(self):
        """
        [究極統合] AI推論・競合回避・DSP処理を一本化した再生メインフロー
        """
        import os
        import time
        # 1. タイムラインから音符データを取得
        notes = self.get_notes_from_timeline()
        if not notes:
            self.statusBar().showMessage("No notes to play.", 3000)
            return

        try:
            self.statusBar().showMessage("Aural AI is thinking...")

            # 2. 【脳】AI推論ループ
            prev = None
            for n in notes:
                # AIに歌い方の設計図を予測させる
                n.dynamics = self.predict_dynamics(n.phonemes, n.note_number)
                # AIの予測をベースに、さらに滑らかなピッチ曲線を生成
                n.pitch_curve = self.generate_pitch_curve(n, prev)
                prev = n

            # 3. 【安全性】ファイルロック回避のためのキャッシュ名生成
            os.makedirs("cache", exist_ok=True)
            temp_wav = os.path.abspath(f"cache/render_{int(time.time() * 1000)}.wav")

            # 4. 【喉】C++レンダリング実行
            final_file = self.synthesize(notes, temp_wav)

            # 5. 【磨き】DSP処理 & 再生
            if final_file and os.path.exists(final_file):
                # 合成後に高域ノイズを除去するDSP EQを適用
                self.apply_dsp_equalizer(frequency=8000.0, gain=-2.0)
                
                # 音を鳴らす
                self.play_audio(final_file)
                self.statusBar().showMessage(f"Playing via {self.active_device}", 5000)

        except Exception as e:
            error_msg = f"Playback Failed: {str(e)}"
            self.log_startup(error_msg)
            self.statusBar().showMessage(error_msg, 10000)

    def predict_dynamics(self, phonemes, notes):
        """AIモデル(ONNX)を使用してパラメータを予測"""
        # [前処理] 歌詞をAIが理解できる数値に変換
        input_data = self.preprocess_lyrics(phonemes, notes)

        # AIモデルが無い環境は通常運用であり、AttributeErrorにせず
        # 入力と同じ形状の中立値を返して再生処理を継続する。
        session = getattr(self, "ai_session", None)
        if session is None:
            return np.zeros_like(input_data, dtype=np.float32)

        try:
            inputs = {session.get_inputs()[0].name: input_data}
            prediction = session.run(None, inputs)
            if not prediction:
                return np.zeros_like(input_data, dtype=np.float32)
            return prediction[0]
        except Exception as e:
            self.log_startup(f"AI dynamics inference failed; using neutral fallback: {e}")
            return np.zeros_like(input_data, dtype=np.float32)

    def synthesize_voice(self, dynamics_data):
        """AIの結果をC++に投げてスピーカーから鳴らす"""
        self.statusBar().showMessage("Rendering via Aural Engine...")

        try:
            # 1. C++ DLLのレンダリング関数を叩く
            if self.engine_dll is None:
                self.log_startup("Synthesis Error: engine DLL is not loaded")
                return
            raw_audio = self.engine_dll.render(dynamics_data)
            
            # 2. sounddevice で再生（ノンブロッキング）
            import sounddevice as sd
            sd.play(raw_audio, samplerate=44100)
            
            self.statusBar().showMessage(f"Playing on {self.active_device}", 3000)
        except Exception as e:
            self.log_startup(f"Synthesis Error: {e}")

    def synthesize(self, notes, output_path="output.wav"):
        """
        スレッドセーフなレンダリングと完璧なメモリ管理。
        GC（ガベージコレクション）からNumPy配列を保護します。
        """
        import numpy as np
        import ctypes
    
        # 1. 入力検証
        if not notes:
            print("エラー: レンダリングするノートがありません")
            return None

        note_count = len(notes)
    
        # 2. C++構造体配列の確保
        cpp_notes_array = (NoteEvent * note_count)()
    
        # 3. 【重要】GCからNumPy配列を保護するリスト
        # このリストが存在する限り、配列はメモリに保持される
        keep_alive = []

        try:
            # 4. 各ノートのデータを構造体に変換
            for i, note in enumerate(notes):
                # ピッチカーブの準備（常にfloat64）
                if hasattr(note, 'pitch_curve') and note.pitch_curve:
                    p_curve = np.array(note.pitch_curve, dtype=np.float64)
                else:
                    # デフォルトのピッチカーブ
                    p_curve = np.array([440.0], dtype=np.float64)
            
                # GC保護リストに追加
                keep_alive.append(p_curve)
            
                # その他のパラメータカーブ（DSP最適化済み標準値）
                curve_length = len(p_curve)
                g_curve = np.full(curve_length, 0.5, dtype=np.float64)  # Gender
                t_curve = np.full(curve_length, 0.5, dtype=np.float64)  # Tension
                b_curve = np.full(curve_length, 0.0, dtype=np.float64)  # Breath
            
                # すべてのカーブをGC保護
                keep_alive.extend([g_curve, t_curve, b_curve])

                # 5. C++構造体へのポインタ転送
                # 音素情報
                phoneme_str = getattr(note, 'phonemes', 'a')
                cpp_notes_array[i].wav_path = phoneme_str.encode('utf-8')
            
                # ピッチカーブ
                cpp_notes_array[i].pitch_curve = p_curve.ctypes.data_as(
                    ctypes.POINTER(ctypes.c_double)
                )
                cpp_notes_array[i].pitch_length = curve_length
            
                # その他のカーブ
                cpp_notes_array[i].gender_curve = g_curve.ctypes.data_as(
                    ctypes.POINTER(ctypes.c_double)
                )
                cpp_notes_array[i].tension_curve = t_curve.ctypes.data_as(
                    ctypes.POINTER(ctypes.c_double)
                )
                cpp_notes_array[i].breath_curve = b_curve.ctypes.data_as(
                    ctypes.POINTER(ctypes.c_double)
                )

            # 6. C++エンジンでレンダリング実行
            if not hasattr(self, 'engine_dll') or not self.engine_dll:
                print("エラー: C++エンジンがロードされていません")
                return None
            
            result_code = self.engine_dll.execute_render(
                cpp_notes_array,
                note_count,
                output_path.encode('utf-8'),
                0
            )
        
            # 7. 結果チェック
            if result_code == 0:
                 print(f"レンダリング成功: {output_path}")
                 return output_path
            else:
                print(f"レンダリング失敗: エラーコード {result_code}")
                return None
            
        except Exception as e:
            print(f"重大なエンジンエラー: {e}")
            import traceback
            traceback.print_exc()
            return None
        
        finally:
            # 8. レンダリング終了後に安全にメモリ解放
            # keep_alive が削除されることで、参照カウントが減り、
            # Pythonのガベージコレクタが適切に処理する
            del keep_alive
            del cpp_notes_array
            print("メモリクリーンアップ完了")


    def on_notes_updated(self):
        """タイムラインが変更された時の処理（オートセーブなど）"""
        pass

    def play_audio(self, path: str) -> None:
        """Play a rendered WAV through the unified desktop AudioMixer."""
        if not path or not os.path.exists(path):
            print(f"エラー: ファイルが見つかりません: {path}")
            return
        mixer = getattr(self, "audio_mixer", None)
        play_file = getattr(mixer, "play_file", None)
        if not callable(play_file):
            print("警告: AudioMixer が初期化されていません")
            return
        try:
            play_file(os.path.abspath(path))
            self.is_playing = True
            self.current_playback_time = 0.0
            self.playback_timer.start()
            print(f"再生開始: {path}")
        except Exception as exc:
            self.is_playing = False
            print(f"再生エラー: {exc}")
    def _check_for_updates(self):
        try:
            import importlib
            updater_mod = importlib.import_module("modules.updater.auto_updater")  # type: ignore
            UpdateChecker = updater_mod.UpdateChecker
        except (ImportError, ModuleNotFoundError):
            return
        checker = UpdateChecker()
        checker.check_async(self._on_update_result)
        
    def _on_update_result(self, has_update, latest_ver, page_url, exe_url):
        if not has_update:
            return

        from PySide6.QtCore import QTimer

        # 型ヒントの修正と変数名のリネーム
        self._pending_update = (latest_ver, page_url, exe_url)

        # 0ミリ秒後にメインスレッドで安全にダイアログを表示
        QTimer.singleShot(0, self._show_update_dialog)

    @Slot()
    def _show_update_dialog(self):
        ver, page_url, exe_url = self._pending_update
        reply = QMessageBox.question(
            self, "アップデートあり",
            f"VO-SE Pro {ver} が公開されています。\n今すぐ更新しますか？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            if exe_url:
                self._start_auto_download(exe_url)
            else:
                import webbrowser
                webbrowser.open(page_url)

    def _start_auto_download(self, url):
        try:
            import importlib
            updater_mod = importlib.import_module("modules.updater.auto_updater")  # type: ignore
            DownloadThread = updater_mod.DownloadThread
            apply_update_and_restart = updater_mod.apply_update_and_restart
        except (ImportError, ModuleNotFoundError):
            import webbrowser
            webbrowser.open(url)
            return
        self._dl_thread = DownloadThread(url)
        self._dl_thread.progress.connect(self.progress_bar.setValue)
        self._dl_thread.finished.connect(apply_update_and_restart)
        self._dl_thread.error.connect(lambda e: QMessageBox.critical(self, "エラー", e))
        self.progress_bar.show()
        self._dl_thread.start()

    # ==========================================================================
    #  Pro audio modeling の起動、呼び出し　　　　　　　　　　　
    # ==========================================================================

    # setup_shortcuts は空の pass のみで、docstring 通り
    # setup_vose_shortcuts に統合済みのため削除した。

    def toggle_audio_monitoring(self, event=None) -> None:
        """
        Spaceキー一発で『音』と『UI』を同時に動かす。
        Actionsログ 2125-2129行目の型推論エラーを完全に回避する防弾仕様。
        """
        # 1. 属性の存在確認と型チェックを同時に行う
        # getattr で取得し、それが期待する「モニタリングオブジェクト」であることを確認
        monitor = getattr(self, 'pro_monitoring', None)
        
        # monitor が None でも bool (False) でも物理的な実体がある場合のみ処理
        if monitor is not None and not isinstance(monitor, bool):
            # 2. 内部属性へのアクセスを hasattr でさらに保護 (AttributeAccessIssue 対策)
            if hasattr(monitor, 'is_playing'):
                # 現在の状態を判定
                current_state = bool(getattr(monitor, 'is_playing', False))
                
                if not current_state:
                    print(" Pro Audio Monitoring: ON")
                    # 各プロパティへの代入を安全に行う
                    if hasattr(monitor, 'current_time'):
                        setattr(monitor, 'current_time', 0.0)
                    
                    # is_playing を True に
                    setattr(monitor, 'is_playing', True)
                    
                    # 3. UI更新メソッドの呼び出し
                    update_func = getattr(monitor, 'update_frame', None)
                    if callable(update_func):
                        update_func()
                else:
                    print(" Pro Audio Monitoring: OFF")
                    # is_playing を False に
                    setattr(monitor, 'is_playing', False)
            else:
                # 属性がない場合のフォールバック（デバッグ用）
                print(" DEBUG: pro_monitoring exists but lacks 'is_playing' attribute.")
        else:
            # エンジンが初期化されていない場合の通知
            print(" WARNING: Pro Audio Monitoring engine is not initialized.")

        # 4. メインウィンドウ側の状態も同期（もし必要であれば）
        if hasattr(self, 'is_playing'):
            # monitor の状態に合わせて self のフラグも更新
            active_monitor = getattr(self, 'pro_monitoring', None)
            if active_monitor is not None and not isinstance(active_monitor, bool):
                self.is_playing = bool(getattr(active_monitor, 'is_playing', False))

    # ==========================================================================
    # VO-SE Pro v1.3.0: 連続音（VCV）解決 ＆ レンダリング準備
    #==========================================================================

    def resolve_target_wav(self, lyric, prev_lyric):
        """前の歌詞から母音を判定し、最適なWAVパスを特定する"""
        vowel_groups = {
            'a': 'あかさたなはまやらわがざだばぱぁゃ',
            'i': 'いきしちにひみりぎじぢびぴぃ',
            'u': 'うくすつぬふむゆるぐずづぶぷぅゅ',
            'e': 'えけせてねへめれげぜでべぺぇ',
            'o': 'おこそとのほもよろをごぞどぼぽぉょ',
            'n': 'ん'
        }

        prev_v = None
        if prev_lyric:
            last_char = prev_lyric[-1]
            for v, chars in vowel_groups.items():
                if last_char in chars:
                    prev_v = v
                    break

        candidates = []
        if prev_v:
            candidates.append(f"{prev_v} {lyric}") # 例: 'a い'
        candidates.append(f"- {lyric}")           # 例: '- い'
        candidates.append(lyric)                   # 例: 'い'

        voice_path = getattr(self.vo_se_engine, 'voice_path', "")
        oto_map = getattr(self.vo_se_engine, 'oto_data', {})

        for alias in candidates:
            if alias in oto_map:
                filename = oto_map[alias].get('wav', f"{lyric}.wav")
                return os.path.join(voice_path, filename)

        return os.path.join(voice_path, f"{lyric}.wav")

    def prepare_rendering_data(self):
        """タイムラインとグラフのデータをエンジン形式にシリアライズ"""
        if not hasattr(self, 'timeline_widget'):
            return None

        notes = self.timeline_widget.notes_list
        if not notes:
            return None

        voice_path = ""
        if hasattr(self, 'voice_manager') and self.voice_manager:
            if hasattr(self.voice_manager, 'get_current_voice_path'):
                voice_path = self.voice_manager.get_current_voice_path()

        render_data = {
            "project_name": "New Project",
            "voice_path": voice_path,
            "tempo": self.timeline_widget.tempo,
            "notes": []
        }

        graph = getattr(self, 'graph_editor_widget', None)
        all_params = getattr(graph, 'all_parameters', {}) if graph else {}

        pitch_events = all_params.get("Pitch", [])
        tension_events = all_params.get("Tension", [])

        # MRO で ProjectIOMixin のダミーが勝つのを回避するため、
        # クラス属性経由で MainWindow 側の実装を明示的に参照する
        sampler = MainWindow._sample_range.__get__(self, MainWindow)

        for note in notes:
            note_info = {
                "lyric": note.lyrics,
                "note_num": note.note_number,
                "start_sec": note.start_time,
                "duration_sec": note.duration,
                "pitch_bend": sampler(pitch_events, note, 64),
                "dynamics": sampler(tension_events, note, 64),
            }
            render_data["notes"].append(note_info)

        return render_data

    def start_playback(self):
        """再生ボタンが押された時のメインエントリ"""
        notes_data = self.prepare_rendering_data()
        
        if not notes_data:
            self.statusBar().showMessage("再生するノートがありません。")
            return

        self.statusBar().showMessage("VCV解析完了。合成を開始します...")
        
        audio_data = self.vo_se_engine.synthesize(notes_data)

        if audio_data is not None and len(audio_data) > 0:
            self.vo_se_engine.play(audio_data)
            self.statusBar().showMessage("再生中 (v1.3.0 VCV Engine)")
        else:
            self.statusBar().showMessage("合成エラー。ログを確認してください。")
    
    # ==========================================================================
    # 初期化メソッド
    #==========================================================================

    def init_dll_engine(self):        
        """C言語レンダリングエンジンの接続（OS横断・core_manager経由）"""
        self.lib = vose_manager.get_lib()
        if self.lib is not None:
            print("✓ Engine core loaded successfully")
        else:
            print("⚠ Warning: libvo_se.dll not found")

    def init_engine(self):
        """エンジンの総合初期化"""
        import os
        #ext = ".dll" if platform.system() == "Windows" else ".dylib"
        #dll_relative_path = os.path.join("bin", f"libvo_se{ext}")
        
        # 音源の自動ロード
        official_voice_path = os.path.join("assets", "voice", "official")
        official_oto_path = os.path.join(official_voice_path, "oto.ini")

        if os.path.exists(official_oto_path):
            print(f"✓ Official voice found: {official_voice_path}")

        try:
            # DLLの読み込み試行
            pass
        except Exception:
            print("⚠ Warning: VOSE core library not available")

    def open_about(self):
        """About画面を表示"""
        dialog = CreditsDialog(self.confirmed_partners, self)
        dialog.exec()

    def clear_layout(self, layout):
        """レイアウト内のウィジェットを安全に全削除"""
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
    


    # ==========================================================================
    # PERFORMANCE CONTROL CENTER (Core i3 Survival Logic)
    # ==========================================================================

    def setup_performance_toggle(self):
        """
        [Strategic Toggle] パフォーマンスモードの初期化。
        リソースの乏しい環境(Core i3等)と、ハイスペック環境を瞬時に最適化します。
        （機能維持・解析エラー根絶版）
        """
        # 1. アクションの生成（代表の設計通り、プロ感を演出）
        self.perf_action = QAction("High-Performance Mode", self)
        self.perf_action.setCheckable(True)
        
        # 初期状態は省電力(False)にしておき、ユーザーが必要に応じてブーストする仕様
        self.perf_action.setChecked(False) 
        self.perf_action.triggered.connect(self.toggle_performance)
        
        # 2. ツールバーへの追加
        # Pylance対策：toolbar を cast して「存在する」と明示し、かつ if で実在確認をします
        # これにより、機能を削らずに reportOptionalMemberAccess エラーを消去します
        toolbar = cast(QToolBar, self.toolbar) if self.toolbar else None
        
        if toolbar:
            # メイン操作部に配置してアクセシビリティを確保
            toolbar.addAction(self.perf_action)

    @Slot(bool)
    def toggle_performance(self, checked):
        """
        パフォーマンスモードの動的切り替え。
        C++エンジン(vose_core)の内部バッファやスレッド優先度を操作します。
        """
        # 1. 動作モードの決定 (1: 高負荷・高品質, 0: 低負荷・安定)
        mode = 1 if checked else 0
        
        # 2. C++エンジン(Shared Library)への安全なアクセス
        try:
            if hasattr(self.vo_se_engine, 'lib'):
                if hasattr(self.vo_se_engine.lib, 'vose_set_performance_mode'):
                    # C言語形式でモードを転送
                    self.vo_se_engine.lib.vose_set_performance_mode(mode)
                
                # [蹂躙ポイント] 省電力モード時は内部バッファを増やして途切れを防ぐなどの追加処理
                if mode == 0 and hasattr(self.vo_se_engine.lib, 'vose_set_buffer_size'):
                    self.vo_se_engine.lib.vose_set_buffer_size(4096) # Core i3向けの安全策
                elif mode == 1 and hasattr(self.vo_se_engine.lib, 'vose_set_buffer_size'):
                    self.vo_se_engine.lib.vose_set_buffer_size(1024) # 高速レスポンス
        except Exception as e:
            print(f"Engine Performance Control Warning: {e}")

        # 3. ユーザーへのフィードバック
        status = "【High-Mode】レンダリング優先" if mode == 1 else "【Power-Save】Core i3最適化モード"
        _ = "#ff4444" if mode == 1 else "#44ff44"
        
        self.statusBar().showMessage(f"System: {status} に切り替えました")
        
        # ログにも残して「まともに動いている」ことを証明
        print(f"Performance Mode Changed to: {mode}")

    

    # ==========================================================================
    # ドラッグ&ドロップ・ZIP解凍（文字化け対策済み）
    # ==========================================================================


            


            


    # ==========================================================================
    # 再生・録音制御
    # ==========================================================================


    def on_click_play(self):
        # タイムラインのデータを渡して合成・再生
        audio = self.vo_se_engine.synthesize(self.timeline_widget.notes_list)
        self.vo_se_engine.play(audio)

    def tart_playback_locked_s(self):
        """
        再生を開始（スレッドロック保持中のみ呼び出し）
        注意: このメソッドは _playback_lock を取得した状態で呼び出す
        """
        # 既に再生中の場合は何もしない
        if self.playback_thread and self.playback_thread.is_alive():
            print("警告: 既に再生スレッドが実行中です")
            return
    
        # 再生フラグを立てる
        self.is_playing = True
    
        # 再生ワーカースレッドを開始
        self.playback_thread = threading.Thread(
            target=self._playback_worker,
            daemon=True,
            name="VO-SE-Playback"
        )
        self.playback_thread.start()
        print("再生スレッド開始")
 
    def _stop_playback_locked(self):
        """
        再生を停止（スレッドロック保持中のみ呼び出し）    
        注意: このメソッドは _playback_lock を取得した状態で呼び出す
        """
        # 再生フラグを下げる
        self.is_playing = False
    
        # スレッドの終了を待機（最大1秒）
        if self.playback_thread:
            self.playback_thread.join(timeout=1.0)
        
            # タイムアウトした場合の警告
            if self.playback_thread.is_alive():
                print("警告: 再生スレッドが1秒以内に終了しませんでした")
        
            self.playback_thread = None
    
        print("再生スレッド停止")

    def _playback_worker(self):
        """
        バックグラウンドで動作する再生ワーカー
      
        このメソッドは別スレッドで実行されます
        """
        try:
            # エンジンの取得
            engine = getattr(self, 'vo_se_engine', None)
            if not engine:
                print("エラー: 再生エンジンが見つかりません")
                return
        
            # 再生処理
            if hasattr(engine, 'play_audio'):
                engine.play_audio()
        
        except Exception as e:
            print(f"再生ワーカーエラー: {e}")
            import traceback
            traceback.print_exc()
    
        finally:
            # 再生終了時にフラグをクリア
            with self._playback_lock:
                self.is_playing = False

    @Slot() 
    def _build_render_parameters(self) -> dict:
        """Normalize current graph parameters for per-track playback renders."""
        graph = getattr(self, "graph_editor_widget", None)
        all_params = getattr(graph, "all_parameters", {}) if graph is not None else {}
        return {
            "Pitch": list(all_params.get("Pitch", [])),
            "Gender": list(all_params.get("Gender", [])),
            "Tension": list(all_params.get("Tension", [])),
            "Breath": list(all_params.get("Breath", [])),
        }

    def _render_vocal_track_for_playback(self, track: Any, track_index: int) -> str:
        """Render one Vocal track to a cached WAV for mixer playback."""
        notes = list(getattr(track, "notes", []) or [])
        if not notes:
            track.playback_path = ""
            track._playback_signature = ""
            return ""

        export_v2 = getattr(self.vo_se_engine, "export_to_wav_v2", None)
        if not callable(export_v2):
            raise RuntimeError("VCV対応の export_to_wav_v2 が利用できません。")

        parameters = self._build_render_parameters()
        signature_items = []
        for note in notes:
            to_dict = getattr(note, "to_dict", None)
            signature_items.append(
                to_dict() if callable(to_dict)
                else getattr(note, "__dict__", repr(note))
            )
        signature = repr((
            signature_items,
            parameters,
            self.current_voice_id,
            float(getattr(self.timeline_widget, "tempo", 120.0)),
        ))

        cache_dir = os.path.join(tempfile.gettempdir(), "vose_playback_cache")
        os.makedirs(cache_dir, exist_ok=True)
        import hashlib
        cache_key = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:24]
        cache_path = os.path.join(cache_dir, f"track_{track_index}_{cache_key}.wav")

        # Check the existing signature before invalidating the previous path.
        # Clearing it first made this cache-hit branch unreachable.
        previous_path = str(getattr(track, "playback_path", "") or "")
        if (getattr(track, "_playback_signature", None) == signature
                and previous_path and os.path.isfile(previous_path)):
            return previous_path

        track.playback_path = ""
        track._playback_signature = ""
        self.statusBar().showMessage(
            f"Vocal {track_index + 1} を再生用にレンダリング中..."
        )
        result = export_v2(
            notes,
            parameters,
            cache_path,
            mode_flag=0,
        )
        if not result:
            raise RuntimeError(f"Vocal {track_index + 1} のWAV生成に失敗しました。")

        result_path = os.path.abspath(str(result))
        if not os.path.isfile(result_path):
            raise RuntimeError(f"Vocal {track_index + 1} のWAV生成に失敗しました。")

        track.playback_path = result_path
        track._playback_signature = signature
        return result_path

    def _prepare_all_vocal_playback(self) -> None:
        """Synchronous compatibility helper; normal transport uses a worker thread."""
        for index, track in enumerate(getattr(self, "tracks", []) or []):
            if getattr(track, "track_type", "") != "vocal":
                continue
            self._render_vocal_track_for_playback(track, index)

    def _set_playback_preparation_ui(self, preparing: bool) -> None:
        """Disable mutable editor controls while a render snapshot is being prepared."""
        self._playback_preparing = bool(preparing)
        for name in (
            "timeline_widget",
            "graph_editor_widget",
            "voice_gallery",
            "character_selector",
            "track_list_widget",
            "render_btn",
        ):
            widget = getattr(self, name, None)
            if widget is not None and hasattr(widget, "setEnabled"):
                widget.setEnabled(not preparing)

        play_btn = getattr(self, "play_btn", None) or getattr(self, "play_button", None)
        if play_btn is not None:
            play_btn.setEnabled(not preparing)
            if preparing:
                play_btn.setText("レンダリング中…")
            else:
                play_btn.setText("▶ 再生")

    @Slot(str)
    def _on_vocal_playback_render_progress(self, message: str) -> None:
        self.statusBar().showMessage(message)

    @Slot(object)
    def _on_vocal_playback_render_failed(self, failure: object) -> None:
        details = failure if isinstance(failure, dict) else {}
        failed_track = details.get("track")
        if failed_track is not None:
            failed_track.playback_path = ""
            failed_track._playback_signature = ""
        self._pending_playback_tracks = None
        self._set_playback_preparation_ui(False)
        message = str(details.get("message") or "再生用レンダリングに失敗しました。")
        status_lbl = getattr(self, "status_label", None)
        if status_lbl is not None:
            status_lbl.setText(f"再生エラー: {message}")
        self._refresh_transport_button_states()
        self.statusBar().showMessage(f"再生エラー: {message}", 5000)

    @Slot(object)
    def _on_vocal_playback_render_prepared(self, results: object) -> None:
        tracks = self._pending_playback_tracks
        start_time = float(getattr(self, "_pending_playback_start_time", 0.0))
        self._pending_playback_tracks = None
        self._set_playback_preparation_ui(False)

        if tracks is None:
            return
        current_tracks = list(getattr(self, "tracks", []) or [])
        if (len(current_tracks) != len(tracks)
                or any(current is not saved for current, saved in zip(current_tracks, tracks))):
            self.statusBar().showMessage(
                "レンダリング中にトラック構成が変わったため、再生を開始しませんでした。", 5000
            )
            return

        for track, path, signature in (results if isinstance(results, list) else []):
            track.playback_path = path
            track._playback_signature = signature

        playable = [
            track for track in tracks
            if str(getattr(track, "playback_path", "") or "")
            or str(getattr(track, "audio_path", "") or "")
        ]
        if not playable:
            self.statusBar().showMessage("再生する音声がありません。", 3000)
            return

        mixer = getattr(self, "audio_mixer", None)
        if mixer is None:
            self.statusBar().showMessage("AudioMixerを初期化できません。", 3000)
            return
        try:
            mixer.set_tracks(tracks)
            mixer.play(start_time)
            self.is_playing = True
            self.current_playback_time = start_time
            self.playback_start_time = start_time
            self.playback_started_monotonic = time.monotonic()
            self.playback_timer.start()
            status_lbl = getattr(self, "status_label", None)
            if status_lbl is not None:
                status_lbl.setText(f"再生中: {start_time:.2f}s")
            self._refresh_transport_button_states()
        except Exception as exc:
            self.is_playing = False
            self.playback_timer.stop()
            status_lbl = getattr(self, "status_label", None)
            if status_lbl is not None:
                status_lbl.setText(f"再生エラー: {exc}")
            self._refresh_transport_button_states()
            self.statusBar().showMessage(f"再生エラー: {exc}", 5000)

    @Slot()
    def _on_vocal_playback_prepare_thread_finished(self) -> None:
        worker = getattr(self, "_playback_prepare_thread", None)
        if worker is not None and not worker.isRunning():
            self._playback_prepare_thread = None

    def on_play_pause_toggled(self):
        """Prepare vocal WAVs in the background, then start the unified mixer."""
        worker = getattr(self, "_playback_prepare_thread", None)
        if worker is not None and worker.isRunning():
            self.statusBar().showMessage("再生用レンダリングを準備中です。", 2000)
            return

        mixer = getattr(self, "audio_mixer", None)
        if mixer is None:
            self.statusBar().showMessage("AudioMixerを初期化できません。", 3000)
            return
        play_btn = cast(QPushButton, getattr(self, "play_btn", None) or getattr(self, "play_button", None))
        status_lbl = cast(QLabel, getattr(self, "status_label", None))
        if mixer.is_playing:
            mixer.stop()
            self.is_playing = False
            self.playback_timer.stop()
            if play_btn is not None:
                play_btn.setText("▶ 再生")
            if status_lbl is not None:
                status_lbl.setText("停止しました")
            self._refresh_transport_button_states()
            return

        if getattr(self, "current_worker", None) is not None:
            self.statusBar().showMessage(
                "書き出し中です。完了またはキャンセル後に再生できます。", 3000
            )
            return

        tracks = list(getattr(self, "tracks", []) or [])
        try:
            timeline = cast(Any, getattr(self, "timeline_widget", None))
            current_idx = int(getattr(self, "current_track_idx", 0))
            current_track = tracks[current_idx] if 0 <= current_idx < len(tracks) else None
            if current_track is not None and getattr(current_track, "track_type", "") == "vocal":
                current_track.notes = deepcopy(
                    getattr(timeline, "notes_list", []) if timeline is not None else []
                )

            parameters = deepcopy(self._build_render_parameters())
            voice_id = getattr(self, "current_voice_id", None)
            tempo = float(getattr(timeline, "tempo", 120.0)) if timeline is not None else 120.0
            jobs = []
            for index, track in enumerate(tracks):
                if getattr(track, "track_type", "") != "vocal":
                    continue
                # Reuse the current track's already-isolated snapshot to avoid
                # copying a long UST twice on the GUI thread.
                source_notes = getattr(track, "notes", []) or []
                notes_snapshot = (
                    source_notes if track is current_track else deepcopy(source_notes)
                )
                jobs.append({
                    "index": index,
                    "track": track,
                    "notes": notes_snapshot,
                    "parameters": parameters,
                    "voice_id": voice_id,
                    "tempo": tempo,
                    "previous_signature": getattr(track, "_playback_signature", ""),
                    "previous_path": getattr(track, "playback_path", ""),
                })

            if not any(
                (job["notes"] for job in jobs)
            ) and not any(str(getattr(track, "audio_path", "") or "") for track in tracks):
                self.statusBar().showMessage("再生する音声がありません。", 3000)
                return

            self._pending_playback_tracks = tracks
            self._pending_playback_start_time = (
                float(getattr(timeline, "_current_playback_time", 0.0))
                if timeline is not None else 0.0
            )
            self._set_playback_preparation_ui(True)

            worker = _VocalPlaybackPreparationThread(
                getattr(self, "vo_se_engine", None), jobs, self
            )
            worker.completed.connect(self._on_vocal_playback_render_prepared)
            worker.failed.connect(self._on_vocal_playback_render_failed)
            worker.progress.connect(self._on_vocal_playback_render_progress)
            worker.finished.connect(self._on_vocal_playback_prepare_thread_finished)
            worker.finished.connect(worker.deleteLater)
            self._playback_prepare_thread = worker
            worker.start()
        except Exception as exc:
            self._pending_playback_tracks = None
            self._set_playback_preparation_ui(False)
            if status_lbl is not None:
                status_lbl.setText(f"再生エラー: {exc}")
            self._refresh_transport_button_states()
            self.statusBar().showMessage(f"再生エラー: {exc}", 5000)
    @Slot()
    def on_record_toggled(self):
        """録音開始/停止"""
        self.is_recording = not self.is_recording

        if self.is_recording:
            if getattr(self, 'is_playing', False):
                self.on_play_pause_toggled()

            if hasattr(self, 'record_button'):
                self.record_button.setText("■ 録音中")

            if hasattr(self, 'status_label'):
                self.status_label.setText("録音開始 - MIDI入力待機中...")

            if hasattr(self, 'timeline_widget'):
                self.timeline_widget.set_recording_state(True, time.time())
        else:
            if hasattr(self, 'record_button'):
                self.record_button.setText("● 録音")

            if hasattr(self, 'status_label'):
                self.status_label.setText("録音停止")

            if hasattr(self, 'timeline_widget'):
                self.timeline_widget.set_recording_state(False, 0.0)

    @Slot()
    def on_loop_button_toggled(self):
        """ループ再生切り替え"""
        self.is_looping_selection = not self.is_looping_selection
        self.is_looping = self.is_looping_selection

        loop_button = getattr(self, 'loop_button', None)
        if loop_button is not None:
            loop_button.setText("ループ: ON" if self.is_looping else "ループ: OFF")

        self._refresh_transport_button_states()
        message = "ループ再生を有効にしました" if self.is_looping else "ループ再生を無効にしました"
        status_label = getattr(self, 'status_label', None)
        if status_label is not None:
            status_label.setText(message)
        else:
            self.statusBar().showMessage(message, 3000)


    def stop_and_clear_playback(self) -> None:
        """Stop the unified mixer and reset transport state/UI."""
        mixer = getattr(self, "audio_mixer", None)
        stop_mixer = getattr(mixer, "stop", None)
        if callable(stop_mixer):
            stop_mixer()
        self.audio_player = mixer
        self.playback_timer.stop()
        self.is_playing = False
        self.current_playback_time = 0.0
        self.playback_start_time = 0.0
        self.playback_started_monotonic = 0.0
        self.is_looping = False
        self.is_looping_selection = False
        timeline = getattr(self, "timeline_widget", None)
        if timeline is not None:
            if hasattr(timeline, "set_playback_time"):
                timeline.set_playback_time(0.0)
            elif hasattr(timeline, "set_current_time"):
                timeline.set_current_time(0.0)
        graph = getattr(self, "graph_editor_widget", None)
        if graph is not None and hasattr(graph, "set_current_time"):
            graph.set_current_time(0.0)
        stop_btn = cast(QPushButton, getattr(self, "stop_btn", None))
        if stop_btn is not None:
            stop_btn.setChecked(True)
            QTimer.singleShot(140, lambda: stop_btn.setChecked(False))
        self._refresh_transport_button_states()
        self.statusBar().showMessage("Playback stopped and reset to 00:00.000")
    @Slot(object)
    def on_single_note_modified(self, note):
        """
        ノートが1つ変更された瞬間に呼ばれる（リアルタイム・プレビュー）。
        軽量なDSPエンジンだからこそ、Core i3でも遅延なく鳴らせます。
        """
        if not self.perf_action.isChecked():
            # 省電力モード（Core i3モード）の時は、負荷を考えてプレビューを
            # 簡略化するか、タイマー待機にする
            self.render_timer.start(100) 
            return

        # 1. 変更されたノートだけの「部分合成」をリクエスト
        # 全体を計算し直さないのが「軽量」の極意
        threading.Thread(
            target=self.vo_se_engine.preview_single_note,
            args=(note,),
            daemon=True
        ).start()

    def setup_realtime_monitoring(self):
        """
        マウスの動きを監視し、『今まさにいじっている音』を
        ダイレクトにオーディオデバイスへ送る設定。
        """
        if hasattr(self.vo_se_engine, 'enable_realtime_monitor'):
            # C++側の低遅延モニタリングを有効化
            self.vo_se_engine.enable_realtime_monitor(True)
            self.statusBar().showMessage("Real-time Monitor: Active (Low Latency)")

    # ==========================================================================
    # GLOBAL DOMINANCE: Pro Audio Performance Engine (Full Integration)
    # ==========================================================================

    @Slot()
    def start_batch_analysis(self):
        """
        [Strategic Engine] 高速音響特性解析の開始。
        AIという呼称を排し、DSP(信号処理)による『Pro Audio Performance』として実行。
        海外勢を凌駕する解析速度と精度を実現します。
        """
        # 1. ターゲットディレクトリの取得とバリデーション
        target_dir = self.voice_manager.get_current_voice_path()
        
        if not target_dir or not os.path.exists(target_dir):
            QMessageBox.warning(self, "Performance Error", "有効な音源ライブラリがロードされていません。")
            return

        # 2. スレッド競合の防止（爆弾3・4対策）
        if hasattr(self, 'analysis_thread') and self.analysis_thread.isRunning():
            QMessageBox.warning(self, "System Busy", "現在、別の解析プロセスが実行中です。")
            return

        # 3. 解析スレッドの初期化
        # ※AnalysisThreadは別途定義されているQThreadクラス
        self.analysis_thread = AnalysisThread(self.voice_manager, target_dir)
        
        # 4. シグナルとスロットの完全接続（省略なし）
        self.analysis_thread.progress.connect(self.update_analysis_status)
        self.analysis_thread.finished.connect(self.on_analysis_complete)
        self.analysis_thread.error.connect(self.on_analysis_error)
        
        # [爆弾5対策] 完了後のメモリ解放を予約
        self.analysis_thread.finished.connect(self.analysis_thread.deleteLater)
        
        # 5. UIの戦闘態勢への切り替え
        ai_btn = getattr(self, 'ai_analyze_button', None)
        if ai_btn:
            ai_btn.setEnabled(False)
        prog = getattr(self, 'progress_bar', None)
        if prog:
            prog.show()
            prog.setValue(0)
        
        self.statusBar().showMessage("Pro Audio Dynamics Engine: Initializing high-speed analysis...")
        
        # 6. 解析実行
        self.analysis_thread.start()

    def update_analysis_status(self, percent: int, filename: str):
        """解析進捗のリアルタイム表示（UXの質で海外勢に差をつける）"""
        self.progress_bar.setValue(percent)
        self.statusBar().showMessage(f"Acoustic Sampling [{percent}%]: {filename}")

    @Slot(dict)
    def on_analysis_complete(self, results: dict) -> None:
        """
        解析完了後の統合・最適化処理。
        抽出されたパラメータをプロジェクトに反映し、世界標準の精度へ昇華させます。
        """
        from PySide6.QtWidgets import QMessageBox, QStatusBar, QProgressBar, QPushButton

        # 1. 安全なUI操作（Noneチェックを追加）
        if hasattr(self, 'progress_bar') and isinstance(self.progress_bar, QProgressBar):
            self.progress_bar.hide()
        
        if hasattr(self, 'ai_analyze_button') and isinstance(self.ai_analyze_button, QPushButton):
            self.ai_analyze_button.setEnabled(True)
        
        # ステータスバーの取得
        status_bar = self.statusBar()
        
        if not results:
            if isinstance(status_bar, QStatusBar):
                status_bar.showMessage("Analysis completed, but no data was returned.")
            return

        # 2. 解析結果の精密適用（爆弾2対策済・省略なし）
        update_count = 0
        
        # timeline_widget の存在確認
        t_widget = getattr(self, 'timeline_widget', None)
        if t_widget is not None:
            # notes_list の存在確認
            notes_list = getattr(t_widget, 'notes_list', [])
            for note in notes_list:
                # note.lyrics が results に存在するかチェック
                lyric = getattr(note, 'lyrics', None)
                if lyric in results:
                    res = results[lyric]
                    # 配列の長さをチェックし、インデックスエラーを回避
                    if isinstance(res, (list, tuple)) and len(res) >= 3:
                        # 内部データへの反映（safe_to_f の存在も前提）
                        safe_f = getattr(self, 'safe_to_f', float)
                        try:
                            note.onset = safe_f(res[0])
                            note.overlap = safe_f(res[1])
                            note.pre_utterance = safe_f(res[2])
                            note.has_analysis = True
                            update_count += 1
                        except (ValueError, TypeError, AttributeError):
                            continue
        
        # 3. UI更新（ピアノロールの再描画など）
        if t_widget is not None:
            t_widget.update()
            
        if isinstance(status_bar, QStatusBar):
            status_bar.showMessage(f"Optimization Complete: {update_count} samples updated.", 5000)
        
        # 4. グローバルシェア奪還のための自動保存ダイアログ
        # ログ 3134行目対策：QMessageBox.StandardButton.No を StandardButton.No に修正
        reply = QMessageBox.question(
            self, 
            "Acoustic Config Save", 
            "解析結果を oto.ini に反映し、音源ライブラリを最適化しますか？\n(既存ファイルは自動でバックアップされます)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
            
        if reply == QMessageBox.StandardButton.Yes:
            # メソッドの存在を確認してから実行
            export_func = getattr(self, 'export_analysis_to_oto_ini', None)
            if callable(export_func):
                export_func()


    def on_analysis_error(self, message: str):
        """解析失敗時の例外ハンドリング"""
        ai_btn = getattr(self, 'ai_analyze_button', None)
        if ai_btn:
            ai_btn.setEnabled(True)
        prog = getattr(self, 'progress_bar', None)
        if prog:
            prog.hide()
        QMessageBox.critical(self, "Engine Fault", f"解析中にエラーが発生しました:\n{message}")
    
    def safe_to_f(self, val):
        """[爆弾2対策] あらゆる入力値を安全に数値化する変換機"""
        try:
            s_val = str(val).strip()
            return float(s_val) if s_val else 0.0
        except (ValueError, TypeError):
            return 0.0

    # ==========================================================================
    # レンダリング
    # ==========================================================================

    @Slot()
    def on_render_button_clicked(self):
        """通常のRENDER操作は進捗・キャンセル対応の非同期経路へ統一する。"""
        return self.request_render()

    @Slot()
    def on_ai_button_clicked(self):
        """AIピッチ補正ボタン"""
        f0 = self.timeline_widget.get_pitch_data()
        if not f0:
            self.statusBar().showMessage("ピッチデータがありません")
            return
        
        new_f0 = self.dynamics_ai.generate_emotional_pitch(f0)
        self.timeline_widget.set_pitch_data(new_f0)
        self.statusBar().showMessage("AIピッチ補正を適用しました")


    def start_vocal_analysis(self, audio_data):
        """AIによるボーカル解析を開始する"""
        if not audio_data:
            self.statusBar().showMessage("解析エラー: オーディオデータがありません")
            return

        self.statusBar().showMessage("AI解析中... しばらくお待ちください")
        
        # 解析処理を非同期（バックグラウンド）で実行
        try:
            self.ai_manager.analyze_async(audio_data)
        except Exception as e:
            self.statusBar().showMessage(f"解析開始失敗: {e}")
            print(f"Analysis Error: {e}")

    def on_analysis_finished(self, results):
        """AIがスキャンした全音符のデータをタイムラインに展開"""
        if not results:
            self.statusBar().showMessage("音符が見つかりませんでした")
            return

        for note_data in results:
            # 1秒 = 100ピクセルの基準で配置
            x_pos = note_data["onset"] * 100 
            
            # 代表のVO-SEエンジンに合わせてノードを生成
            self.create_new_note(
                x=x_pos, 
                lyric="あ", 
                overlap=note_data.get("overlap", 0.0),
                pre_utterance=note_data.get("pre_utterance", 0.0)
            )

        self.statusBar().showMessage(f"{len(results)} 個の音符を配置しました")
        self.update()

    def create_new_note(self, x, lyric, overlap, pre_utterance):
        """実際にノードをリストに追加し、描画を指示する関数（仮）"""
        # ここに代表のVO-SE Proのノード追加ロジックを書く
        print(f"Node at {x}px added.")

  

    # ==========================================================================
    # ファイル操作
    # (旧: import_external_project, _parse_vsqx, load_ust_file, read_file_safely,
    #      save_oto_ini, get_safe_installed_name, on_export_button_clicked,
    #      save_file_dialog_and_save_midi, load_json_project,
    #      load_midi_file_from_path, parse_ust_dict_to_note は
    #      modules/gui/mixins/project_io_mixin.py へ移動済み)
    # ==========================================================================

    def _sample_range(self, events, note, res):
        """サンプリング補助関数 (Actionsエラー修正版)"""
        # 1. note が None でないことを確認 (reportOptionalOperand対策)
        if note is None:
            return [0.5] * res
            
        # 2. サンプリングポイントの生成
        times = np.linspace(note.start_time, note.start_time + note.duration, res)
        
        # 3. events が空の場合の早期リターン
        if not events:
            return [0.5] * res
            
        # 4. graph_editor_widget の存在確認と呼び出し
        if hasattr(self, 'graph_editor_widget') and self.graph_editor_widget is not None:
            return [self.graph_editor_widget.get_value_at_time(events, t) for t in times]
        else:
            # widgetがない場合のフォールバック
            return [0.5] * res




    # ==========================================================================
    # 歌詞・ノート操作
    # ==========================================================================

    @Slot()
    def on_click_auto_lyrics(self) -> None:
        """AI自動歌詞配置 (Actions完全合格版)"""
        # 1. 冒頭で import QInputDialog 済み。型ヒントでActionsを安心させる
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        text, ok = QInputDialog.getText(self, "自動歌詞配置", "文章を入力:")
        
        # bool値と文字列の存在を厳密にチェック
        if not ok or not text:
            return

        try:
            # 2. analyzerの存在チェックをガード
            if not hasattr(self, 'analyzer') or self.analyzer is None:
                return
            
            # analyzeメソッドの戻り値を型推論させる
            trace_data = self.analyzer.analyze(text)
            parsed_notes = self.analyzer.parse_trace_to_notes(trace_data)

            # 3. インポートとNoteEvent生成
            # フォルダ構成エラーを防ぐため絶対パス的なインポートを試みる
            try:
                from modules.data.data_models import NoteEvent  # type: ignore
            except ImportError:
                # 万が一インポートできない場合のフォールバック（Actions対策）
                class NoteEvent:
                    def __init__(self, **kwargs: Any):
                        for k, v in kwargs.items(): 
                            setattr(self, k, v)

            new_notes: List[Any] = []
            for d in parsed_notes:
                # 辞書の get 戻り値の型を明示的に扱う
                note = NoteEvent(
                    lyrics=str(d.get("lyric", "")),
                    start_time=float(d.get("start", 0.0)),
                    duration=float(d.get("duration", 0.5)),
                    note_number=int(d.get("pitch", 60))
                )
                new_notes.append(note)

            # 4. タイムラインへの反映（Undo/Redo対応）
            if new_notes:
                timeline = getattr(self, "timeline_widget", None)
                if timeline is not None:
                    before = timeline._snapshot_notes()
                    timeline.set_notes(new_notes)
                    current_idx = int(getattr(self, "current_track_idx", 0))
                    tracks = getattr(self, "tracks", [])
                    if 0 <= current_idx < len(tracks):
                        tracks[current_idx].notes = list(new_notes)
                    timeline._commit_edit(before, "AI自動歌詞配置")
                    timeline.update()
                
                # statusBarの存在確認（Noneになる可能性があるため）
                status_bar = self.statusBar()
                if status_bar:
                    status_bar.showMessage(f"{len(new_notes)}個の音素を配置しました")

        except Exception as e:
            QMessageBox.critical(self, "エラー", f"歌詞解析エラー: {e}")
        
        # 5. プロ版監視機能 (pro_monitoring) への同期
        # Literal[True] などのエラーを避けるため、丁寧に属性を辿る
        pro_mon = getattr(self, 'pro_monitoring', None)
        if pro_mon is not None:
            sync_func = getattr(pro_mon, 'sync_notes', None)
            if callable(sync_func):
                # timeline_widget の notes_list 存在確認
                notes = getattr(self.timeline_widget, 'notes_list', [])
                sync_func(notes)

    def update_timeline_style(self):
        """タイムラインの見た目を Apple Pro 仕様に固定"""
        if not hasattr(self, 'timeline_widget'):
            return
        self.timeline_widget.setStyleSheet("background-color: #121212; border: none;")
        self.timeline_widget.note_color = "#FF9F0A"
        self.timeline_widget.note_border_color = "#FFD60A" 
        self.timeline_widget.text_color = "#FFFFFF"

    def apply_lyrics_to_notes(self, text: str):
        """歌詞を既存ノートに割り当て（Undo/Redo対応）"""
        timeline = getattr(self, "timeline_widget", None)
        if timeline is None:
            return

        lyrics = [char for char in text if char.strip()]
        notes = timeline.notes_list
        before = timeline._snapshot_notes()
        changed = False

        for i, note in enumerate(notes):
            if i < len(lyrics) and getattr(note, "lyrics", "") != lyrics[i]:
                note.lyrics = lyrics[i]
                note.phoneme = timeline.analyze_lyric_to_phoneme(lyrics[i])
                changed = True

        if changed:
            timeline.notes_changed_signal.emit()
            timeline._commit_edit(before, "歌詞一括適用")
            timeline.update()

    @Slot()
    def on_click_apply_lyrics_bulk(self):
        """歌詞の一括流し込み（Undo/Redo対応）"""
        text, ok = QInputDialog.getMultiLineText(self, "歌詞の一括入力", "歌詞を入力:")
        if not (ok and text):
            return

        timeline = getattr(self, "timeline_widget", None)
        if timeline is None:
            return

        lyric_list = [char for char in text if char.strip() and char not in "、。！？"]
        notes = sorted(timeline.notes_list, key=lambda n: n.start_time)
        before = timeline._snapshot_notes()
        changed = False

        for i in range(min(len(lyric_list), len(notes))):
            if getattr(notes[i], "lyrics", "") != lyric_list[i]:
                notes[i].lyrics = lyric_list[i]
                notes[i].phoneme = timeline.analyze_lyric_to_phoneme(lyric_list[i])
                changed = True

        if changed:
            timeline.notes_changed_signal.emit()
            timeline._commit_edit(before, "歌詞一括流し込み")
            timeline.update()
        
        if hasattr(self, 'pro_monitoring') and self.pro_monitoring:
            self.sync_notes = True
            
            # QColorを明示的に使用（
            self.bg_color: QColor = QColor("#FFFFFF")
            
            if hasattr(self, 'timeline_widget'):
                self.refresh_canvas() # 再描画で同期を視覚化

   
    # =========================================================================
    # スクロールバー制御
    # ==========================================================================
    @Slot(int)
    def _sync_horizontal_scrollbar_from_timeline(self, offset: int) -> None:
        """TimelineWidget内部操作（ホイール/端スクロール）を外部UIへ反映する。"""
        if self.h_scrollbar is not None:
            if offset > self.h_scrollbar.maximum():
                self.update_scrollbar_range()
            self.h_scrollbar.blockSignals(True)
            self.h_scrollbar.setValue(max(0, min(int(offset), self.h_scrollbar.maximum())))
            self.h_scrollbar.blockSignals(False)

        if self.graph_editor_widget is not None:
            if self.timeline_widget is not None and hasattr(self.graph_editor_widget, "pixels_per_beat"):
                self.graph_editor_widget.pixels_per_beat = self.timeline_widget.pixels_per_beat
            self.graph_editor_widget.set_horizontal_offset(int(offset))

    @Slot()
    def update_scrollbar_range(self):
        """水平スクロールバーの範囲更新"""
        if self.h_scrollbar is None or self.timeline_widget is None:
            return

        if not self.timeline_widget.notes_list:
            self.h_scrollbar.setRange(0, 0)
            return

        max_beats = self.timeline_widget.get_max_beat_position()

        max_x_position = (max_beats + 4) * self.timeline_widget.pixels_per_beat
        viewport_width = self.timeline_widget.width()

        max_scroll_value = max(0, int(max_x_position - viewport_width))
        self.h_scrollbar.setRange(0, max_scroll_value)
        self.h_scrollbar.setPageStep(viewport_width)

    # ==========================================================================
    # その他のスロット
    # ==========================================================================
    @Slot()
    def auto_tune_selected(self) -> None:
        """
        選択中のノートのピッチを半音階グリッドにスナップさせる（オートチューン風）
        """
        if self.timeline_widget is None:
            return
        
        selected = [n for n in self.timeline_widget.notes_list if getattr(n, 'is_selected', False)]
        if not selected:
            self.statusBar().showMessage("オートチューン: ノートが選択されていません")
            return
        
        # 元のピッチを保存（Undo用）
        before_snapshot = self.timeline_widget._snapshot_notes()
        
        for note in selected:
            # MIDIノート番号に最も近い整数に丸める（半音単位）
            original = float(getattr(note, 'note_number', 60))
            snapped = int(round(original))
            # 範囲制限
            note.note_number = max(0, min(127, snapped))
            
            # もしピッチカーブ（オートメーション）が存在すればそれも補正
            # （GraphEditorWidget の Pitch イベントを参照）
            if hasattr(self, 'graph_editor_widget'):
                pitch_events = self.graph_editor_widget.all_parameters.get("Pitch", [])
                for ev in pitch_events:
                    if note.start_time <= ev.time <= note.start_time + note.duration:
                        # セント単位で補正（MIDIノート基準にスナップ）
                        midi_val = 69 + 12 * math.log2(ev.value / 440.0)
                        snapped_midi = round(midi_val)
                        ev.value = 440.0 * (2.0 ** ((snapped_midi - 69) / 12.0))
        
        self.timeline_widget._invalidate_note_rects()
        self.timeline_widget.notes_changed_signal.emit()
        self.timeline_widget._commit_edit(before_snapshot, "オートチューン")
        self.timeline_widget.update()
        self.statusBar().showMessage(f"オートチューン適用: {len(selected)} ノート")

    @Slot(object, object, str)
    def on_graph_edit_committed(self, before: dict, after: dict, description: str):
        """グラフエディタの編集を Undo/Redo 履歴に登録"""
        def redo():
           self.graph_editor_widget._restore_parameters_snapshot(after)
        def undo():
            self.graph_editor_widget._restore_parameters_snapshot(before)
        self.history.push(EditCommand(redo, undo, description))

    @Slot(str, dict)
    def on_effect_parameters_changed(self, effect_id: str, params: dict):
        """エフェクトパラメータ変更時の処理（エンジンに反映）"""
        # 例: self.vo_se_engine.update_effect(effect_id, params)
        self.statusBar().showMessage(f"エフェクト更新: {effect_id}", 2000)
    
    @Slot()
    def update_tempo_from_input(self):
        """
        テンポ入力をシステム全体（Timeline, GraphEditor, Engine）に反映する。
        Ruff F811を解消した統合版（省略なし）。

        [FIX] 以前は int(new_tempo) で小数点以下を切り捨てていたため、
        170.5 のような小数テンポを UST から読み込んだ直後に
        ユーザーがテンポ欄を編集すると 170 に丸められてしまっていた。
        UST 側は float 精度で扱っているので、UI 側も float で統一する。
        """
        try:
            # 1. 必須ウィジェットの存在チェック
            if self.tempo_input is None:
                return

            # 安全な型変換
            try:
                new_tempo = float(self.tempo_input.text())
            except ValueError:
                raise ValueError("数値形式が正しくありません")

            # 2. テンポの範囲バリデーション（30-300 BPM）
            if not (30.0 <= new_tempo <= 300.0):
                raise ValueError("テンポは30.0〜300.0の範囲で入力してください")

            # 同じ値の再入力は編集履歴を増やさない。
            old_tempo = None
            if hasattr(self, "timeline_widget") and self.timeline_widget is not None:
                old_tempo = float(self.timeline_widget.tempo)
            elif hasattr(self, "timeline") and self.timeline is not None:
                old_tempo = float(self.timeline.tempo)

            if old_tempo is not None and old_tempo == float(new_tempo):
                return

            # 3. 各コンポーネントへの伝播
            # [FIX] int() → float() に変更。小数テンポの精度を保持する。
            # TimelineWidgetへの反映
            if hasattr(self, 'timeline_widget') and self.timeline_widget is not None:
                self.timeline_widget.tempo = float(new_tempo)
                self.timeline_widget.update()  # 再描画強制
            elif hasattr(self, 'timeline') and self.timeline is not None:
                # 変数名の揺れ対策
                self.timeline.tempo = float(new_tempo)
                self.timeline.update()

            # グラフエディタへの反映
            if hasattr(self, 'graph_editor_widget') and self.graph_editor_widget is not None:
                self.graph_editor_widget.tempo = float(new_tempo)
                self.graph_editor_widget.update()

            # C++エンジンへの即時通知
            if self.vo_se_engine is not None:
                # エンジン側は精度のために float で渡す
                self.vo_se_engine.set_tempo(new_tempo)

            # 4. UIの整合性維持
            self.update_scrollbar_range()

            # ステータス表示
            if self.status_label is not None:
                self.status_label.setText(f"テンポ: {new_tempo:.1f} BPM")
            elif self.statusBar():
                self.statusBar().showMessage(f"Tempo changed to: {new_tempo:.1f}", 2000)

            # テンポ変更はプロジェクト編集なのでUndo/Redo対象にする。
            if old_tempo is not None:
                def apply_tempo(value: float) -> None:
                    if hasattr(self, "timeline_widget") and self.timeline_widget is not None:
                        self.timeline_widget.tempo = float(value)
                        self.timeline_widget.update()
                    elif hasattr(self, "timeline") and self.timeline is not None:
                        self.timeline.tempo = float(value)
                        self.timeline.update()
                    if hasattr(self, "graph_editor_widget") and self.graph_editor_widget is not None:
                        self.graph_editor_widget.tempo = float(value)
                        self.graph_editor_widget.update()
                    if getattr(self, "vo_se_engine", None) is not None:
                        self.vo_se_engine.set_tempo(float(value))
                    self.update_scrollbar_range()
                    if getattr(self, "tempo_input", None) is not None:
                        self.tempo_input.blockSignals(True)
                        self.tempo_input.setText(str(float(value)))
                        self.tempo_input.blockSignals(False)

                self.history.push(EditCommand(
                    lambda: apply_tempo(float(new_tempo)),
                    lambda: apply_tempo(float(old_tempo)),
                    "テンポ変更",
                ))

            print(f"DEBUG: System tempo synchronized to {new_tempo} BPM")

        except ValueError as e:
            # エラー時は警告を出し、値を元に戻す
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "テンポ設定エラー", str(e))

            # 直近の有効な値（timeline_widget保持分）をUIに復元
            # [FIX] 120 → 120.0 (float で統一)
            valid_tempo = 120.0
            if hasattr(self, 'timeline_widget') and self.timeline_widget:
                valid_tempo = self.timeline_widget.tempo
            elif hasattr(self, 'timeline') and self.timeline:
                valid_tempo = self.timeline.tempo

            if self.tempo_input:
                self.tempo_input.setText(str(valid_tempo))

    @Slot(str)
    def set_current_parameter_layer(self, layer_name: str):
        if not hasattr(self, 'parameters'):
            return
        if layer_name in self.parameters:
            self.current_param_layer = layer_name
            self.update()
            print(f"Parameter layer switched to: {layer_name}")
        else:
            print(f"Error: Parameter layer '{layer_name}' not found.")


    @Slot()
    def on_timeline_updated(self):
        """
        タイムライン更新時の処理（省略なし完全版）。
        ノートデータを同期し、バックグラウンドでキャッシュを先行生成する。
        """
        import threading
        
        if self.status_label:
            self.status_label.setText("エンジン同期中...")
        elif self.statusBar():
            self.statusBar().showMessage("レンダリングキャッシュ更新中...", 1000)

        # 1. タイムラインから最新のノートリストを取得
        if not hasattr(self, 'timeline_widget') or not self.timeline_widget:
            return
            
        updated_notes = self.timeline_widget.notes_list

        # TimelineWidget is the live editing surface, while each VoseTrack
        # owns the persistent copy used by project save/playback. Keep the
        # current vocal track synchronized immediately instead of waiting for
        # a later track switch, save, or playback action.
        current_idx = int(getattr(self, "current_track_idx", 0))
        tracks = list(getattr(self, "tracks", []) or [])
        if 0 <= current_idx < len(tracks):
            current_track = tracks[current_idx]
            if getattr(current_track, "track_type", "") == "vocal":
                current_track.notes = deepcopy(updated_notes)
                # Any note edit invalidates the rendered playback cache. This
                # prevents direct mixer playback from reusing an older render.
                current_track.playback_path = ""
                current_track._playback_signature = ""

        self.notes = updated_notes # MainWindow側のリストも同期

        # 2. Cエンジンへの先行キャッシュ指示
        # ※UIスレッドをブロックしないよう、重い処理（波形生成の準備）は別スレッドで実行
        if hasattr(self, 'vo_se_engine') and self.vo_se_engine:
            try:
                # 既存のスレッドと衝突しないよう、デーモンスレッドとして開始
                cache_thread = threading.Thread(
                    target=self._run_engine_cache,
                    args=(updated_notes,),
                    daemon=True
                )
                cache_thread.start()
            except Exception as e:
                print(f"❌ Cache thread failed to start: {e}")

    def _run_engine_cache(self, notes):
        """エンジン側のキャッシュ生成を安全に実行するサブメソッド"""
        try:
            # エンジン側でNoteEvent構造体への変換と、先行波形計算（UTAUならresampler呼び出し等）を行う
            if self.vo_se_engine and hasattr(self.vo_se_engine, "prepare_cache"):
                self.vo_se_engine.prepare_cache(notes)
                print(f"DEBUG: Cache prepared for {len(notes)} notes.")
            elif self.vo_se_engine:
                print("⚠️ Engine does not support prepare_cache; skipping cache warm-up.")
        except Exception as e:
            print(f"❌ Engine Cache Error: {e}")

    @Slot()
    def on_notes_modified(self):
        """変更検知（連打防止タイマー）"""
        if not hasattr(self, 'render_timer'):
            return
        self.render_timer.stop()
        self.render_timer.start(300)
        self.statusBar().showMessage("変更を検知しました...", 500)

    def execute_async_render(self):
        """非同期レンダリング実行"""
        import threading
        self.statusBar().showMessage("音声をレンダリング中...", 1000)
        
        updated_notes = self.timeline_widget.notes_list
        if not updated_notes:
            return

        if hasattr(self, 'vo_se_engine') and self.vo_se_engine:
            if hasattr(self.vo_se_engine, 'update_notes_data'):
                self.vo_se_engine.update_notes_data(updated_notes)

            def rendering_task():
                try:
                    if hasattr(self.vo_se_engine, 'prepare_cache'):
                        self.vo_se_engine.prepare_cache(updated_notes)
                    
                    if hasattr(self.vo_se_engine, 'synthesize_track'):
                        pitch = getattr(self, 'pitch_data', [])
                        self.vo_se_engine.synthesize_track(
                            updated_notes, 
                            pitch, 
                            preview_mode=True
                        )
                except Exception as e:
                    print(f"Async Render Error: {e}")

            render_thread = threading.Thread(target=rendering_task, daemon=True)
            render_thread.start()

    @Slot(dict)
    def on_graph_parameters_changed(self, all_parameters: dict):
        # GraphEditorWidget は {"Pitch": [...], "Gender": [...], ...} を送る
        self.pitch_data = all_parameters.get("Pitch", [])
        if self.timeline_widget is not None:
            self.timeline_widget.update()

    @Slot(list)
    def on_pitch_data_updated(self, new_pitch_events: list):
        self.pitch_data = new_pitch_events

    @Slot()
    def on_midi_port_changed(self):
        if self.midi_port_selector is None:
           return

        selected_port = self.midi_port_selector.currentData()

        if self.midi_manager is not None:
           self.midi_manager.stop()
           self.midi_manager = None

        if selected_port and selected_port != "ポートなし":
            try:
               from .midi_io import MidiInputManager  # type: ignore
               self.midi_manager = MidiInputManager(selected_port)
               self.midi_manager.start()

               if self.status_label is not None:
                   self.status_label.setText(f"MIDI: {selected_port}")

            except ImportError:
                pass

    @Slot(int, int, str)
    def update_gui_with_midi(self, note_number: int, velocity: int, event_type: str):
        if self.status_label is None:
            return

        if event_type == 'on':
            self.status_label.setText(f"ノートオン: {note_number} (Velocity: {velocity})")
        elif event_type == 'off':
            self.status_label.setText(f"ノートオフ: {note_number}")

    def handle_midi_realtime(self, note_number: int, velocity: int, event_type: str):
        if not hasattr(self, 'vo_se_engine') or not self.vo_se_engine:
            return
        if event_type == 'on':
            if hasattr(self.vo_se_engine, 'play_realtime_note'):
                self.vo_se_engine.play_realtime_note(note_number)
            if getattr(self, 'is_recording', False):
                self.timeline_widget.add_note_from_midi(note_number, velocity)
        elif event_type == 'off':
            if hasattr(self.vo_se_engine, 'stop_realtime_note'):
                self.vo_se_engine.stop_realtime_note(note_number)

    @Slot()
    def update_scrollbar_v_range(self):
        if self.timeline_widget is None:
            return

        key_h = self.timeline_widget.key_height_pixels
        full_height = 128 * key_h
        viewport_height = self.timeline_widget.height()

        n_height = getattr(self.timeline_widget, 'note_height', key_h)
        max_v = 128 * n_height

        if self.vertical_scroll is not None:
            self.vertical_scroll.setRange(0, int(max_v))

        if self.v_scrollbar is not None:
            max_scroll_value = max(0, int(full_height - viewport_height + key_h))
            self.v_scrollbar.setRange(0, max_scroll_value)

        if self.keyboard_sidebar is not None:
            self.keyboard_sidebar.set_key_height_pixels(key_h)

    # ==========================================================================
    # ヘルパーメソッド
    # ==========================================================================

    def _get_yomi_from_lyrics(self, lyrics: str) -> str:
        """
        歌詞（漢字・かな混じり）を平仮名に変換する（省略なし完全版）
        pykakasiが未インストールの場合でも、歌詞を壊さず返す安全設計。
        """
        if not lyrics:
            return ""

        try:
            # メソッド内インポートにより、ライブラリがない環境でも起動を妨げない
            import pykakasi
            
            # インスタンス生成（最新のpykakasi仕様に準拠）
            kks = pykakasi.kakasi()
            result = kks.convert(lyrics)
            
            # 各形態素の 'hira' (ひらがな) 属性を結合
            yomi = "".join([str(item.get('hira', '')) for item in result])
            return yomi
            
        except (ImportError, ModuleNotFoundError):
            # pykakasiがインストールされていない場合のフォールバック
            print("DEBUG: pykakasi not found. Returning raw lyrics.")
            return lyrics
        except Exception as e:
            # その他の予期せぬエラー（辞書破損など）への対応
            print(f"DEBUG: Yomi conversion error: {e}")
            return lyrics

    def midi_to_hz(self, midi_note: int) -> float:
        """
        MIDIノート番号を周波数(Hz)に変換する（数学的完全版）
        計算式: $f = 440 \times 2^{\frac{n-69}{12}}$
        """
        # MIDI番号が None や不正な値の場合のガード
        if midi_note is None:
            return 0.0
            
        # 浮動小数点数として計算し、型安全性を確保
        # 69は A4 (440Hz) のMIDI番号
        return float(440.0 * (2.0 ** ((float(midi_note) - 69.0) / 12.0)))

    # ==========================================================================
    # イベントハンドラ
    # ==========================================================================

    def keyPressEvent(self, event) -> None:
        """
        キーボードショートカット制御。
        ActionsのEnumアクセスエラーを完全に回避しつつ、DAWとしての操作性を完遂します。
        """
        from PySide6.QtCore import Qt

        key = event.key()
        mod = event.modifiers()
        
        # 1. スペースキー：再生/一時停止
        # Actions対策: Qt.Key.Key_Space ではなく Qt.Key.Key_Space (PySide6標準) を使用
        if key == Qt.Key.Key_Space:
            play_func = getattr(self, 'on_play_pause_toggled', None)
            if callable(play_func):
                play_func()
            event.accept()
            return

        # 2. Ctrl + R：録音開始/停止
        # Actions対策: KeyboardModifier.ControlModifier を安全に比較
        elif key == Qt.Key.Key_R and (mod & Qt.KeyboardModifier.ControlModifier):
            record_func = getattr(self, 'on_record_toggled', None)
            if callable(record_func):
                record_func()
            event.accept()
            return

        # 3. Ctrl + L：ループ切り替え
        elif key == Qt.Key.Key_L and (mod & Qt.KeyboardModifier.ControlModifier):
            loop_func = getattr(self, 'on_loop_button_toggled', None)
            if callable(loop_func):
                loop_func()
            event.accept()
            return

        # 4. Delete / Backspace：選択項目の削除
        elif key in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            # タイムラインウィジェットの存在を安全に確認
            t_widget = getattr(self, 'timeline_widget', None)
            if t_widget is not None:
                delete_func = getattr(t_widget, 'delete_selected_notes', None)
                if callable(delete_func):
                    delete_func()
            event.accept()
            return

        # 5. その他：親クラスのイベントに渡す
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event):
        reply = QMessageBox.question(
            self,
            "確認",
            "作業内容が失われる可能性があります。終了してもよろしいですか？",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )

        if reply == QMessageBox.StandardButton.Save:
            save_func = getattr(self, "on_save_project_clicked", None)
            if callable(save_func) and not bool(save_func()):
                event.ignore()
                return
        elif reply != QMessageBox.StandardButton.Discard:
            event.ignore()
            return

        # Stop accepting new transport work and ask any active render to cancel.
        playback_timer = getattr(self, "playback_timer", None)
        if playback_timer is not None:
            playback_timer.stop()

        worker = getattr(self, "current_worker", None)
        if worker is not None:
            cancel = getattr(worker, "cancel", None)
            if callable(cancel):
                cancel()
            pool = QThreadPool.globalInstance()
            if not pool.waitForDone(15000):
                QMessageBox.warning(
                    self,
                    "レンダリング終了待ち",
                    "レンダリング処理がまだ実行中です。処理の安全な終了後に、もう一度終了してください。",
                )
                event.ignore()
                return
            self.current_worker = None
            temporary_path = str(getattr(self, "_render_temp_path", "") or "")
            if temporary_path:
                try:
                    if os.path.exists(temporary_path):
                        os.remove(temporary_path)
                except OSError as exc:
                    print(f"[Render] Shutdown cleanup warning: {exc}")

        # Playback preparation uses a QThread, so it must leave native code
        # before the engine and the parent window are released.
        playback_worker = getattr(self, "_playback_prepare_thread", None)
        if playback_worker is not None and playback_worker.isRunning():
            request_cancel = getattr(playback_worker, "request_cancel", None)
            if callable(request_cancel):
                request_cancel()
            if not playback_worker.wait(15000):
                QMessageBox.warning(
                    self,
                    "再生準備終了待ち",
                    "再生準備処理がまだ実行中です。安全な終了後に、もう一度終了してください。",
                )
                event.ignore()
                return
        self._playback_prepare_thread = None
        self._pending_playback_tracks = None

        # Stop the legacy playback thread, if an older entry point started it.
        self.is_playing = False
        legacy_playback_thread = getattr(self, "playback_thread", None)
        if legacy_playback_thread is not None and legacy_playback_thread.is_alive():
            if legacy_playback_thread is not threading.current_thread():
                legacy_playback_thread.join(timeout=2.0)
            if legacy_playback_thread.is_alive():
                QMessageBox.warning(
                    self,
                    "再生終了待ち",
                    "再生スレッドが停止していません。再生停止後に、もう一度終了してください。",
                )
                event.ignore()
                return
        self.playback_thread = None

        mixer = getattr(self, "audio_mixer", None)
        stop_mixer = getattr(mixer, "stop", None)
        if callable(stop_mixer):
            try:
                stop_mixer()
            except Exception as exc:
                print(f"Audio mixer shutdown warning: {exc}")

        config = {
            "default_voice": getattr(self, "current_voice", None),
            "volume": getattr(self, "volume", 1.0),
        }
        # main.py holds a reference to this same dictionary and writes it once
        # more after app.exec(); update it before returning from closeEvent.
        current_config = getattr(self, "config", None)
        if isinstance(current_config, dict):
            current_config.update(config)
        else:
            self.config = dict(config)
        config_manager = getattr(self, "config_manager", None)
        save_config = getattr(config_manager, "save_config", None)
        if callable(save_config):
            try:
                save_config(config)
            except Exception as exc:
                print(f"Config save warning: {exc}")

        # The MIDI setup patch stores the live input manager under this name.
        for manager_name in ("_midi_input_manager", "midi_manager"):
            midi_manager = getattr(self, manager_name, None)
            stop_midi = getattr(midi_manager, "stop", None)
            if callable(stop_midi):
                try:
                    stop_midi()
                except Exception as exc:
                    print(f"MIDI shutdown warning: {exc}")

        engine = getattr(self, "vo_se_engine", None)
        stop_engine = getattr(engine, "stop", None)
        if callable(stop_engine):
            try:
                stop_engine()
            except Exception as exc:
                print(f"Voice engine shutdown warning: {exc}")
        close_engine = getattr(engine, "close", None)
        if callable(close_engine):
            try:
                close_engine()
            except Exception as exc:
                print(f"Voice engine close warning: {exc}")

        audio_output = getattr(self, "audio_output", None)
        stop_audio_output = getattr(audio_output, "stop", None)
        if callable(stop_audio_output):
            try:
                stop_audio_output()
            except Exception as exc:
                print(f"Audio output shutdown warning: {exc}")

        event.accept()
        print("Application closing...")

    # ==============================================================================
    # レリタリング実行メゾット
    # ==============================================================================

    def _set_render_controls_enabled(self, rendering: bool) -> None:
        """Freeze mutable render inputs for the duration of an export."""
        control_names = (
            "timeline_widget",
            "graph_editor_widget",
            "voice_gallery",
            "character_selector",
            "track_list_widget",
            "tempo_input",
            "play_btn",
            "play_button",
            "record_button",
            "loop_btn",
            "undo_action",
            "redo_action",
        )
        if rendering:
            states = {}
            for name in control_names:
                widget = getattr(self, name, None)
                if widget is not None and callable(getattr(widget, "setEnabled", None)):
                    is_enabled = getattr(widget, "isEnabled", None)
                    states[name] = bool(is_enabled()) if callable(is_enabled) else True
                    widget.setEnabled(False)
            self._render_control_enabled_states = states
        else:
            states = getattr(self, "_render_control_enabled_states", {}) or {}
            for name, was_enabled in states.items():
                widget = getattr(self, name, None)
                if widget is not None and callable(getattr(widget, "setEnabled", None)):
                    widget.setEnabled(bool(was_enabled))
            self._render_control_enabled_states = {}

    def _start_async_render(
        self,
        notes: list,
        parameters: dict,
        output_path: str,
        *,
        play_after_success: bool,
        is_pro: Optional[bool] = None,
    ) -> bool:
        """Render to a temporary sibling WAV and publish only after success."""
        if getattr(self, "current_worker", None) is not None:
            self.statusBar().showMessage("別のレンダリングが実行中です。", 4000)
            return False
        if bool(getattr(self, "_playback_preparing", False)):
            self.statusBar().showMessage("再生用の準備中は書き出しを開始できません。", 4000)
            return False

        playback_worker = getattr(self, "_playback_prepare_thread", None)
        if playback_worker is not None and playback_worker.isRunning():
            self.statusBar().showMessage("再生用レンダリングの完了後に書き出してください。", 4000)
            return False

        engine = getattr(self, "vo_se_engine", None)
        if engine is None:
            self.statusBar().showMessage("エンジンがロードされていません。", 5000)
            return False

        final_path = os.path.abspath(os.fspath(output_path))
        output_dir = os.path.dirname(final_path) or os.getcwd()
        temporary_path = ""
        timeline = getattr(self, "timeline_widget", None)
        try:
            render_tempo = float(getattr(timeline, "tempo", 120.0))
            if not math.isfinite(render_tempo) or render_tempo <= 0.0:
                raise ValueError("テンポが不正なためレンダリングを開始できません。")
            os.makedirs(output_dir, exist_ok=True)
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{os.path.splitext(os.path.basename(final_path))[0]}.",
                suffix=".tmp.wav",
                dir=output_dir,
            )
            os.close(descriptor)
            os.remove(temporary_path)

            if is_pro is None:
                from modules.data.licensing import LicenseManager
                is_pro = LicenseManager.is_pro()

            self._render_target_path = final_path
            self._render_temp_path = temporary_path
            self._render_play_after_success = bool(play_after_success)
            self._render_cancelled = False
            self._set_render_controls_enabled(True)

            if hasattr(self, "progress_bar") and self.progress_bar is not None:
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(0)
                self.progress_bar.setVisible(True)
            if getattr(self, "render_eta_label", None) is not None:
                self.render_eta_label.setText("ETA: 計算中...")
            if getattr(self, "render_btn", None) is not None:
                self.render_btn.setEnabled(False)
            if getattr(self, "cancel_render_btn", None) is not None:
                self.cancel_render_btn.setVisible(True)
                self.cancel_render_btn.setEnabled(True)

            self.statusBar().showMessage("レンダリング準備中...")
            worker = SynthesisWorker(
                engine,
                deepcopy(list(notes)),
                temporary_path,
                is_pro=bool(is_pro),
                parameters=deepcopy(parameters),
                tempo_bpm=render_tempo,
            )
            self.current_worker = worker
            worker.signals.finished.connect(self.on_render_success)
            worker.signals.error.connect(self.on_render_failed)
            worker.signals.progress.connect(self.on_render_progress)
            QThreadPool.globalInstance().start(worker)
            return True
        except Exception as exc:
            self.current_worker = None
            self._render_target_path = ""
            self._render_temp_path = ""
            self._render_play_after_success = False
            self._set_render_controls_enabled(False)
            try:
                if temporary_path and os.path.exists(temporary_path):
                    os.remove(temporary_path)
            except OSError:
                pass
            self.on_render_failed(str(exc))
            return False

    @Slot()
    def request_render(self) -> None:
        """RENDERボタン用の非同期プレビュー書き出し。"""
        timeline = getattr(self, "timeline_widget", None)
        graph = getattr(self, "graph_editor_widget", None)
        if timeline is None or graph is None:
            QMessageBox.warning(
                self,
                "エラー",
                "書き出しに必要な画面が初期化されていません。",
            )
            return

        try:
            raw_notes = list(timeline.get_all_notes_data() or [])
        except Exception as exc:
            self.statusBar().showMessage(f"ノート取得エラー: {exc}", 5000)
            return
        if not raw_notes:
            self.statusBar().showMessage("エラー: レンダリングするノートがありません。")
            return

        all_params = getattr(graph, "all_parameters", {}) or {}
        parameters = {
            "Pitch": list(all_params.get("Pitch", [])),
            "Gender": list(all_params.get("Gender", [])),
            "Tension": list(all_params.get("Tension", [])),
            "Breath": list(all_params.get("Breath", [])),
        }
        output_wav = os.path.join(
            tempfile.gettempdir(), "vose_render_cache", "render_result.wav"
        )
        self._start_async_render(
            raw_notes,
            parameters,
            output_wav,
            play_after_success=True,
        )

    @Slot(int, float)
    def on_render_progress(self, percent: int, eta_sec: float):
        """レンダリング進捗の更新（プログレスバーとETAラベル）"""
        if hasattr(self, "progress_bar"):
            self.progress_bar.setValue(percent)
        if eta_sec > 0:
            mins = int(eta_sec // 60)
            secs = int(eta_sec % 60)
            self.render_eta_label.setText(f"ETA: {mins:02d}:{secs:02d}")
        else:
            self.render_eta_label.setText("ETA: 計算中...")

    def on_render_success(self, output_wav):
        """レンダリング成功時に一時WAVを正式な保存先へ公開する。"""
        produced_path = os.path.abspath(os.fsdecode(os.fspath(output_wav)))
        final_path = str(getattr(self, "_render_target_path", "") or "")
        play_after_success = bool(getattr(self, "_render_play_after_success", True))

        try:
            if final_path:
                if not os.path.isfile(produced_path):
                    raise RuntimeError("レンダリング結果の一時WAVが見つかりません。")
                # 同じ保存先ディレクトリ内の一時ファイルから置換するため、
                # 失敗時は既存の正常なWAVを残せる。
                os.replace(produced_path, final_path)
                produced_path = final_path
        except Exception as exc:
            self.on_render_failed(f"書き出しファイルの確定に失敗しました: {exc}")
            return

        self._render_target_path = ""
        self._render_temp_path = ""
        self._render_play_after_success = False
        self._set_render_controls_enabled(False)
        if hasattr(self, "progress_bar") and self.progress_bar is not None:
            self.progress_bar.setVisible(False)
        if getattr(self, "render_btn", None) is not None:
            self.render_btn.setEnabled(True)
        if getattr(self, "render_eta_label", None) is not None:
            self.render_eta_label.setText("完了")
        if getattr(self, "cancel_render_btn", None) is not None:
            self.cancel_render_btn.setVisible(False)
            self.cancel_render_btn.setEnabled(False)
        self.current_worker = None

        self.statusBar().showMessage(f"WAV書き出し完了: {produced_path}", 5000)
        if play_after_success:
            self.play_rendered_audio(produced_path)
        else:
            QMessageBox.information(
                self,
                "書き出し完了",
                f"WAVを書き出しました。\n{produced_path}",
            )

    def on_render_failed(self, error_msg):
        """レンダリング失敗・キャンセル時に一時ファイルとUI状態を整理する。"""
        temporary_path = str(getattr(self, "_render_temp_path", "") or "")
        if temporary_path:
            try:
                if os.path.exists(temporary_path):
                    os.remove(temporary_path)
            except OSError as cleanup_error:
                print(f"[Render] Temporary WAV cleanup failed: {cleanup_error}")

        self._render_target_path = ""
        self._render_temp_path = ""
        self._render_play_after_success = False
        self._set_render_controls_enabled(False)
        if hasattr(self, "progress_bar") and self.progress_bar is not None:
            self.progress_bar.setVisible(False)
        if getattr(self, "render_btn", None) is not None:
            self.render_btn.setEnabled(True)
        if getattr(self, "cancel_render_btn", None) is not None:
            self.cancel_render_btn.setVisible(False)
            self.cancel_render_btn.setEnabled(False)
        self.current_worker = None

        was_cancelled = self._render_cancelled
        self._render_cancelled = False
        if was_cancelled:
            if getattr(self, "render_eta_label", None) is not None:
                self.render_eta_label.setText("キャンセル済み")
            self.statusBar().showMessage("レンダリングをキャンセルしました。")
            return

        if getattr(self, "render_eta_label", None) is not None:
            self.render_eta_label.setText("エラー")
        self.statusBar().showMessage(f"失敗: {error_msg}")
        QMessageBox.critical(
            self,
            "Render Error",
            f"レンダリング中にエラーが発生しました:\n{error_msg}",
        )

    @Slot()
    def cancel_current_render(self):
        """現在実行中のレンダリングをキャンセルする（協調的キャンセル）"""
        if self.current_worker:
            self._render_cancelled = True
            self.current_worker.cancel()
            self.cancel_render_btn.setVisible(False)
            self.cancel_render_btn.setEnabled(False)
            self.render_eta_label.setText("キャンセル中...")
            self.statusBar().showMessage(
                "キャンセル要求を送信しました（現在のチャンク完了後に停止します）。"
            )

    def play_rendered_audio(self, wav_path: str) -> None:
        """Play a rendered WAV through the unified desktop AudioMixer."""
        if not wav_path or not os.path.exists(wav_path):
            return
        self.play_audio(wav_path)

def main() -> None:
    """
    VO-SE Pro アプリケーション起動エントリーポイント。
    """
    #from PySide6.QtWidgets import QApplication

    # 1. アプリケーションインスタンスの作成
    # sys をインポート済みなので、sys.argv へのアクセスが安全です
    app = QApplication(sys.argv)
    
    # 2. 外観の設定
    # DAWとしての統一感を出すため、Fusionスタイルを適用
    app.setStyle("Fusion")
    
    # 3. メインウィンドウの生成と表示
    # クラス MainWindow が定義済みであることを前提にインスタンス化
    try:
        # 代表、ここで MainWindow を呼び出します
        window = MainWindow()
        window.show()
        
        # 4. イベントループの開始と安全な終了
        # 戻り値を sys.exit に渡すことで、正常終了(0)を保証します
        exit_code = app.exec()
        sys.exit(exit_code)
        
    except NameError as e:
        # MainWindow が見つからない場合のデバッグ用
        print(f"CRITICAL ERROR: MainWindow class is not defined. {e}")
        sys.exit(1)
    except Exception as e:
        # その他の予期せぬ起動エラーの捕捉
        print(f"APPLICATION ERROR: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
