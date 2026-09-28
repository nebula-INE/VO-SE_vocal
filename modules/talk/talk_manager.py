# talk_manager.py
"""
VO-SE Cut Studio — コアエンジン統合モジュール
- IntonationAnalyzer : pyopenjtalk による音素・F0解析
- generate_talk_events: トークイベント生成
- NoteEvent           : C++ 構造体バインディング
- VoseRendererBridge  : DLL/dylib ブリッジ
- TalkManager         : 音声合成マネージャー

修正点:
  [FIX-1] VoseRendererBridge.render(): wav_path に音素文字列ではなく
          実際の WAV ファイルパスを渡すよう修正。
  [FIX-2] TalkManager.speak(): sounddevice で実装。
  [FIX-3] TalkManager.synthesize(): float32 → int16 変換クリップ修正。
  [FIX-4] _tts_with_voice(): ndarray 単体の戻り値にも対応。
  [FIX-5] speak(): tmp_path を try の前に None 初期化 → finally で None チェック。
  [FIX-6] _tts_default(): pyopenjtalk.tts() の例外を確実にキャッチ。
  [FIX-7] synthesize(): 全サンプルが 0 の場合の max() 処理を安全化。
  [FIX-8] VoseRendererBridge.__init__(): hasattr で存在確認してからシグネチャ設定。
"""
from __future__ import annotations

import os
import ctypes
import platform
import tempfile
import traceback
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import pyopenjtalk
import sounddevice as sd
import soundfile as sf
try:
    from PySide6.QtCore import QObject, Signal
except ImportError:
    from modules.utils.pyside_stub import QObject, Signal
from modules.ffi import CNoteEvent, as_c_double_array


# ══════════════════════════════════════════════════════════════
# 0. 型プロトコル
# ══════════════════════════════════════════════════════════════

class VoiceLibraryProtocol(Protocol):
    def get_wav_path(self, phoneme: str) -> str: ...


# ══════════════════════════════════════════════════════════════
# 1. データクラス
# ══════════════════════════════════════════════════════════════

@dataclass
class AccentPhrase:
    text: str
    mora_count: int
    accent_position: int
    f0_values: list[float] = field(default_factory=list)


# ══════════════════════════════════════════════════════════════
# 2. イントネーション解析
# ══════════════════════════════════════════════════════════════

class IntonationAnalyzer:
    def __init__(self) -> None:
        self.last_analysis_status: bool = False

    def analyze(self, text: str) -> str:
        if not text:
            return ""
        try:
            labels: list[str] = self._get_labels(text)
            self.last_analysis_status = True
            return "\n".join(labels)
        except Exception as e:
            self.last_analysis_status = False
            msg = f"Error during analysis: {e}\n{traceback.format_exc()}"
            print(msg)
            return msg

    def analyze_to_phonemes(self, text: str) -> list[str]:
        if not text:
            return []
        try:
            phoneme_str: str = pyopenjtalk.g2p(text, kana=False)
            return [p for p in phoneme_str.split() if p]
        except Exception as e:
            print(f"[IntonationAnalyzer] g2p error: {e}")
            return []

    def analyze_to_accent_phrases(self, text: str) -> list[AccentPhrase]:
        if not text:
            return []
        try:
            labels = self._get_labels(text)
            return self._parse_labels(labels)
        except Exception as e:
            print(f"[IntonationAnalyzer] accent parse error: {e}")
            return []

    def _get_labels(self, text: str) -> list[str]:
        if hasattr(pyopenjtalk, "run_frontend"):
            features = pyopenjtalk.run_frontend(text)
        else:
            features = pyopenjtalk.extract_fullcontext(text)
        return pyopenjtalk.make_label(features)

    def _parse_labels(self, labels: list[str]) -> list[AccentPhrase]:
        phrases: list[AccentPhrase] = []
        current_moras: list[tuple[str, float]] = []
        accent_pos: int = 0
        prev_phrase_id: str = ""

        for label in labels:
            parts = label.split("-")
            phoneme = parts[1] if len(parts) > 1 else "?"
            phrase_id = self._extract_field(label, "/E:")

            if phrase_id != prev_phrase_id and current_moras:
                phrases.append(AccentPhrase(
                    text="".join(m[0] for m in current_moras),
                    mora_count=len(current_moras),
                    accent_position=accent_pos,
                    f0_values=[m[1] for m in current_moras],
                ))
                current_moras = []

            try:
                a_field = self._extract_field(label, "/A:")
                accent_pos = int(a_field.split("_")[0]) if a_field else 0
            except (ValueError, IndexError):
                accent_pos = 0

            f0 = 130.0 if accent_pos == 0 else 150.0 + accent_pos * 5.0

            if phoneme not in ("sil", "pau", "?"):
                current_moras.append((phoneme, f0))

            prev_phrase_id = phrase_id

        if current_moras:
            phrases.append(AccentPhrase(
                text="".join(m[0] for m in current_moras),
                mora_count=len(current_moras),
                accent_position=accent_pos,
                f0_values=[m[1] for m in current_moras],
            ))

        return phrases

    @staticmethod
    def _extract_field(label: str, key: str) -> str:
        idx = label.find(key)
        if idx == -1:
            return ""
        start = idx + len(key)
        end = label.find("/", start)
        return label[start:end] if end != -1 else label[start:]


