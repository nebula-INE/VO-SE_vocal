# modules/audio/vcv_resolver.py
"""
VO-SE Vocal — VCV 連音解決エンジン

役割:
  - 前ノートの末尾母音を判定し、"a い" 形式の VCV エイリアスを解決する
  - CV / 単独音 / 無声化母音 のフォールバックチェーンを持つ
  - OtoParser と協調して OtoEntry を返す

クラス:
  VowelClassifier  : 歌詞文字 → 末尾母音ラベル ("a"/"i"/"u"/"e"/"o"/"n"/"") の変換
  VcvResolver      : ノート列 → (alias, OtoEntry) リストの解決

[NEW-1] VO-SE は従来 resolve_target_wav() で vowel_groups を 2 箇所に重複定義していた。
        本モジュールに一元化し、main_window.py / vo_se_engine.py 双方から参照する。
[NEW-2] 無声化母音 (A/I/U/E/O) や促音 "っ" / 撥音 "ん" を正確にマッピング。
[NEW-3] pyopenjtalk が使える環境では g2p による末尾音素推定を優先する。
"""
from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 母音グループ定義 (ひらがな・カタカナ・一部漢字読み仮名を網羅)
# ---------------------------------------------------------------------------
_VOWEL_MAP: Dict[str, str] = {}

def _build_vowel_map() -> Dict[str, str]:
    """文字 → 末尾母音ラベルの辞書を構築する"""
    groups: Dict[str, str] = {
        "a": (
            "あかさたなはまやらわがざだばぱぁゃ"
            "アカサタナハマヤラワガザダバパァャ"
        ),
        "i": (
            "いきしちにひみりぎじぢびぴぃ"
            "イキシチニヒミリギジヂビピィ"
        ),
        "u": (
            "うくすつぬふむゆるぐずづぶぷぅゅ"
            "ウクスツヌフムユルグズヅブプゥュ"
        ),
        "e": (
            "えけせてねへめれげぜでべぺぇ"
            "エケセテネヘメレゲゼデベペェ"
        ),
        "o": (
            "おこそとのほもよろをごぞどぼぽぉょ"
            "オコソトノホモヨロヲゴゾドボポォョ"
        ),
        "n": "んン",
    }
    result: Dict[str, str] = {}
    for label, chars in groups.items():
        for ch in chars:
            result[ch] = label
    return result

_VOWEL_MAP = _build_vowel_map()

# 促音 "っ/ッ" は直前の母音を引き継ぐ（ここでは空文字→フォールバック扱い）
_SILENT_CHARS = set("っッ")


class VowelClassifier:
    """
    歌詞文字列から末尾母音ラベルを判定するクラス。

    - pyopenjtalk が利用可能な場合は g2p を優先する
    - ない場合は _VOWEL_MAP による文字ベース判定にフォールバック
    """

    def __init__(self, use_g2p: bool = True) -> None:
        self._use_g2p = use_g2p
        self._g2p_available = False

        if use_g2p:
            try:
                import pyopenjtalk as _ojt  # noqa: F401
                self._g2p_available = True
            except Exception as exc:
                # pyopenjtalk は Cython/NumPy ABI に依存するため、
                # import 時の ValueError 等も「G2P 利用不可」として扱う。
                # VCV 解決自体は文字ベースの母音判定で継続できる。
                logger.warning(
                    "pyopenjtalk が利用不可。文字ベースの母音判定に切り替えます: %s",
                    exc,
                )

    def trailing_vowel(self, lyric: str) -> Optional[str]:
        """
        歌詞の末尾音素を母音ラベルに変換して返す。

        Returns:
            "a" / "i" / "u" / "e" / "o" / "n"  or  None (判定不能)
        """
        if not lyric:
            return None

        # クリーニング: ピッチサフィックス ("か_A3", "かC4", "か1") や VCV プレフィックス ("a か", "- か") を取り除く
        clean = re.sub(r'_?[A-Ga-g][#b]?[0-9]$', '', lyric).strip()
        clean = re.sub(r'_[0-9]$', '', clean).strip()
        clean = re.sub(r'^[-aieuon_]\s*', '', clean, flags=re.IGNORECASE).strip() or clean

        if not clean:
            return None

        # ローマ字末尾判定
        if re.match(r'^[a-zA-Z\s_-]+$', clean):
            c_low = clean.replace('_', '').replace('-', '').strip().lower()
            for v in ('a', 'i', 'u', 'e', 'o', 'n'):
                if c_low.endswith(v):
                    return v

        # --- g2p 優先パス ---
        if self._g2p_available:
            try:
                import pyopenjtalk
                raw: str = pyopenjtalk.g2p(clean, kana=False)
                phonemes = [p for p in raw.split() if p not in ("sil", "pau")]
                if phonemes:
                    last = phonemes[-1].lower()
                    # WORLD/Open JTalk の無声化母音 A→a, I→i, U→u, E→e, O→o
                    normalized = last[0] if last[0] in "aiueon" else None
                    if normalized:
                        return normalized
            except Exception as exc:
                logger.debug("g2p 判定失敗 (%s): %s", clean, exc)

        # --- 文字ベースフォールバック ---
        for ch in reversed(clean):
            if ch in _SILENT_CHARS or ch in ("ー", "っ", "ッ"):
                continue  # 促音・長音はスキップして一つ前を見る
            label = _VOWEL_MAP.get(ch)
            if label:
                return label

        return None


