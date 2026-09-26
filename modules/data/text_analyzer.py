# modules/data/text_analyzer.py
"""
VO-SE Vocal — テキスト解析 / 発声タイミング整合

変更点 (vs 旧実装):
  [FIX-1] align_vocal_timing(): VcvResolver を使って前ノート母音を毎ノート正確に決定
  [FIX-2] align_vocal_timing(): OtoParser から先行発声・オーバーラップを取得し
           note.start_time の real_start_sec を正確に計算する
  [FIX-3] align_vocal_timing(): UST 上書き値 (note.pre_utterance, note.overlap) が
           None でない場合はそちらを優先する
  [FIX-4] align_vocal_timing(): ビブラートカーブをフレームタイムラインに注入する
  [FIX-5] _lyric_to_phonemes(): エラー時に "pau" ではなく [] を返すよう変更
  [NEW-1] convert_kanji_to_kana(): pykakasi による漢字→ひらがな自動変換
"""
from __future__ import annotations

import os
import logging
from typing import TYPE_CHECKING,Tuple, Any, Dict, List, Optional

if TYPE_CHECKING:
    pass

import pyopenjtalk

from modules.data.data_models import NoteEvent
from modules.data.oto_parser import OtoParser, OtoEntry
from modules.audio.vcv_resolver import VcvResolver

logger = logging.getLogger(__name__)


