# modules/tools/batch_voice_optimizer.py
# ═══════════════════════════════════════════════════════════════════════
# VO-SE Pro 究極のoto.ini自動生成エンジン
#
# 搭載技術:
#   1. 高精度フォルマントトラッキング (LPC + ケプストラム + FFTピーク補正)
#   2. 音素認識 (pyopenjtalk + CNNベースの音響分類器)
#   3. 機械学習予測 (XGBoost + オンライン更新)
#   4. 信号処理による微調整 (エンベロープ加速度・RMS・ZCR複合)
#   5. コンテキスト依存パラメータ最適化 (前後音素考慮)
#   6. 自己改善機構 (ユーザーフィードバック蓄積・再学習)
# ═══════════════════════════════════════════════════════════════════════
import os
import re
import json
import hashlib
import pickle
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Tuple
import multiprocessing

import numpy as np
import soundfile as sf
from scipy import signal
from scipy.fft import rfft, rfftfreq
from scipy.signal import find_peaks, lfilter
from scipy.linalg import solve_toeplitz
try:
    from sklearn.preprocessing import StandardScaler  # type: ignore[import-not-found]
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False

    class StandardScaler:  # type: ignore[no-redef]
        """
        【堅牢化】scikit-learn非搭載環境向けの最小限フォールバック実装。
        平均・標準偏差ベースの正規化のみ提供し、外部依存を持たない。
        """
        def __init__(self):
            self.mean_: Optional[np.ndarray] = None
            self.scale_: Optional[np.ndarray] = None

        def fit(self, X: np.ndarray):
            X = np.asarray(X, dtype=np.float64)
            self.mean_ = np.mean(X, axis=0)
            std = np.std(X, axis=0)
            self.scale_ = np.where(std < 1e-8, 1.0, std)
            return self

        def transform(self, X: np.ndarray) -> np.ndarray:
            X = np.asarray(X, dtype=np.float64)
            if self.mean_ is None or self.scale_ is None:
                return X
            return (X - self.mean_) / self.scale_

        def fit_transform(self, X: np.ndarray) -> np.ndarray:
            return self.fit(X).transform(X)

import logging
logger = logging.getLogger("VO-SE-vocal")

# ─── オプション依存関係 ────────────────────────────────────────────────
try:
    import pyopenjtalk
    PYOPENJTALK_AVAILABLE = True
except ImportError:
    PYOPENJTALK_AVAILABLE = False

try:
    import xgboost as xgb  # type: ignore[import-not-found]
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False

try:
    import torch  # type: ignore[import-not-found]
    import torch.nn as nn  # type: ignore[import-not-found]
    import torch.nn.functional as F  # type: ignore[import-not-found]
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False


# ═══════════════════════════════════════════════════════════════════════
# 1. データ構造
# ═══════════════════════════════════════════════════════════════════════

@dataclass
class OtoParams:
    """oto.ini パラメータ（ms単位）"""
    offset: float = 0.0
    preutter: float = 50.0
    overlap: float = 20.0
    constant: float = 70.0
    blank: float = -10.0
    confidence: float = 0.0
    phoneme: str = ""
    consonant_type: str = ""
    vowel: str = ""


@dataclass
class AcousticFeatures:
    """WAVから抽出する全音響特徴量（48次元）"""
    # 時間領域
    onset_time: float = 0.0
    attack_time: float = 0.0
    decay_time: float = 0.0
    release_time: float = 0.0
    rms_max: float = 0.0
    rms_mean: float = 0.0
    rms_std: float = 0.0
    rms_skew: float = 0.0
    rms_kurt: float = 0.0
    zcr_mean: float = 0.0
    zcr_std: float = 0.0
    zcr_skew: float = 0.0
    # 周波数領域
    centroid_mean: float = 0.0
    centroid_std: float = 0.0
    bandwidth_mean: float = 0.0
    bandwidth_std: float = 0.0
    rolloff_mean: float = 0.0
    rolloff_std: float = 0.0
    flatness_mean: float = 0.0
    flatness_std: float = 0.0
    flux_mean: float = 0.0
    flux_std: float = 0.0
    # フォルマント (F1-F4)
    f1_mean: float = 0.0
    f1_std: float = 0.0
    f2_mean: float = 0.0
    f2_std: float = 0.0
    f3_mean: float = 0.0
    f3_std: float = 0.0
    f4_mean: float = 0.0
    f4_std: float = 0.0
    f1_f2_ratio: float = 0.0
    f1_f3_ratio: float = 0.0
    # スペクトル形状
    spectral_slope_mean: float = 0.0
    spectral_slope_std: float = 0.0
    spectral_crest_mean: float = 0.0
    spectral_crest_std: float = 0.0
    # MFCC (簡易: 13次元)
    mfcc: List[float] = field(default_factory=lambda: [0.0] * 13)
    # 音素情報
    phoneme: str = ""
    consonant_type: str = ""
    vowel: str = ""
    context_prev: str = ""
    context_next: str = ""


# ═══════════════════════════════════════════════════════════════════════
# 2. 高精度フォルマントトラッカー
# ═══════════════════════════════════════════════════════════════════════

