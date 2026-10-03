# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
# modules/ai/ai_manager.py

import os
import sys
import logging
import json
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Dict

try:
    from PySide6.QtCore import QObject, Signal
except ImportError:
    from modules.utils.pyside_stub import QObject, Signal

from modules.data.licensing import LicenseManager

logger = logging.getLogger(__name__)


# Pyright の継承エラーを回避するため、# type: ignore を付与
class AIManager(QObject):  # type: ignore
    """
    VO-SE Pro / Ultra: AI推論マネージャー (フェーズ1: 刷新版)
    """

    # Signal の型注釈はそのままでOK（Pyright はスタブの制限で警告が出るが、# type: ignore で回避）
    finished = Signal(object)  # type: ignore
    error = Signal(str)        # type: ignore

    def __init__(self):
        # QObject.__init__ を呼び出す（スタブでは何もしないが、実際の PySide6 では必要）
        super().__init__()  # type: ignore
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.model_path = self._get_model_path()
        self.phoneme_dict: Dict[str, Any] = {}
        self.dict_path = self._get_dict_path()
        self.init_model()

    # ============================================================
    # パス解決
    # ============================================================

    def _get_model_path(self) -> str:
        try:
            if getattr(sys, 'frozen', False):
                base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
            else:
                base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            models_dir = os.path.join(base, "models")

            if LicenseManager.is_pro():
                pro_model = os.path.join(models_dir, "vose_pro_vits.onnx")
                if os.path.exists(pro_model):
                    logger.info(f"[AI] Pro/Ultra VITS model selected: {pro_model}")
                    return pro_model

            return os.path.join(models_dir, "vose_default_core.onnx")
        except Exception as e:
            logger.error(f"Failed to resolve model path: {e}")
            return ""

    def _get_dict_path(self) -> str:
        if getattr(sys, 'frozen', False):
            base = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
        else:
            base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(base, "dicts", "phoneme_table.json")

    # ============================================================
    # モデルおよび辞書の初期化
    # ============================================================

    def init_model(self) -> bool:
        try:
            if os.path.exists(self.dict_path):
                with open(self.dict_path, 'r', encoding='utf-8') as f:
                    self.phoneme_dict = json.load(f)
                logger.info(f"[AI] Phoneme dictionary loaded: {self.dict_path}")
            else:
                logger.warning(f"[AI] Phoneme dictionary not found: {self.dict_path}. Using empty dict.")

            logger.info("[AI] VITS Core Interface initialized successfully.")
            return True

        except Exception as e:
            error_msg = f"AI Init Error: {e}"
            logger.error(error_msg)
            self.error.emit(error_msg)  # type: ignore
            return False

    # ============================================================
    # 音素解析
    # ============================================================

    def text_to_phonemes(self, text: str) -> List[str]:
        if not text:
            return []

        words_map = self.phoneme_dict.get("words", {})
        if text in words_map:
            return words_map[text]

        try:
            import pyopenjtalk
            g2p_result = pyopenjtalk.g2p(text, kana=False)
            phonemes = [p for p in g2p_result.split() if p]
            if phonemes:
                return phonemes
        except Exception as e:
            logger.debug(f"[AI] pyopenjtalk g2p failed for '{text}': {e}")

        logger.debug(f"[AI] Word '{text}' using fallback decomposition.")
        return list(text)

    # ============================================================
    # 非同期推論インターフェース
    # ============================================================

    def analyze_async(self, input_context: Any) -> None:
        def task():
            try:
                if isinstance(input_context, dict) and "text" in input_context:
                    text_input = input_context["text"]
                    phonemes = self.text_to_phonemes(text_input)
                    logger.info(f"[AI Task] Target text: '{text_input}' -> Phonemes: {phonemes}")
                else:
                    logger.warning("[AI Task] Received old wave-style input context. Fallback triggered.")
                    phonemes = ["a"]

                # ダミー結果（実際の推論は将来実装）
                fallback_results = [{
                    "onset": 0.0,
                    "overlap": 0.05,
                    "pre_utterance": 0.1
                }]

                self.finished.emit(fallback_results)  # type: ignore

            except Exception as e:
                error_msg = f"AI Inference Task Error: {e}"
                logger.error(error_msg)
                self.error.emit(error_msg)  # type: ignore

        try:
            self.executor.submit(task)
        except Exception as e:
            logger.critical(f"[AI] Failed to submit task to executor: {e}")
            self.error.emit(f"Thread Submission Error: {e}")  # type: ignore

    # ============================================================
    # 将来用: VITS / BigVGAN 波形生成コア（スタブ）
    # ============================================================

    def predict_vits_waveform(self, phonemes: List[str], f0_curve: np.ndarray) -> np.ndarray:
        logger.info(f"[AI Core] VITS Waveform generation requested for {len(phonemes)} phonemes.")
        return np.zeros(1024, dtype=np.float32)

    # ============================================================
    # 終了処理
    # ============================================================

    def shutdown(self) -> None:
        try:
            self.executor.shutdown(wait=False)
            logger.info("[AI] AIManager executor safely shut down.")
        except Exception as e:
            logger.error(f"Error during AIManager shutdown: {e}")