class TextAnalyzer:
    """発声タイミング整合・音素解析クラス"""

    # Open JTalk 標準母音
    STANDARD_VOWELS = {"a", "i", "u", "e", "o"}
    # 無声化母音
    VOICELESS_VOWELS = {"A", "I", "U", "E", "O"}
    ALL_VOWELS = STANDARD_VOWELS | VOICELESS_VOWELS
    SPECIAL_PHONEMES = {"N", "cl"}

    def __init__(self, dict_path: Optional[str] = None) -> None:
        self.dict_path: str = dict_path or ""
        if self.dict_path and os.path.exists(self.dict_path):
            set_dic = getattr(pyopenjtalk, "set_dic_path", None)
            if callable(set_dic):
                set_dic(self.dict_path)

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def convert_kanji_to_kana(self, text: str) -> str:
        """
        漢字・カナ混じりテキストをひらがなに変換する。

        pykakasi が未インストールの場合はそのまま返す（起動を妨げない設計）。
        """
        if not text:
            return ""
        try:
            import pykakasi
            kks = pykakasi.kakasi()
            result = kks.convert(text)
            return "".join(str(item.get("hira", "")) for item in result)
        except (ImportError, ModuleNotFoundError):
            logger.debug("pykakasi 未インストール。元のテキストを返します。")
            return text
        except Exception as exc:
            logger.warning("漢字→ひらがな変換エラー: %s", exc)
            return text

    def align_vocal_timing(
        self,
        note_events: List[NoteEvent],
        oto_parser: Optional[OtoParser] = None,
        frame_period_ms: float = 5.0,
    ) -> Tuple[List[NoteEvent], List[Dict[str, Any]]]:
        """
        【完全版】各ノートに対し:
          1. VCV 連音エイリアスを解決する
          2. Oto.ini から先行発声・オーバーラップを取得し、
             UST 上書き値がある場合はそちらを優先する
          3. 実際の発声開始時刻 (real_start_sec) を note に書き戻す
          4. 5ms フレーム単位のタイムライン配列を構築し返す

        Args:
            note_events:     NoteEvent のリスト
            oto_parser:      OtoParser インスタンス (None なら VCV・先行発声無効)
            frame_period_ms: フレーム周期 (ms)

        Returns:
            (更新済み NoteEvent リスト, フレームタイムライン辞書リスト)
        """
        if not note_events:
            return [], []

        # --- フォールバック定数 (秒) ---
        DEFAULT_PREUTTERANCE  = 0.05   # 50 ms
        DEFAULT_OVERLAP       = 0.02   # 20 ms
        DEFAULT_CONSONANT_DUR = 0.05
        RELEASE_DURATION      = 0.03

        frame_period_sec = frame_period_ms / 1000.0

        # --- VCV リゾルバー初期化 ---
        vcv_resolver: Optional[VcvResolver] = None
        if oto_parser is not None:
            vcv_resolver = VcvResolver(oto_parser, use_g2p=True)

        global_frame_timeline: List[Dict[str, Any]] = []

        for i, note in enumerate(note_events):
            if note is None:
                continue

            # 1. 歌詞 → 音素
            phonemes = self._lyric_to_phonemes(note.lyric)
            note.phonemes = phonemes

            if not phonemes or "pau" in phonemes:
                note.has_analysis = True
                continue

            # 2. VCV 解決 → 先行発声・オーバーラップ取得
            prev_lyric = note_events[i - 1].lyric if i > 0 else None

            oto_entry: Optional[OtoEntry] = None
            resolved_alias: str = note.lyric

            if vcv_resolver is not None:
                r = vcv_resolver.resolve_note(note.lyric, prev_lyric)
                resolved_alias = r[0]
                oto_entry: Optional[OtoEntry] = r[1] 

            # 3. 先行発声・オーバーラップ決定 (UST 上書き > oto.ini > デフォルト)
            if getattr(note, "pre_utterance", None) is not None and note.pre_utterance > 0:
                # UST の PreUtterance= 上書き値 (ms) を秒に変換
                preutterance_sec = note.pre_utterance / 1000.0
            elif oto_entry is not None:
                preutterance_sec = oto_entry.preutterance_sec
            else:
                preutterance_sec = DEFAULT_PREUTTERANCE

            if getattr(note, "overlap", None) is not None and note.overlap > 0:
                overlap_sec = note.overlap / 1000.0
            elif oto_entry is not None:
                overlap_sec = oto_entry.overlap_sec
            else:
                overlap_sec = DEFAULT_OVERLAP

            # 4. 実際の発声開始・オーバーラップ開始時刻
            vocal_start_sec   = note.start_time - preutterance_sec
            overlap_start_sec = max(0.0, note.start_time - overlap_sec)

            # NoteEvent に書き戻す (C++ エンジンが参照)
            note.pre_utterance = preutterance_sec * 1000.0  # ms で保存
            note.overlap       = overlap_sec * 1000.0        # ms で保存
            # onset = WAV 上で音が実際に始まる時刻
            note.onset = vocal_start_sec

            # 5. 子音 / 母音の分類
            vowels     = [p for p in phonemes if p.lower() in self.ALL_VOWELS]
            consonants = [p for p in phonemes
                          if p not in self.ALL_VOWELS and p not in self.SPECIAL_PHONEMES]

            # Oto.ini の子音固定範囲がある場合はそれを使う
            consonant_total_sec = DEFAULT_CONSONANT_DUR
            if oto_entry is not None and oto_entry.fixed_range_sec > 0:
                consonant_total_sec = oto_entry.fixed_range_sec

            note_end_sec = note.start_time + note.duration

            # 6. ビブラートカーブの組み立て
            vibrato_depth = float(getattr(note, "vibrato_depth", 0.0))
            vibrato_rate  = float(getattr(note, "vibrato_rate",  5.5))

            # 7. フレームタイムラインの生成
            current_time = vocal_start_sec
            while current_time < note_end_sec + RELEASE_DURATION:
                rel = current_time - vocal_start_sec

                # 音素の選択
                if current_time < note.start_time:
                    # 先行発声フェーズ（子音）
                    if consonants:
                        c_idx = int(rel / max(0.001, consonant_total_sec / len(consonants)))
                        current_phoneme = consonants[min(c_idx, len(consonants) - 1)]
                    else:
                        current_phoneme = vowels[0] if vowels else "a"
                elif current_time < note_end_sec:
                    # 母音持続フェーズ
                    current_phoneme = vowels[0] if vowels else "a"
                else:
                    # リリースフェーズ
                    current_phoneme = vowels[-1] if vowels else "a"

                # 特殊音素 (ん / っ)
                if "N" in phonemes:
                    current_phoneme = "N"
                elif "cl" in phonemes and current_time >= note_end_sec - 0.02:
                    current_phoneme = "cl"

                # オーバーラップ重み
                overlap_weight = 0.0
                if current_time >= overlap_start_sec and overlap_sec > 0:
                    overlap_weight = min(1.0, (current_time - overlap_start_sec) / overlap_sec)

                # ビブラート (発声開始から note.start_time を過ぎた部分に適用)
                pitch_offset = 0.0
                if vibrato_depth > 0 and current_time >= note.start_time:
                    import math
                    vib_rel = current_time - note.start_time
                    pitch_offset = math.sin(2 * math.pi * vibrato_rate * vib_rel) * vibrato_depth

                global_frame_timeline.append({
                    "time":            current_time,
                    "phoneme":         current_phoneme,
                    "weight":          1.0 - overlap_weight,
                    "note_index":      i,
                    "pitch_offset":    pitch_offset,   # semitone
                    "resolved_alias":  resolved_alias,
                    "wav_path":        oto_entry.wav_path if oto_entry else "",
                })

                current_time += frame_period_sec

            note.has_analysis = True

        return note_events, global_frame_timeline

    # ------------------------------------------------------------------
    # 内部ユーティリティ
    # ------------------------------------------------------------------

    def _lyric_to_phonemes(self, lyric: str) -> List[str]:
        """歌詞から音素リストを返す"""
        if not lyric or not lyric.strip():
            return ["pau"]
        try:
            raw = pyopenjtalk.g2p(lyric, kana=False)
            if raw:
                return [p for p in raw.split() if p not in ("sil", "pau")]
        except Exception as exc:
            logger.warning("[TextAnalyzer] g2p 変換失敗 '%s': %s", lyric, exc)
        return ["pau"]

    def midi_to_hz(self, midi_note: int) -> float:
        """MIDI ノート番号 → 周波数 (Hz)"""
        if midi_note is None:
            return 0.0
        return float(440.0 * (2.0 ** ((float(midi_note) - 69.0) / 12.0)))