class FormantTracker:
    """LPC + ケプストラム + FFTピーク補正のハイブリッドフォルマント抽出"""

    def __init__(self, sr: int = 16000, n_formants: int = 4):
        self.sr = sr
        self.n_formants = n_formants
        self.formant_ranges = [
            (200, 1000),    # F1
            (500, 2500),    # F2
            (1500, 4000),   # F3
            (2500, 5500),   # F4
        ]
        # ケプストラムリフター
        self.quefrency_lifter = 0.6

    @staticmethod
    def _lpc_coeffs(x: np.ndarray, order: int = 14) -> np.ndarray:
        """自己相関法によるLPC係数計算（数値安定版）"""
        if len(x) < order:
            return np.zeros(order + 1)
        # プリエンファシス
        x = np.asarray(lfilter([1.0, -0.97], 1.0, x))
        # 自己相関
        r = np.correlate(x, x, mode='full')[len(x)-1:len(x)+order]
        if r[0] == 0:
            return np.zeros(order + 1)
        # Toeplitz方程式を解く（solve_toeplitzは行列生成と解を一度に行う）
        try:
            a = solve_toeplitz(r[:-1], -r[1:])
        except Exception:
            return np.zeros(order + 1)
        return np.concatenate(([1.0], a))

    def _formants_from_lpc(self, a: np.ndarray) -> np.ndarray:
        """LPC係数からフォルマント周波数を抽出"""
        if np.all(a == 0):
            return np.zeros(self.n_formants)
        roots = np.roots(a)
        # 単位円上の極のみを抽出
        roots = roots[np.abs(np.abs(roots) - 1.0) < 0.1]
        roots = roots[roots.imag > 0]
        if len(roots) == 0:
            return np.zeros(self.n_formants)
        angles = np.angle(roots)
        freqs = angles * self.sr / (2 * np.pi)
        # 範囲内のものを選択
        selected = []
        for f_min, f_max in self.formant_ranges:
            candidates = freqs[(freqs >= f_min) & (freqs <= f_max)]
            if len(candidates) > 0:
                selected.append(candidates[np.argmin(np.abs(candidates - np.median(candidates)))])
            else:
                selected.append(0.0)
        # 不足分は補完
        while len(selected) < self.n_formants:
            selected.append(0.0)
        return np.array(selected[:self.n_formants])

    def _formants_from_cepstrum(self, x: np.ndarray) -> np.ndarray:
        """ケプストラム法によるフォルマント推定（補助）

        【バグ修正】track() から渡される x は既に窓関数（Hanning）を適用済みの
        単一フレーム（1フレーム分の波形）である。従来実装は内部で
        signal.stft(nperseg=1024, ...) を使っており、
          (a) frame_len(512) < n_fft(1024) のとき scipy が nperseg を自動縮小する一方
              noverlap は縮小されず ValueError になる
          (b) 縮小を回避できたとしても STFT は複数時間フレームを返す2次元配列になり、
              1次元入力を要求する find_peaks に渡すと "x must be a 1-D array" で落ちる
        という二重のバグを抱えていた。単一フレーム入力に対して「1フレーム→1スペクトル」
        という意図に忠実な直接FFT（rfft）ベースの実装に置き換えることで両方を解消する。
        """
        n = len(x)
        if n < 2:
            return np.zeros(self.n_formants)
        spec = np.abs(np.asarray(rfft(x)))
        log_spec = np.log(spec + 1e-10)
        ceps = np.fft.irfft(log_spec, n=n)
        # リフタリング
        lifter = np.ones_like(ceps)
        lifter[int(len(ceps) * self.quefrency_lifter):] = 0
        smoothed = np.fft.rfft(ceps * lifter)
        envelope = np.exp(np.real(smoothed))
        # ピーク検出（envelopeは常に1次元）
        peaks, _ = find_peaks(envelope, height=np.percentile(envelope, 70), distance=3)
        freqs = peaks * self.sr / n
        selected = []
        for f_min, f_max in self.formant_ranges:
            candidates = freqs[(freqs >= f_min) & (freqs <= f_max)]
            if len(candidates) > 0:
                selected.append(candidates[np.argmax(envelope[peaks][(freqs >= f_min) & (freqs <= f_max)])])
            else:
                selected.append(0.0)
        while len(selected) < self.n_formants:
            selected.append(0.0)
        return np.array(selected[:self.n_formants])

    def track(self, x: np.ndarray, frame_len: int = 512, hop: int = 128) -> np.ndarray:
        """フレームごとにフォルマントを追跡"""
        if len(x) < frame_len:
            return np.zeros((1, self.n_formants))
        n_frames = max(1, (len(x) - frame_len) // hop + 1)
        formants = np.zeros((n_frames, self.n_formants))

        for i in range(n_frames):
            start = i * hop
            end = start + frame_len
            if end > len(x):
                break
            frame = x[start:end] * np.hanning(frame_len)
            # LPC法
            a = self._lpc_coeffs(frame, order=14)
            f_lpc = self._formants_from_lpc(a)
            # ケプストラム法（補助）
            f_ceps = self._formants_from_cepstrum(frame)
            # ハイブリッド: 信頼度の高い方を選択
            f_final = f_lpc.copy()
            for j in range(self.n_formants):
                if f_lpc[j] == 0 and f_ceps[j] > 0:
                    f_final[j] = f_ceps[j]
                elif f_lpc[j] > 0 and f_ceps[j] > 0:
                    f_final[j] = (f_lpc[j] + f_ceps[j]) / 2
            formants[i] = f_final

        # メディアンフィルタで平滑化
        for j in range(self.n_formants):
            if len(formants) > 5:
                formants[:, j] = signal.medfilt(formants[:, j], kernel_size=5)
        return formants


# ═══════════════════════════════════════════════════════════════════════
# 3. 音素認識エンジン (pyopenjtalk + CNN)
# ═══════════════════════════════════════════════════════════════════════

# 【事前学習済みCNNモデルの同梱】
# パッケージ内 (modules/tools/models/) に汎用モデルを同梱しておくことで、
# ユーザー側の再学習なしに初回から一定の精度を確保する。
# 実際の .pt ファイルはリポジトリに同梱 or 初回起動時に別途配布する想定。
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CNN_MODEL_PATH = os.path.join(_MODULE_DIR, "models", "phoneme_cnn_pretrained.pt")


def resolve_cnn_model_path(explicit_path: Optional[str] = None) -> Optional[str]:
    """
    使用するCNNモデルパスを決定する。
    優先順位: 1) 明示的に指定されたパス  2) 同梱の汎用モデル
              3) （設定されていれば）初回ダウンロード  4) なし（ヒューリスティックにフォールバック）
    """
    if explicit_path and os.path.exists(explicit_path):
        return explicit_path
    if os.path.exists(DEFAULT_CNN_MODEL_PATH):
        return DEFAULT_CNN_MODEL_PATH

    # 【改善】初回起動時の自動取得（オプション）。
    # 環境変数 VOSE_CNN_MODEL_URL が設定されている場合のみ、同梱モデルの配置先へダウンロードを試みる。
    # ネットワークが使えない/未設定の環境では何もせずヒューリスティックにフォールバックするだけなので安全。
    download_url = os.environ.get("VOSE_CNN_MODEL_URL")
    if download_url:
        try:
            import urllib.request
            os.makedirs(os.path.dirname(DEFAULT_CNN_MODEL_PATH), exist_ok=True)
            logger.info(f"同梱モデルが未配置のため {download_url} から初回ダウンロードします...")
            urllib.request.urlretrieve(download_url, DEFAULT_CNN_MODEL_PATH)
            if os.path.exists(DEFAULT_CNN_MODEL_PATH):
                return DEFAULT_CNN_MODEL_PATH
        except Exception as e:
            logger.warning(f"CNNモデルの自動ダウンロードに失敗しました: {e}")

    logger.info(
        "同梱の汎用CNNモデルが見つかりません (%s)。"
        "ヒューリスティック/XGBoostベースの推定にフォールバックします。"
        "models/phoneme_cnn_pretrained.pt を配置するか、"
        "環境変数 VOSE_CNN_MODEL_URL を設定すると初回から精度が向上します。",
        DEFAULT_CNN_MODEL_PATH,
    )
    return None

# 【pyright対応】if/elseでクラスを2つ定義すると "obscured by a declaration of the
# same name" (reportRedeclaration) となり、pyrightが後方の宣言だけを型情報として
# 採用してしまうため、nn.Module由来のメソッド（load_state_dict/to/eval/__call__等）が
# 未知の属性として扱われるエラーが多数発生していた。
# ベースクラスを動的に切り替える単一のクラス定義に統合することでこれを解消する。
_PhonemeCNNBase = nn.Module if TORCH_AVAILABLE else object


class PhonemeCNN(_PhonemeCNNBase):  # type: ignore[misc]
    """メルスペクトログラムから音素を分類する1D CNN

    torch非搭載環境でもモジュール全体のimportがNameErrorで落ちないよう、
    ベースクラスをobjectにフォールバックする。実際のインスタンス化は
    TORCH_AVAILABLEがFalseの場合はRuntimeErrorとなる
    （PhonemeRecognizer側もuse_cnnフラグで使用を制御している）。
    """

    def __init__(self, n_mels=40, n_classes=50):
        if not TORCH_AVAILABLE:
            raise RuntimeError("PyTorch is not available; PhonemeCNN cannot be instantiated.")
        super().__init__()
        self.conv1 = nn.Conv1d(1, 64, kernel_size=5, stride=2, padding=2)
        self.bn1 = nn.BatchNorm1d(64)
        self.conv2 = nn.Conv1d(64, 128, kernel_size=5, stride=2, padding=2)
        self.bn2 = nn.BatchNorm1d(128)
        self.conv3 = nn.Conv1d(128, 256, kernel_size=5, stride=2, padding=2)
        self.bn3 = nn.BatchNorm1d(256)
        self.gap = nn.AdaptiveAvgPool1d(1)
        self.fc1 = nn.Linear(256, 128)
        self.fc2 = nn.Linear(128, n_classes)

    def forward(self, x):
        x = F.relu(self.bn1(self.conv1(x)))
        x = F.relu(self.bn2(self.conv2(x)))
        x = F.relu(self.bn3(self.conv3(x)))
        x = self.gap(x).squeeze(-1)
        x = F.relu(self.fc1(x))
        return F.softmax(self.fc2(x), dim=1)

def _select_torch_device() -> Optional["torch.device"]:
    """
    利用可能な最速デバイスを自動選択する。
    優先順位: CUDA (NVIDIA GPU) > MPS (Apple Silicon GPU/NPU) > CPU
    """
    if not TORCH_AVAILABLE:
        return None
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


class PhonemeRecognizer:
    """音素認識の統合インターフェース"""

    # 日本語音素 → 子音タイプ マッピング
    CONSONANT_TYPES = {
        'k': 'unvoiced_stop', 't': 'unvoiced_stop', 'p': 'unvoiced_stop',
        's': 'fricative', 'h': 'fricative', 'sh': 'fricative',
        'ch': 'affricate', 'ts': 'affricate', 'j': 'affricate',
        'b': 'voiced_stop', 'd': 'voiced_stop', 'g': 'voiced_stop',
        'm': 'nasal', 'n': 'nasal', 'N': 'nasal',
        'r': 'liquid', 'w': 'approximant', 'y': 'approximant',
        'cl': 'special', 'pau': 'special'
    }

    # 【ファイル名サニタイズ】G2Pに不要なノイズ文字を除去するための正規表現。
    # 連番・拡張子残骸・記号・空白・アンダースコア・括弧書きの注記などは
    # pyopenjtalk.g2p() の解析精度を落とすため、事前に取り除く。
    # 残すのは「ひらがな・カタカナ・漢字・ローマ字（a-zA-Z）」のみ。
    _NON_LINGUISTIC_CHARS = re.compile(
        r'[0-9０-９'                     # 半角/全角数字（連番）
        r'_\-\.\+\(\)\[\]\{\}'          # 記号類
        r'（）「」『』【】〈〉《》'         # 全角括弧類
        r'～〜~!！?？@#$%^&*=;:,、。・'  # その他記号
        r'\s]+'                          # 空白（半角/全角）
        r'|'
        r'(?<=[a-zA-Zぁ-んァ-ヶ一-龠])(?=[0-9０-９])'  # 念のための境界（未使用だが将来拡張用）
    )

    @classmethod
    def sanitize_filename_for_g2p(cls, raw_name: str) -> str:
        """
        WAVファイル名からG2P解析に不要な文字を取り除く。
        例: "_れ01(強).wav" -> "れ"
            "ka-2_VCV.wav"  -> "kaVCV"（英数字とかなのみ残す）
        拡張子は呼び出し側で既に除去されている前提だが、保険として再度除去する。
        """
        name = os.path.splitext(raw_name)[0]
        # 数字・記号・空白などのノイズを除去（ひらがな/カタカナ/漢字/ローマ字は保持）
        cleaned = cls._NON_LINGUISTIC_CHARS.sub('', name)
        # 上記regexで拾いきれない残存記号を最終防御としてホワイトリスト方式で除去
        cleaned = re.sub(r'[^a-zA-Zぁ-んァ-ヶー一-龠]', '', cleaned)
        return cleaned if cleaned else name

    # 子音タイプ別の基本パラメータ（ヒューリスティックフォールバック用）
    HEURISTIC_BASE = {
        'unvoiced_stop': (30, 120, 45, 180, -10),
        'voiced_stop': (20, 100, 40, 150, -10),
        'fricative': (10, 80, 25, 100, -15),
        'affricate': (25, 110, 40, 160, -10),
        'nasal': (15, 90, 30, 120, -15),
        'liquid': (10, 70, 20, 90, -15),
        'approximant': (5, 60, 15, 80, -20),
        'vowel': (0, 50, 10, 50, -20),
        'special': (0, 20, 0, 20, -10),
    }

    def __init__(self, cnn_model_path: Optional[str] = None):
        self.use_g2p = PYOPENJTALK_AVAILABLE
        # 【事前学習済みモデル同梱対応】明示指定がなければ同梱モデルを自動探索
        resolved_path = resolve_cnn_model_path(cnn_model_path)
        self.use_cnn = TORCH_AVAILABLE and resolved_path is not None
        self.cnn_model = None
        self.device = _select_torch_device() if TORCH_AVAILABLE else None
        if self.use_cnn and resolved_path is not None:
            self.cnn_model = PhonemeCNN()
            try:
                self.cnn_model.load_state_dict(torch.load(resolved_path, map_location='cpu'))
                # 【GPU/NPU対応】デバイスが None でない場合のみ to() を呼ぶ
                if self.device is not None:
                    self.cnn_model.to(self.device)
                    self.cnn_model.eval()
                    logger.info(f"PhonemeCNN loaded on device: {self.device}")
                else:
                    # フォールバック: CPU を使用
                    self.cnn_model.to("cpu")
                    self.cnn_model.eval()
                    logger.warning("PhonemeCNN loaded on CPU (no accelerator available).")
            except Exception as e:
                # 読み込み失敗時は use_cnn を False にし、cnn_model を None に戻す
                logger.warning(f"Failed to load CNN model at {resolved_path}: {e}")
                self.use_cnn = False
                self.cnn_model = None

    def recognize(self, x: np.ndarray, sr: int, filename: str = "") -> AcousticFeatures:
        """音声波形から音響特徴量と音素情報を抽出"""
        # 1. 最終的に格納する変数の初期化（安全のためのデフォルト値）
        phoneme = 'a'
        consonant = ''
        consonant_type = 'vowel'
        vowel = 'a'
        context_prev = ''
        context_next = ''

        # 2. ファイル名からG2P推定
        if self.use_g2p and filename:
            try:
                base_raw = os.path.basename(filename)
                base = self.sanitize_filename_for_g2p(base_raw)
                raw = pyopenjtalk.g2p(base, kana=False)
                
                if raw:
                    parts = raw.split()
                    if parts:
                        # 'sil', 'pau' などの判定をシンプルに
                        phoneme = parts[0]
                        
                        # 子音と子音タイプの判定
                        consonant = ''.join([c for c in phoneme if c not in 'aiueoN'])
                        consonant_type = self.CONSONANT_TYPES.get(
                            consonant, 
                            'vowel' if not consonant else 'unvoiced_stop'
                        )
                        
                        # 母音抽出
                        # 【VCV対応】VCV音源（例: "a ka" のような連続音）では、
                        # エイリアスが表す実際の発声区間は「末尾の母音」に対応する。
                        # CV音源では vowels は通常1要素のみなので、
                        # vowels[-1] に変更しても単独音への影響はなく、VCV精度のみ向上する。
                        vowels = [p for p in parts if p in 'aiueo']
                        vowel = vowels[-1] if vowels else 'a'
                        
                        # 【重要修正】単一ファイル名処理時の文脈情報バグを修正
                        context_prev = '' 
                        if len(parts) > 1:
                            context_next = parts[1] if parts[1] not in ('sil', 'pau') else ''
                            
            except Exception as e:
                # エラーの握りつぶしをやめ、警告を残す
                logger.warning(f"G2P Processing failed for {filename}: {e}")

        # 3. CNNによる補正（波形から推定）
        if self.use_cnn and self.cnn_model is not None:
            try:
                mel_spec = self._compute_mel_spectrogram(x, sr)
                with torch.no_grad():
                    mel_tensor = torch.from_numpy(mel_spec).unsqueeze(0).unsqueeze(0).float()
                    # 【GPU/NPU対応】cuda専用判定をやめ、選択済みdevice(cuda/mps/cpu)へ統一転送
                    mel_tensor = mel_tensor.to(self.device)

                    # 【重要修正】生出力(Logits)を確率(0.0〜1.0)に変換するためにSoftmaxを適用
                    logits = self.cnn_model(mel_tensor)
                    probs = torch.softmax(logits, dim=1)
                    
                    pred_idx = int(torch.argmax(probs, dim=1).item())
                    confidence = probs[0, pred_idx].item()
                    
                    # 確信度が60%を超えた場合のみG2Pの結果を上書き
                    if confidence > 0.6:
                        # ★ 音素ID→文字列のマッピング（実際のモデルのクラスと一致させること）
                        id_to_phoneme = {0: 'a', 1: 'i', 2: 'u', 3: 'e', 4: 'o', 5: 'k', 6: 's'}
                        cnn_phoneme = id_to_phoneme.get(pred_idx, phoneme)
                        
                        if cnn_phoneme != phoneme:
                            phoneme = cnn_phoneme
                            consonant = ''.join([c for c in phoneme if c not in 'aiueoN'])
                            consonant_type = self.CONSONANT_TYPES.get(
                                consonant, 
                                'vowel' if not consonant else 'unvoiced_stop'
                            )
                            # 必要であればここで vowel も再計算する
                            
            except Exception as e:
                logger.warning(f"CNN Inference failed: {e}")

        # 4. 音響特徴量抽出と最終データの格納
        # 【重要修正】G2PとCNNの「両方の判定」が終わってから、最終的な情報をセットする
        features = self._extract_acoustic_features(x, sr)
        
        features.phoneme = phoneme
        features.consonant_type = consonant_type
        features.vowel = vowel
        features.context_prev = context_prev
        features.context_next = context_next

        return features

    def _compute_mel_spectrogram(self, x: np.ndarray, sr: int) -> np.ndarray:
        """メルスペクトログラムを計算（CNN入力用）"""
        n_fft = 512
        hop = 128
        n_mels = 40
        _, _, spec_complex = signal.stft(x, fs=sr, nperseg=n_fft, noverlap=n_fft - hop, window='hann')
        spec = np.abs(spec_complex)
        # 簡易メルフィルタバンク（実際はlibrosa推奨）
        mel_basis = self._mel_filterbank(sr, n_fft, n_mels)
        mel_spec = mel_basis @ spec
        mel_spec = np.log(mel_spec + 1e-10)
        # 時間方向に正規化
        mel_spec = (mel_spec - mel_spec.mean()) / (mel_spec.std() + 1e-8)
        return mel_spec

    def _mel_filterbank(self, sr: int, n_fft: int, n_mels: int) -> np.ndarray:
        """簡易メルフィルタバンク（実装簡略化のため）"""
        # 実際の実装では librosa.filters.mel を使用推奨
        mel_min = 0
        mel_max = 2595 * np.log10(1 + sr / 2 / 700)
        mel_points = np.linspace(mel_min, mel_max, n_mels + 2)
        hz_points = 700 * (10 ** (mel_points / 2595) - 1)
        bins = np.floor((n_fft + 1) * hz_points / sr).astype(int)
        fbank = np.zeros((n_mels, n_fft // 2 + 1))
        for i in range(n_mels):
            left = bins[i]
            center = bins[i + 1]
            right = bins[i + 2]
            for j in range(left, center):
                fbank[i, j] = (j - left) / (center - left)
            for j in range(center, right):
                fbank[i, j] = (right - j) / (right - center)
        return fbank

    def _extract_acoustic_features(self, x: np.ndarray, sr: int) -> AcousticFeatures:
        """48次元の音響特徴量を抽出"""
        frame_len = int(sr * 0.025)
        hop_len = int(sr * 0.010)
        n_frames = max(1, (len(x) - frame_len) // hop_len + 1)

        # 初期化
        rms = np.zeros(n_frames)
        zcr = np.zeros(n_frames)
        spectral_centroids = np.zeros(n_frames)
        spectral_bandwidths = np.zeros(n_frames)
        spectral_rolloffs = np.zeros(n_frames)
        spectral_flux = np.zeros(n_frames)
        spectral_crest = np.zeros(n_frames)
        spectral_slope = np.zeros(n_frames)
        prev_fft = None

        # フレーム処理
        for i in range(n_frames):
            start = i * hop_len
            end = min(start + frame_len, len(x))
            seg = x[start:end]
            if len(seg) < frame_len:
                seg = np.pad(seg, (0, frame_len - len(seg)))
            windowed = seg * np.hanning(frame_len)

            # RMS
            rms[i] = np.sqrt(np.mean(seg ** 2))

            # ZCR
            zcr[i] = np.sum(np.abs(np.diff(np.sign(seg)))) / (2 * len(seg)) if len(seg) > 1 else 0

            # FFT
            fft_vals = np.abs(np.asarray(rfft(windowed)))
            freqs = rfftfreq(frame_len, d=1 / sr)
            sum_fft = np.sum(fft_vals)

            if sum_fft > 0:
                spectral_centroids[i] = np.sum(freqs * fft_vals) / sum_fft
                spectral_bandwidths[i] = np.sqrt(np.sum((freqs - spectral_centroids[i]) ** 2 * fft_vals) / sum_fft)
                # ロールオフ (85%)
                cumsum = np.cumsum(fft_vals)
                spectral_rolloffs[i] = freqs[np.argmax(cumsum >= 0.85 * cumsum[-1])]
                # クレストファクター
                spectral_crest[i] = np.max(fft_vals) / (np.mean(fft_vals) + 1e-10)
                # スペクトル傾斜（線形回帰）
                if len(freqs) > 1:
                    slope = np.polyfit(freqs, fft_vals, 1)[0]
                    spectral_slope[i] = slope

            if prev_fft is not None:
                spectral_flux[i] = np.sum((fft_vals - prev_fft) ** 2)
            prev_fft = fft_vals.copy()

        # エンベロープ解析
        envelope = np.abs(np.asarray(signal.hilbert(x))) if len(x) > 0 else np.zeros_like(x)
        env_smooth = np.convolve(envelope, np.ones(int(sr * 0.005)) / int(sr * 0.005), mode='same')
        env_diff = np.diff(env_smooth, prepend=0)
        env_accel = np.diff(env_diff, prepend=0)

        # Onset / Attack / Release
        search_len = min(int(sr * 0.1), len(env_accel))
        onset_idx = np.argmax(env_accel[:search_len]) if search_len > 0 else 0
        peak_idx = np.argmax(env_smooth)
        attack_time = (peak_idx - onset_idx) / sr if peak_idx > onset_idx else 0.0

        release_idx = peak_idx
        for i in range(peak_idx, len(env_smooth)):
            if env_smooth[i] < env_smooth[peak_idx] * 0.368:
                release_idx = i
                break
        release_time = (release_idx - peak_idx) / sr

        # フォルマントトラッキング
        tracker = FormantTracker(sr)
        formants = tracker.track(x)
        f1_vals = formants[:, 0]
        f2_vals = formants[:, 1]
        f3_vals = formants[:, 2]
        f4_vals = formants[:, 3]

        def safe_stats(vals):
            v = vals[vals > 0]
            if len(v) == 0:
                return (0.0, 0.0, 0.0, 0.0)
            return (float(np.mean(v)), float(np.std(v)),
                    float(np.mean(np.square(v))), float(np.mean(np.power(v, 3))))

        f1_mean, f1_std, _, _ = safe_stats(f1_vals)
        f2_mean, f2_std, _, _ = safe_stats(f2_vals)
        f3_mean, f3_std, _, _ = safe_stats(f3_vals)
        f4_mean, f4_std, _, _ = safe_stats(f4_vals)

        # MFCC (簡易: DCTベース)
        mfcc = self._compute_mfcc(x, sr)

        return AcousticFeatures(
            onset_time=float(onset_idx / sr),
            attack_time=float(attack_time),
            decay_time=0.0,
            release_time=float(release_time),
            rms_max=float(np.max(rms)) if len(rms) > 0 else 0.0,
            rms_mean=float(np.mean(rms)) if len(rms) > 0 else 0.0,
            rms_std=float(np.std(rms)) if len(rms) > 0 else 0.0,
            rms_skew=float(self._skewness(rms)),
            rms_kurt=float(self._kurtosis(rms)),
            zcr_mean=float(np.mean(zcr)) if len(zcr) > 0 else 0.0,
            zcr_std=float(np.std(zcr)) if len(zcr) > 0 else 0.0,
            zcr_skew=float(self._skewness(zcr)),
            centroid_mean=float(np.mean(spectral_centroids)),
            centroid_std=float(np.std(spectral_centroids)),
            bandwidth_mean=float(np.mean(spectral_bandwidths)),
            bandwidth_std=float(np.std(spectral_bandwidths)),
            rolloff_mean=float(np.mean(spectral_rolloffs)),
            rolloff_std=float(np.std(spectral_rolloffs)),
            flatness_mean=float(np.mean(spectral_centroids / (spectral_bandwidths + 1e-10))),
            flatness_std=float(np.std(spectral_centroids / (spectral_bandwidths + 1e-10))),
            flux_mean=float(np.mean(spectral_flux)),
            flux_std=float(np.std(spectral_flux)),
            f1_mean=f1_mean,
            f1_std=f1_std,
            f2_mean=f2_mean,
            f2_std=f2_std,
            f3_mean=f3_mean,
            f3_std=f3_std,
            f4_mean=f4_mean,
            f4_std=f4_std,
            f1_f2_ratio=f1_mean / (f2_mean + 1e-10),
            f1_f3_ratio=f1_mean / (f3_mean + 1e-10),
            spectral_slope_mean=float(np.mean(spectral_slope)),
            spectral_slope_std=float(np.std(spectral_slope)),
            spectral_crest_mean=float(np.mean(spectral_crest)),
            spectral_crest_std=float(np.std(spectral_crest)),
            mfcc=mfcc,
            phoneme='',
            consonant_type='',
            vowel='',
            context_prev='',
            context_next='',
        )

    @staticmethod
    def _skewness(vals: np.ndarray) -> float:
        if len(vals) < 2:
            return 0.0
        return float(np.mean((vals - np.mean(vals)) ** 3) / (np.std(vals) ** 3 + 1e-10))

    @staticmethod
    def _kurtosis(vals: np.ndarray) -> float:
        if len(vals) < 2:
            return 0.0
        return float(np.mean((vals - np.mean(vals)) ** 4) / (np.std(vals) ** 4 + 1e-10) - 3)

    def _compute_mfcc(self, x: np.ndarray, sr: int, n_mfcc: int = 13) -> List[float]:
        """
        MFCC計算。librosaがあれば優先使用し、無い場合は
        本クラス既存の独自メルフィルタバンク（_mel_filterbank）+ DCT-II で代替する。
        【改善】従来は librosa 非搭載時に [0.0]*13 を返し特徴量が完全に無効化されていたが、
        これにより librosa なしでも意味のあるMFCC相当の特徴量を得られる。
        """
        try:
            import librosa  # type: ignore[import-not-found]
            mfcc = librosa.feature.mfcc(y=x, sr=sr, n_mfcc=n_mfcc)
            return list(np.mean(mfcc, axis=1))
        except ImportError:
            return self._compute_mfcc_fallback(x, sr, n_mfcc)

    def _compute_mfcc_fallback(self, x: np.ndarray, sr: int, n_mfcc: int = 13) -> List[float]:
        """librosa非依存のMFCC代替実装（メルフィルタバンク + DCT-II）"""
        if len(x) < 32:
            return [0.0] * n_mfcc
        n_fft = 512
        hop = 160
        n_mels = max(n_mfcc + 2, 20)
        try:
            _, _, spec_complex = signal.stft(x, fs=sr, nperseg=n_fft, noverlap=max(0, n_fft - hop), window='hann')
            spec = np.abs(spec_complex)
            mel_basis = self._mel_filterbank(sr, n_fft, n_mels)
            mel_spec = mel_basis @ spec
            log_mel = np.log(mel_spec + 1e-10)
            log_mel_mean = np.mean(log_mel, axis=1)  # (n_mels,)

            # DCT-II（scipy非依存の直接実装、要素数が少ないため計算コストは無視できる）
            N = n_mels
            k = np.arange(n_mfcc)
            n = np.arange(N)
            dct_basis = np.cos(np.pi / N * (n[None, :] + 0.5) * k[:, None])
            mfcc_vec = dct_basis @ log_mel_mean
            return list(mfcc_vec.astype(float))
        except Exception as e:
            logger.warning(f"MFCC fallback computation failed: {e}")
            return [0.0] * n_mfcc


# ═══════════════════════════════════════════════════════════════════════
# 4. 機械学習予測モデル (XGBoost + オンライン更新)
# ═══════════════════════════════════════════════════════════════════════

class OtoPredictor:
    """XGBoostによるotoパラメータ予測 + オンライン学習"""

    def __init__(self, model_path: Optional[str] = None):
        self.model = None
        self.scaler = StandardScaler()
        self.is_trained = False
        self.feature_names = [
            'onset_time', 'attack_time', 'release_time',
            'rms_max', 'rms_mean', 'rms_std', 'rms_skew', 'rms_kurt',
            'zcr_mean', 'zcr_std', 'zcr_skew',
            'centroid_mean', 'centroid_std', 'bandwidth_mean', 'bandwidth_std',
            'rolloff_mean', 'rolloff_std', 'flatness_mean', 'flatness_std',
            'flux_mean', 'flux_std',
            'f1_mean', 'f1_std', 'f2_mean', 'f2_std',
            'f3_mean', 'f3_std', 'f4_mean', 'f4_std',
            'f1_f2_ratio', 'f1_f3_ratio',
            'spectral_slope_mean', 'spectral_slope_std',
            'spectral_crest_mean', 'spectral_crest_std',
        ] + [f'mfcc_{i}' for i in range(13)]
        # カテゴリ変数用エンコーディング（簡易）
        self.category_map = {}
        self.target_columns = ['offset', 'preutter', 'overlap', 'constant', 'blank']
        # ★ FIX: model_path が None でない場合のみ load を呼ぶ
        if model_path is not None and os.path.exists(model_path):
            self.load(model_path)

    def _features_to_vector(self, features: AcousticFeatures) -> np.ndarray:
        """AcousticFeatures → 数値ベクトル（48次元）"""
        vec = [
            features.onset_time, features.attack_time, features.release_time,
            features.rms_max, features.rms_mean, features.rms_std,
            features.rms_skew, features.rms_kurt,
            features.zcr_mean, features.zcr_std, features.zcr_skew,
            features.centroid_mean, features.centroid_std,
            features.bandwidth_mean, features.bandwidth_std,
            features.rolloff_mean, features.rolloff_std,
            features.flatness_mean, features.flatness_std,
            features.flux_mean, features.flux_std,
            features.f1_mean, features.f1_std,
            features.f2_mean, features.f2_std,
            features.f3_mean, features.f3_std,
            features.f4_mean, features.f4_std,
            features.f1_f2_ratio, features.f1_f3_ratio,
            features.spectral_slope_mean, features.spectral_slope_std,
            features.spectral_crest_mean, features.spectral_crest_std,
        ]
        vec.extend(features.mfcc)
        return np.array(vec, dtype=np.float64)

    def _encode_category(self, features: AcousticFeatures) -> np.ndarray:
        """カテゴリ変数を数値エンコード"""
        # 子音タイプをワンホット化（簡易: 辞書でマッピング）
        ctype_map = {
            'unvoiced_stop': 0, 'voiced_stop': 1, 'fricative': 2,
            'affricate': 3, 'nasal': 4, 'liquid': 5,
            'approximant': 6, 'vowel': 7, 'special': 8
        }
        vowel_map = {'a': 0, 'i': 1, 'u': 2, 'e': 3, 'o': 4}
        ctype_enc = ctype_map.get(features.consonant_type, 0)
        vowel_enc = vowel_map.get(features.vowel, 0)
        return np.array([ctype_enc, vowel_enc], dtype=np.float64)

    def train(self, features_list: List[AcousticFeatures], params_list: List[OtoParams]) -> None:
        if len(features_list) < 10:
            raise ValueError("最低10サンプル必要です")
        X_raw = np.array([self._features_to_vector(f) for f in features_list])
        X_cat = np.array([self._encode_category(f) for f in features_list])
        X = np.hstack([X_raw, X_cat])
        y = np.array([[p.offset, p.preutter, p.overlap, p.constant, p.blank] for p in params_list])

        self.scaler.fit(X)
        X_scaled = self.scaler.transform(X)

        if XGB_AVAILABLE:
            self.model = xgb.XGBRegressor(
                n_estimators=200, max_depth=8, learning_rate=0.05,
                subsample=0.8, colsample_bytree=0.8,
                tree_method='hist', random_state=42
            )
            self.model.fit(X_scaled, y)
        else:
            # XGBoostがない場合のフォールバック（RandomForest）
            try:
                from sklearn.ensemble import RandomForestRegressor  # type: ignore[import-not-found]
            except ImportError:
                # 【改善】XGBoost・sklearn.ensembleいずれも無い環境では、
                # 学習をスキップしてヒューリスティック推定のみで動作させる（例外で落とさない）。
                logger.warning(
                    "XGBoostもscikit-learn(RandomForestRegressor)も利用できないため、"
                    "機械学習モデルの学習をスキップし、ヒューリスティック推定にフォールバックします。"
                )
                self.is_trained = False
                return
            self.model = RandomForestRegressor(n_estimators=100, max_depth=12, random_state=42)
            self.model.fit(X_scaled, y)

        self._training_X = X
        self._training_y = y
        self.is_trained = True

    def predict(self, features: AcousticFeatures) -> OtoParams:
        if not self.is_trained or self.model is None:
            return self._heuristic_predict(features)
        X_raw = self._features_to_vector(features).reshape(1, -1)
        X_cat = self._encode_category(features).reshape(1, -1)
        X = np.hstack([X_raw, X_cat])
        X_scaled = self.scaler.transform(X)
        pred = self.model.predict(X_scaled)[0]
        # パラメータの範囲補正
        return OtoParams(
            offset=float(np.clip(pred[0], 0, 200)),
            preutter=float(np.clip(pred[1], 10, 400)),
            overlap=float(np.clip(pred[2], 0, 200)),
            constant=float(np.clip(pred[3], 0, 400)),
            blank=float(np.clip(pred[4], -200, 0)),
            confidence=0.85,
            phoneme=features.phoneme,
            consonant_type=features.consonant_type,
            vowel=features.vowel,
        )

    def update(self, features_list: List[AcousticFeatures], params_list: List[OtoParams]) -> None:
        """オンライン学習（追加学習）"""
        if not self.is_trained or len(features_list) < 5:
            self.train(features_list, params_list)
            return
        X_raw = np.array([self._features_to_vector(f) for f in features_list])
        X_cat = np.array([self._encode_category(f) for f in features_list])
        X = np.hstack([X_raw, X_cat])
        X_scaled = self.scaler.transform(X)
        y = np.array([[p.offset, p.preutter, p.overlap, p.constant, p.blank] for p in params_list])

        if self.model is None:
            self.train(features_list, params_list)
            return

        if XGB_AVAILABLE and isinstance(self.model, xgb.XGBRegressor):
            self.model.fit(X_scaled, y, xgb_model=self.model)
        else:
            combined_X = np.vstack([self.scaler.transform(self._training_X), X_scaled])
            combined_y = np.vstack([self._training_y, y])
            self.model.fit(combined_X, combined_y)

    def save(self, path: str) -> None:
        if self.model is None:
            return
        with open(path, 'wb') as f:
            pickle.dump({
                'model': self.model,
                'scaler': self.scaler,
                'is_trained': self.is_trained,
            }, f)

    def load(self, path: str) -> None:
        with open(path, 'rb') as f:
            data = pickle.load(f)
        self.model = data['model']
        self.scaler = data['scaler']
        self.is_trained = data.get('is_trained', True)

    def _heuristic_predict(self, features: AcousticFeatures) -> OtoParams:
        """機械学習が使えない場合のヒューリスティック推定（改善版）"""
        ctype = features.consonant_type
        offset, preutter, overlap, constant, blank = PhonemeRecognizer.HEURISTIC_BASE.get(
            ctype, (20, 100, 40, 150, -10)
        )
        # 母音による微調整
        vowel_factors = {'a': 1.0, 'i': 0.9, 'u': 0.85, 'e': 0.95, 'o': 1.0}
        factor = vowel_factors.get(features.vowel, 1.0)
        preutter *= factor
        overlap *= factor
        constant *= factor
        # RMSに基づく調整（音量が大きいほど先行発声を長く）
        rms_factor = 0.8 + 0.4 * (features.rms_max / (features.rms_max + 0.1))
        preutter *= rms_factor
        overlap *= rms_factor
        return OtoParams(
            offset=float(offset), preutter=float(np.clip(preutter, 10, 400)),
            overlap=float(np.clip(overlap, 0, 200)), constant=float(np.clip(constant, 0, 400)),
            blank=float(np.clip(blank, -200, 0)), confidence=0.5,
            phoneme=features.phoneme, consonant_type=ctype, vowel=features.vowel
        )


# ═══════════════════════════════════════════════════════════════════════
# 5. メインバッチオプティマイザ
# ═══════════════════════════════════════════════════════════════════════

# ─── マルチプロセスワーカー用グローバルキャッシュ ──────────────────────
# 【改善】ProcessPoolExecutor の initializer で子プロセスごとに一度だけ
# PhonemeRecognizer / OtoPredictor をロードし、タスクごとの再インスタンス化
# （モデル読み込みI/O・CNN初期化等）のオーバーヘッドを排除する。
_worker_recognizer: Optional["PhonemeRecognizer"] = None
_worker_predictor: Optional["OtoPredictor"] = None


def _init_worker(model_path: Optional[str]) -> None:
    """ProcessPoolExecutor の initializer。子プロセス起動時に一度だけ呼ばれる。"""
    global _worker_recognizer, _worker_predictor
    _worker_recognizer = PhonemeRecognizer()
    _worker_predictor = OtoPredictor(model_path)


class BatchVoiceOptimizer:
    """oto.ini 一括生成エンジン（完全版）"""

    def __init__(
        self,
        target_sr: int = 16000,
        cache_dir: str = "cache/oto_cache",
        max_workers: Optional[int] = None,
        use_multiprocessing: bool = True,
    ):
        self.target_sr = target_sr
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)
        self.model_path = os.path.join(cache_dir, "oto_predictor.pkl")
        self.predictor = OtoPredictor(self.model_path if os.path.exists(self.model_path) else None)
        self.recognizer = PhonemeRecognizer()
        self.training_features: List[AcousticFeatures] = []
        self.training_labels: List[OtoParams] = []

        # 【改善】プロセスごとにCNNモデル等がメモリ上に複製されるため、
        # メモリの少ない環境ではワーカー数を絞れるようにする。
        # 未指定時は従来通りCPUコア数を使用。
        self.max_workers = max_workers if max_workers is not None else multiprocessing.cpu_count()
        # 【改善】シリアルモード。マルチプロセスを一切使わず、
        # メインプロセス内で1ファイルずつ処理する（CNNモデルの複製が発生しない）。
        # メモリが極端に少ない環境や、デバッグ時のスタックトレース確認に有用。
        self.use_multiprocessing = use_multiprocessing

    def optimize_voice_bank(self, voice_dir: str, force_redo: bool = False) -> Dict[str, OtoParams]:
        wav_files = self._collect_wavs(voice_dir)
        if not wav_files:
            return {}

        tasks = []
        for wav_path in wav_files:
            cache_key = self._get_cache_key(wav_path)
            cache_path = os.path.join(self.cache_dir, f"{cache_key}.json")
            if not force_redo and os.path.exists(cache_path):
                if os.path.getmtime(wav_path) <= os.path.getmtime(cache_path):
                    continue
            tasks.append(wav_path)

        if not tasks:
            print(f"[BatchOptimizer] 全 {len(wav_files)} ファイルキャッシュ済み")
            return self._load_all_caches(wav_files)

        print(f"[BatchOptimizer] {len(tasks)} / {len(wav_files)} ファイルを解析中...")

        results: Dict[str, OtoParams] = {}
        model_path_for_workers = self.model_path if os.path.exists(self.model_path) else None

        if self.use_multiprocessing:
            with ProcessPoolExecutor(
                max_workers=self.max_workers,
                initializer=_init_worker,
                initargs=(model_path_for_workers,),
            ) as executor:
                future_to_path = {
                    executor.submit(self._analyze_single_wav, path, self.target_sr): path
                    for path in tasks
                }
                for future in as_completed(future_to_path):
                    path = future_to_path[future]
                    try:
                        params, features = future.result(timeout=90)
                        results[path] = params
                        self._save_cache(path, params)
                        if params.confidence > 0.5:
                            self.training_features.append(features)
                            self.training_labels.append(params)
                    except Exception as e:
                        print(f"[ERROR] {path}: {e}")
        else:
            # 【シリアルモード】マルチプロセスを使わずメインプロセス内で順次処理。
            # CNNモデルの複製が発生せず、self.recognizer/self.predictorを使い回すため
            # メモリ効率が良い（速度はマルチプロセスより劣る）。
            print("[BatchOptimizer] シリアルモードで実行中（マルチプロセス無効）...")
            for path in tasks:
                try:
                    params, features = self._analyze_wav_with(path, self.target_sr, self.recognizer, self.predictor)
                    results[path] = params
                    self._save_cache(path, params)
                    if params.confidence > 0.5:
                        self.training_features.append(features)
                        self.training_labels.append(params)
                except Exception as e:
                    print(f"[ERROR] {path}: {e}")

        # モデル更新（蓄積データが十分あれば）
        if len(self.training_features) >= 20:
            try:
                print(f"[BatchOptimizer] 機械学習モデルを更新中（{len(self.training_features)}サンプル）...")
                self.predictor.update(self.training_features, self.training_labels)
                self.predictor.save(self.model_path)
            except Exception as e:
                print(f"[BatchOptimizer] モデル更新失敗: {e}")

        cached_results = self._load_all_caches(wav_files)
        results.update(cached_results)
        return results

    @staticmethod
    def _analyze_wav_with(
        wav_path: str,
        target_sr: int,
        recognizer: "PhonemeRecognizer",
        predictor: "OtoPredictor",
    ) -> Tuple[OtoParams, AcousticFeatures]:
        """WAV解析の共通ロジック。渡されたrecognizer/predictorインスタンスを再利用する。"""
        data, sr = sf.read(wav_path, always_2d=False)
        x = np.asarray(data, dtype=np.float64)
        if x.ndim > 1:
            x = np.mean(x, axis=1)
        if sr != target_sr:
            x = np.asarray(signal.resample(x, int(len(x) * target_sr / sr)))
            sr = target_sr

        features = recognizer.recognize(x, sr, wav_path)
        params = predictor.predict(features)

        # 信号処理による微調整
        params = BatchVoiceOptimizer._refine_params(x, sr, params, features)
        return params, features

    @staticmethod
    def _analyze_single_wav(wav_path: str, target_sr: int) -> Tuple[OtoParams, AcousticFeatures]:
        """1WAV解析（子プロセス用）。initializerでロード済みのグローバルモデルを再利用する。"""
        # 【改善】initializer未使用（例: 直接呼び出しやテスト時）でも動作するよう
        # グローバルが未設定の場合はその場でインスタンス化するフォールバックを残す。
        global _worker_recognizer, _worker_predictor
        recognizer = _worker_recognizer if _worker_recognizer is not None else PhonemeRecognizer()
        predictor = _worker_predictor if _worker_predictor is not None else OtoPredictor(None)
        return BatchVoiceOptimizer._analyze_wav_with(wav_path, target_sr, recognizer, predictor)

    @staticmethod
    def _refine_params(x: np.ndarray, sr: int, params: OtoParams, features: AcousticFeatures) -> OtoParams:
        """エンベロープ加速度とRMSを用いた精密微調整"""
        if len(x) == 0:
            return params

        # エンベロープ
        envelope = np.abs(np.asarray(signal.hilbert(x)))
        env_smooth = np.convolve(envelope, np.ones(int(sr * 0.005)) / int(sr * 0.005), mode='same')
        env_diff = np.diff(env_smooth, prepend=0)
        env_accel = np.diff(env_diff, prepend=0)

        # onset補正（加速度最大点）
        search_len = min(int(sr * 0.05), len(env_accel))
        if search_len > 0:
            actual_onset = np.argmax(np.abs(env_accel[:search_len]))
            params.offset = float((actual_onset / sr) * 1000.0)

        # preutter補正（RMSが80%に達する点）
        peak_idx = np.argmax(env_smooth)
        if peak_idx > 0:
            search_start = max(0, int(params.offset * sr / 1000.0))
            search_end = min(peak_idx + int(sr * 0.1), len(x))
            if search_end > search_start + 10:
                rms_vals = []
                frame_len = int(sr * 0.005)
                for i in range(search_start, search_end, frame_len):
                    seg = x[i:i + frame_len]
                    if len(seg) > 0:
                        rms_vals.append(np.sqrt(np.mean(seg ** 2)))
                
                if rms_vals:
                    rms_arr = np.array(rms_vals)
                    target = 0.8 * np.max(rms_arr)
                    idx_80 = np.argmax(rms_arr >= target) if np.any(rms_arr >= target) else len(rms_arr) // 2
                    stable_ms = (search_start + idx_80 * frame_len) / sr * 1000
                    params.preutter = float(np.clip(stable_ms, 10, 400))

        # オーバーラップはpreutterに連動（経験則）
        if params.preutter > 0:
            ratio = 0.35 if features.consonant_type in ('fricative', 'affricate') else 0.45
            params.overlap = params.preutter * ratio

        # ブランクはノイズテイルを検出
        tail_len = min(int(sr * 0.2), len(x))
        if tail_len > 100:
            tail = x[-tail_len:]
            rms_tail = np.sqrt(np.mean(tail ** 2))
            if rms_tail < 0.001:
                params.blank = -5.0
            else:
                params.blank = -10.0

        return params

    def _get_cache_key(self, path: str) -> str:
        return hashlib.md5(path.encode('utf-8')).hexdigest()

    def _save_cache(self, wav_path: str, params: OtoParams) -> None:
        cache_key = self._get_cache_key(wav_path)
        cache_path = os.path.join(self.cache_dir, f"{cache_key}.json")
        with open(cache_path, 'w') as f:
            json.dump(asdict(params), f)

    def _load_cache(self, wav_path: str) -> Optional[OtoParams]:
        cache_key = self._get_cache_key(wav_path)
        cache_path = os.path.join(self.cache_dir, f"{cache_key}.json")
        if not os.path.exists(cache_path):
            return None
        try:
            with open(cache_path, 'r') as f:
                data = json.load(f)
            return OtoParams(**data)
        except Exception:
            return None

    def _load_all_caches(self, wav_files: List[str]) -> Dict[str, OtoParams]:
        results = {}
        for path in wav_files:
            cached = self._load_cache(path)
            if cached:
                results[path] = cached
        return results

    @staticmethod
    def _collect_wavs(voice_dir: str) -> List[str]:
        wavs = []
        for root, _, files in os.walk(voice_dir):
            for f in files:
                if f.lower().endswith('.wav'):
                    wavs.append(os.path.join(root, f))
        return wavs

    @staticmethod
    def export_oto_ini(voice_dir: str, params_map: Dict[str, OtoParams], output_name: str = "oto.ini") -> None:
        oto_path = os.path.join(voice_dir, output_name)
        lines = []
        for wav_path, p in params_map.items():
            fname = os.path.basename(wav_path)
            alias = os.path.splitext(fname)[0]
            line = f"{fname}={alias},{p.offset:.0f},{p.constant:.0f},{p.blank:.0f},{p.preutter:.0f},{p.overlap:.0f}"
            lines.append(line)
        with open(oto_path, 'w', encoding='cp932', errors='replace') as f:
            f.write("\n".join(lines))
        print(f"[BatchOptimizer] {len(lines)} エントリを {oto_path} に出力")


# ═══════════════════════════════════════════════════════════════════════
# 6. エントリポイント
# ═══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(
            "Usage: python batch_voice_optimizer.py <voice_dir> [--force] "
            "[--serial] [--max-workers N]"
        )
        sys.exit(1)
    voice_dir = sys.argv[1]
    force = "--force" in sys.argv
    # 【改善】メモリの少ない環境向けにシリアルモード/ワーカー数制限をCLIから指定可能に
    use_mp = "--serial" not in sys.argv
    max_workers = None
    if "--max-workers" in sys.argv:
        idx = sys.argv.index("--max-workers")
        if idx + 1 < len(sys.argv):
            max_workers = int(sys.argv[idx + 1])
    optimizer = BatchVoiceOptimizer(max_workers=max_workers, use_multiprocessing=use_mp)
    results = optimizer.optimize_voice_bank(voice_dir, force_redo=force)
    if results:
        BatchVoiceOptimizer.export_oto_ini(voice_dir, results)
    else:
        print("[BatchOptimizer] 処理対象がありませんでした")