# ══════════════════════════════════════════════════════════════
# 3. トークイベント生成
# ══════════════════════════════════════════════════════════════

def generate_accent_curve(phoneme: str, accent_pos: int = 0) -> list[float]:
    base_f0 = 150.0 + accent_pos * 5.0
    voiced = phoneme in list("aeiou") + ["N", "m", "n", "r", "w", "y", "v"]
    return [base_f0 if voiced else 0.0] * 50


def generate_talk_events(
    text: str,
    analyzer: IntonationAnalyzer,
    voice_library: VoiceLibraryProtocol,
) -> list[dict[str, Any]]:
    phonemes = analyzer.analyze_to_phonemes(text)
    accent_phrases = analyzer.analyze_to_accent_phrases(text)

    accent_map: dict[int, int] = {}
    idx = 0
    for phrase in accent_phrases:
        for _ in range(phrase.mora_count):
            accent_map[idx] = phrase.accent_position
            idx += 1

    talk_notes: list[dict[str, Any]] = []
    for i, phoneme in enumerate(phonemes):
        accent_pos = accent_map.get(i, 0)
        pitch_curve = generate_accent_curve(phoneme, accent_pos)
        length = len(pitch_curve)

        wav_path = voice_library.get_wav_path(phoneme)
        if not wav_path:
            print(f"[generate_talk_events] WAV not found for phoneme: '{phoneme}' — skipping")
            continue

        talk_notes.append({
            "phoneme":       phoneme,
            "wav_path":      wav_path,
            "pitch":         pitch_curve,
            "gender":        [0.5] * length,
            "tension":       [0.5] * length,
            "breath":        [0.1] * length,
            "offset":        0.0,
            "consonant":     0.0,
            "cutoff":        0.0,
            "pre_utterance": 0.0,
            "overlap":       0.0,
        })

    return talk_notes


# ══════════════════════════════════════════════════════════════
# 4. C++ 構造体バインディング
# ══════════════════════════════════════════════════════════════

class VoseRendererBridge:
    def __init__(self, dll_path: str) -> None:
        self.lib = None
        self.dll_cookie = None
        try:
            from modules.ffi.vose_api import load_engine

            dll_path = os.path.abspath(dll_path)
            if not os.path.exists(dll_path):
                print(f"❌ File not found: {dll_path}")
                return

            # 共通ローダーを使い、execute_render / init_official_engine の
            # argtypes / restype を一元管理する。
            self.lib = load_engine(search_dirs=[os.path.dirname(dll_path)])
            if self.lib is None:
                print(f"❌ VO-SE Core load failed: {dll_path}")
                return

            init_engine = getattr(self.lib, "init_official_engine", None)
            execute_render = getattr(self.lib, "execute_render", None)
            if init_engine is None or execute_render is None:
                raise AttributeError(
                    "required VO-SE Core symbols are missing: "
                    "init_official_engine / execute_render"
                )

            init_engine()
            print(f"✅ VO-SE Engine Initialized: {dll_path}")

        except OSError as e:
            print(f"❌ OS Error (Dependency issue?): {e}")
            if platform.system() == "Windows":
                print("Hint: MSVC Redistributable がインストールされているか確認してください。")
        except Exception as e:
            print(f"❌ Engine Load Error: {e}\n{traceback.format_exc()}")
            self.lib = None

    def render(self, notes_data: list[dict[str, Any]], output_path: str) -> bool:
        if self.lib is None:
            print("❌ render() called but engine is not loaded.")
            return False
        if not notes_data:
            print("⚠️ render() called with empty notes_data.")
            return False

        note_count = len(notes_data)
        NotesArray = CNoteEvent * note_count
        c_notes = NotesArray()
        keep_alive: list[Any] = []

        for i, data in enumerate(notes_data):
            pitch = data.get("pitch", [])
            gender = data.get("gender", [])
            tension = data.get("tension", [])
            breath = data.get("breath", [])

            if not (len(pitch) == len(gender) == len(tension) == len(breath)):
                print("❌ Curve length mismatch detected — aborting")
                return False

            p_arr = as_c_double_array(pitch)
            g_arr = as_c_double_array(gender)
            t_arr = as_c_double_array(tension)
            b_arr = as_c_double_array(breath)
            keep_alive.extend([p_arr, g_arr, t_arr, b_arr])

            wav_path: str = data.get("wav_path", "")
            if not wav_path or not os.path.exists(wav_path):
                print(f"❌ WAV not found at render time: '{wav_path}' — aborting")
                return False

            c_notes[i].wav_path = wav_path.encode("utf-8")
            c_notes[i].pitch_length = len(pitch)
            c_notes[i].pitch_curve = p_arr
            c_notes[i].gender_curve = g_arr
            c_notes[i].tension_curve = t_arr
            c_notes[i].breath_curve = b_arr

        try:
            self.lib.execute_render(c_notes, note_count, output_path.encode("utf-8"), 0)
            print(f"🎬 Render finished: {output_path}")
            return True
        except Exception as e:
            print(f"❌ execute_render error: {e}\n{traceback.format_exc()}")
            return False