# ---------------------------------------------------------------------------
# VcvResolver
# ---------------------------------------------------------------------------

class VcvResolver:
    """
    ノート列に対して、OtoParser を参照しながら
    最適な alias と OtoEntry を解決する。

    使い方:
        from modules.data.oto_parser import OtoParser
        from modules.audio.vcv_resolver import VcvResolver

        oto = OtoParser()
        oto.load_voice_dir("/path/to/voice")

        resolver = VcvResolver(oto)
        resolved = resolver.resolve(notes_list)
        # resolved: List[ResolvedNote]
    """

    def __init__(self, oto_parser, use_g2p: bool = True) -> None:
        """
        Args:
            oto_parser: OtoParser インスタンス
            use_g2p:    True なら pyopenjtalk g2p を母音判定に使う
        """
        self._oto = oto_parser
        self._classifier = VowelClassifier(use_g2p=use_g2p)
        self._has_vcv = oto_parser.has_vcv() if hasattr(oto_parser, "has_vcv") else False

    def resolve_note(
        self,
        lyric: str,
        prev_lyric: Optional[str],
    ) -> Tuple[str, object]:
        """
        1 ノート分のエイリアスと OtoEntry を解決する。

        Args:
            lyric:      現在ノートの歌詞
            prev_lyric: 前ノートの歌詞（先頭ノートなら None）

        Returns:
            (resolved_alias, OtoEntry or None)
        """
        prev_vowel: Optional[str] = None
        if prev_lyric and self._has_vcv:
            prev_vowel = self._classifier.trailing_vowel(prev_lyric)

        entry = self._oto.resolve_alias(lyric, prev_vowel)
        if entry is not None:
            return (entry.alias, entry)

        # エントリが全く見つからない場合は lyric をそのまま alias として返す
        return (lyric, None)

    def resolve(self, notes: Sequence) -> List["ResolvedNote"]:
        """
        ノートのシーケンス全体を解決する。

        Args:
            notes: NoteEvent 互換オブジェクトのリスト（.lyric 属性を持つこと）

        Returns:
            ResolvedNote のリスト
        """
        results: List[ResolvedNote] = []
        prev_lyric: Optional[str] = None

        for note in notes:
            lyric: str = getattr(note, "lyric", "") or getattr(note, "lyrics", "") or ""
            alias, entry = self.resolve_note(lyric, prev_lyric)
            results.append(ResolvedNote(note=note, alias=alias, oto_entry=entry))
            prev_lyric = lyric

        return results


class ResolvedNote:
    """
    VcvResolver.resolve() の返り値。元ノート + 解決済み alias + OtoEntry をバンドル。
    """

    def __init__(self, note, alias: str, oto_entry) -> None:
        self.note = note
        self.alias = alias
        self.oto_entry = oto_entry  # OtoEntry or None

    # --- ショートカット ---
    @property
    def wav_path(self) -> Optional[str]:
        return self.oto_entry.wav_path if self.oto_entry else None

    @property
    def preutterance_sec(self) -> float:
        return self.oto_entry.preutterance_sec if self.oto_entry else 0.05

    @property
    def overlap_sec(self) -> float:
        return self.oto_entry.overlap_sec if self.oto_entry else 0.02

    @property
    def fixed_range_sec(self) -> float:
        return self.oto_entry.fixed_range_sec if self.oto_entry else 0.0

    def __repr__(self) -> str:
        return (
            f"ResolvedNote(lyric={getattr(self.note, 'lyric', '?')!r}, "
            f"alias={self.alias!r}, "
            f"pre={self.preutterance_sec:.3f}s, "
            f"ov={self.overlap_sec:.3f}s)"
        )