# ══════════════════════════════════════════════════════════════
# 5. 音声合成マネージャー
# ══════════════════════════════════════════════════════════════

class TalkManager(QObject):
    speak_started  = Signal()
    speak_finished = Signal()
    speak_error    = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.current_voice_path: str | None = None
        self.is_speaking: bool = False

    def set_voice(self, htsvoice_path: str) -> bool:
        if htsvoice_path and os.path.exists(htsvoice_path):
            self.current_voice_path = htsvoice_path
            return True
        print(f"⚠️ Voice path not found: {htsvoice_path}")
        return False

    def speak(self, text: str, speed: float = 1.0) -> None:
        if not text or self.is_speaking:
            return

        self.is_speaking = True
        self.speak_started.emit()

        # [FIX-5] try の前に None で初期化 → finally で None チェックが確実に機能する
        tmp_path: str | None = None

        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp_path = tmp.name

            ok, result = self.synthesize(text, tmp_path, speed=speed)
            if not ok:
                self.speak_error.emit(result)
                return

            audio_data, sample_rate = sf.read(tmp_path, dtype="float32")
            sd.play(audio_data, sample_rate)
            sd.wait()

        except Exception as e:
            msg = f"speak() error: {e}\n{traceback.format_exc()}"
            print(msg)
            self.speak_error.emit(msg)
        finally:
            self.is_speaking = False
            self.speak_finished.emit()
            # [FIX-5] None チェック済みなので os.path.exists() に None が渡らない
            if tmp_path is not None and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def synthesize(self, text: str, output_path: str, speed: float = 1.0) -> tuple[bool, str]:
        if not text:
            return False, "テキストが空です。"

        try:
            output_dir = os.path.dirname(output_path)
            if output_dir:
                os.makedirs(output_dir, exist_ok=True)

            x: np.ndarray | None = None
            sr: int = 48000
            options: dict[str, Any] = {"speed": float(speed)}
            voice = self.current_voice_path or ""

            if voice and os.path.exists(voice):
                x, sr = self._tts_with_voice(text, voice, options)
            else:
                x, sr = self._tts_default(text, options)

            if x is None:
                return False, "音声データの生成に失敗しました。"

            x_arr = np.asarray(x)

            # [FIX-7] 全サンプルが 0 / 空配列の場合でも安全に処理する
            if x_arr.dtype in (np.float32, np.float64):
                abs_max = np.abs(x_arr).max() if x_arr.size > 0 else 0.0
                if abs_max <= 1.0:
                    x_arr = x_arr * 32767.0

            x_int16 = np.clip(x_arr, -32768, 32767).astype(np.int16)
            sf.write(output_path, x_int16, sr)

            if os.path.exists(output_path):
                print(f"✅ Saved: {output_path}")
                return True, output_path

            return False, f"書き出し失敗: {output_path}"

        except Exception as e:
            msg = f"Critical synthesis error: {e}\n{traceback.format_exc()}"
            print(msg)
            return False, msg

    def _tts_with_voice(self, text: str, voice: str, options: dict[str, Any]) -> tuple[np.ndarray | None, int]:
        for key in ("htsvoice", "font"):
            try:
                result = pyopenjtalk.tts(text, **{**options, key: voice})
                if result is None:
                    continue
                if isinstance(result, tuple) and len(result) >= 2:
                    return result[0], int(result[1])
                if isinstance(result, np.ndarray):
                    return result, 48000
            except (TypeError, Exception) as e:
                print(f"DEBUG: '{key}' kwarg failed: {e}")

        print("DEBUG: Falling back to default voice")
        return self._tts_default(text, options)

    @staticmethod
    def _tts_default(text: str, options: dict[str, Any]) -> tuple[np.ndarray | None, int]:
        # [FIX-6] pyopenjtalk.tts() の例外を確実にキャッチしてクラッシュを防ぐ
        try:
            result = pyopenjtalk.tts(text, **options)
            if result is None:
                return None, 48000
            if isinstance(result, tuple) and len(result) >= 2:
                return result[0], int(result[1])
            if isinstance(result, np.ndarray):
                return result, 48000
            return None, 48000
        except Exception as e:
            print(f"[Error] pyopenjtalk.tts() failed: {e}\n{traceback.format_exc()}")
            return None, 48000
