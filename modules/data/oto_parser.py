# modules/data/oto_parser.py
"""
VO-SE Vocal — oto.ini 完全パーサー
変更点:
  [NEW-1] OtoEntry dataclass: 先行発声・オーバーラップ・子音固定範囲・左右ブランクを全フィールドとして保持
  [NEW-2] OtoParser.load_oto_file(): Shift-JIS/UTF-8 自動判別、サブフォルダ再帰対応
  [NEW-3] OtoParser.get(): alias の完全一致 → 末尾母音一致フォールバック
  [NEW-4] OtoParser.get_preutterance_sec() / get_overlap_sec(): ms → sec 変換ショートカット
  [NEW-5] OtoParser.resolve_alias(): VCV ("a い") → CV ("い") への段階的フォールバック
"""
from __future__ import annotations

import codecs
import gc
import logging
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from modules.data.prefix_map import (
    PrefixMapEntry,
    map_alias,
    note_name_to_midi,
    parse_prefix_map,
)
from typing import Callable, Dict, List, Optional, Tuple, TypedDict, cast

logger = logging.getLogger(__name__)

_PITCH_SUFFIX_RE = re.compile(r"(?:_?[A-Ga-g][#b]?-?\d+)$", re.IGNORECASE)
_PITCH_TAG_RE = re.compile(
    r"(?:^|[_\s/\\.\-])([A-Ga-g])([#b]?)(-?\d+)(?=$|[_\s/\\.\-])"
)
_PITCH_SUFFIX_TAG_RE = re.compile(r"([A-Ga-g])([#b]?)(-?\d+)$", re.IGNORECASE)

_KANA_TO_ROMAJI: Dict[str, str] = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "を": "wo", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "di", "づ": "du", "で": "de", "ど": "do",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
}
_ROMAJI_TO_KANA: Dict[str, str] = {
    romaji: kana for kana, romaji in _KANA_TO_ROMAJI.items()
}


def _find_prefix_map_path(voice_dir: str) -> Optional[str]:
    """Find the root-level prefix.map regardless of filename casing."""
    try:
        candidates = sorted(
            (
                name for name in os.listdir(voice_dir)
                if name.casefold() == "prefix.map"
                and os.path.isfile(os.path.join(voice_dir, name))
            ),
            key=lambda name: (name.casefold(), name),
        )
    except OSError:
        return None

    if len(candidates) > 1:
        logger.warning(
            "大文字小文字違いの prefix.map が複数あります。%s を使用します: %s",
            candidates[0],
            voice_dir,
        )
    return os.path.join(voice_dir, candidates[0]) if candidates else None


def _extract_pitch_midi(*values: str) -> Optional[int]:
    for value in values:
        text = str(value or "")
        match = _PITCH_TAG_RE.search(text) or _PITCH_SUFFIX_TAG_RE.search(text)
        if match:
            return note_name_to_midi(
                f"{match.group(1)}{match.group(2)}{match.group(3)}"
            )
    return None


# System RAM check helper for 8GB or lower RAM environments
def get_system_ram_gb() -> float:
    try:
        sysconf_obj = getattr(os, "sysconf", None)
        if not callable(sysconf_obj):
            return 8.0
        sysconf = cast(Callable[[str], int], sysconf_obj)
        page_size = sysconf("SC_PAGE_SIZE")
        pages = sysconf("SC_PHYS_PAGES")
        return (pages * page_size) / (1024 ** 3)
    except (AttributeError, OSError, ValueError, TypeError):
        return 8.0

IS_LOW_RAM = get_system_ram_gb() <= 8.5

# The cache has a separate optional-read budget because it is a disposable
# optimization; an oversized cache is skipped and the source oto.ini is still
# streamed normally. Source files and valid voicebank layers have no fixed cap.
MAX_OTO_CACHE_BYTES = 16 * 1024 * 1024 if IS_LOW_RAM else 64 * 1024 * 1024


class VoicebankResourceLimitError(RuntimeError):
    """Raised when a voicebank exceeds safe parsing/resource limits."""


class VoicebankLoadCancelled(RuntimeError):
    """Raised when a caller cancels a voicebank scan or parse."""


def clear_voice_dir_file_map_cache() -> None:
    """Compatibility hook; WAV lookups no longer retain whole-tree file maps."""
    gc.collect()


def _find_unique_voicebank_file_by_name(
    roots: Tuple[str, ...],
    basename: str,
    is_allowed_path: Callable[[str], bool],
) -> Tuple[Optional[str], bool]:
    """Find a unique file with constant memory; stop immediately on ambiguity."""
    matches: set[str] = set()
    target = basename.casefold()

    for root in roots:
        if not os.path.isdir(root):
            continue
        pending = [root]
        while pending:
            current = pending.pop()
            try:
                with os.scandir(current) as iterator:
                    subdirectories = []
                    for item in iterator:
                        try:
                            if item.is_symlink():
                                continue
                            if item.is_dir(follow_symlinks=False):
                                subdirectories.append(item.path)
                            elif (
                                item.is_file(follow_symlinks=False)
                                and item.name.casefold() == target
                                and is_allowed_path(item.path)
                            ):
                                matches.add(os.path.abspath(item.path))
                                if len(matches) > 1:
                                    return None, True
                        except OSError:
                            continue
                pending.extend(sorted(subdirectories, reverse=True))
            except OSError:
                continue

    if len(matches) == 1:
        return next(iter(matches)), True
    return None, True


@dataclass
class OtoEntry:
    """oto.ini の 1 エントリを表すデータクラス。単位はすべてミリ秒 (ms)。"""
    alias: str              # エイリアス名 (例: "a い", "- い", "い")
    filename: str           # 対応 WAV ファイル名
    voice_dir: str          # この oto.ini が置かれているフォルダの絶対パス

    left_blank: float       # 左ブランク (ms)  : WAV 先頭からの読み飛ばし量
    fixed_range: float      # 子音固定範囲 (ms) : ストレッチされない先頭部分
    right_blank: float      # 右ブランク (ms)  : WAV 末尾からの読み飛ばし量（負値可）
    preutterance: float     # 先行発声 (ms)    : ノート開始時刻より「先」に発声を始める量
    overlap: float          # オーバーラップ (ms): 前のノートとフェードでクロスする量
    search_root: Optional[str] = None  # WAV basename fallback の検索境界
    search_roots: Tuple[str, ...] = ()  # 対応する本体/補助音源だけを含む検索境界
    layer_priority: int = 0  # main / supplement の読み込み優先順位

    @property
    def wav_path(self) -> str:
        """フルパスで WAV へのパスを返す (区切り文字・大文字小文字差を吸収)。"""
        normalized_filename = self.filename.replace("\\", os.sep).replace("/", os.sep)
        default_root = os.path.abspath(
            os.path.expanduser(self.search_root or self.voice_dir)
        )
        search_roots = tuple(
            dict.fromkeys(
                os.path.abspath(os.path.expanduser(root))
                for root in (self.search_roots or (default_root,))
            )
        )

        def is_allowed_path(candidate: str) -> bool:
            """既存ファイル・未作成パスともに、実体が許可ルート内か確認する。"""
            try:
                real_candidate = os.path.realpath(candidate)
                for root in search_roots:
                    real_root = os.path.realpath(root)
                    common = os.path.commonpath((real_root, real_candidate))
                    if os.path.normcase(common) == os.path.normcase(real_root):
                        return True
            except (OSError, ValueError):
                return False
            return False

        if os.path.isabs(normalized_filename):
            exact_path = os.path.normpath(normalized_filename)
        else:
            exact_path = os.path.normpath(
                os.path.join(self.voice_dir, normalized_filename)
            )

        # 先に実ファイルシステム上の実際の名前へ解決する。
        # macOS/Windows の case-insensitive filesystem では
        # os.path.isfile(exact_path) が表記違いでも True になるため、
        # これを先に行わないと SubVoice が subvoice のまま返る。
        if not os.path.isabs(normalized_filename):
            current = os.path.abspath(self.voice_dir)
            components = [
                part
                for part in normalized_filename.replace("\\", "/").split("/")
                if part not in ("", ".")
            ]
            for component in components:
                if component == "..":
                    current = os.path.dirname(current)
                    continue
                try:
                    entries = os.listdir(current)
                except OSError:
                    break
                matched = next(
                    (name for name in entries if name.casefold() == component.casefold()),
                    None,
                )
                if matched is None:
                    break
                current = os.path.join(current, matched)
            else:
                if os.path.isfile(current) and is_allowed_path(current):
                    return current

        if os.path.isfile(exact_path) and is_allowed_path(exact_path):
            return exact_path

        # Basename fallback is streamed with a strict scan budget. Never build a
        # dictionary containing every file in a large voicebank.
        target_lower = (
            normalized_filename.replace(os.sep, "/").replace("\\", "/").lower()
        )
        target_basename = target_lower.rsplit("/", 1)[-1]
        basenames = [target_basename]
        if not target_basename.endswith(".wav"):
            basenames.append(target_basename + ".wav")
        for basename in basenames:
            resolved, complete = _find_unique_voicebank_file_by_name(
                search_roots,
                basename,
                is_allowed_path,
            )
            if resolved:
                logger.debug(
                    "OTO WAV basename fallback: %s -> %s (roots=%s)",
                    self.filename,
                    resolved,
                    search_roots,
                )
                return resolved
            if not complete:
                logger.warning(
                    "WAV basename fallback scan limit reached; unresolved safely: %s",
                    self.filename,
                )
                break

        # ファイルがまだ存在しない場合でも、許可ルート内のパスだけを返す。
        if is_allowed_path(exact_path):
            return exact_path
        safe_basename = os.path.basename(normalized_filename.rstrip("/\\"))
        if not safe_basename or safe_basename in (".", ".."):
            safe_basename = "unresolved.wav"
        return os.path.join(self.voice_dir, safe_basename)

    @property
    def preutterance_sec(self) -> float:
        """先行発声を秒単位で返す"""
        return self.preutterance / 1000.0

    @property
    def overlap_sec(self) -> float:
        """オーバーラップを秒単位で返す"""
        return self.overlap / 1000.0

    @property
    def fixed_range_sec(self) -> float:
        """子音固定範囲を秒単位で返す"""
        return self.fixed_range / 1000.0

    @property
    def left_blank_sec(self) -> float:
        """左ブランクを秒単位で返す"""
        return self.left_blank / 1000.0


class _VoiceLayerInfo(TypedDict):
    directory: str
    ini_path: str
    root: str
    coverage: float
    covered: set[str]
    supplement: bool
    supplement_reason: str
    selected: bool
    selected_subtree: bool
    relative_depth: int


class OtoParser:
    """
    oto.ini をロード・検索する統合パーサー。

    使い方:
        parser = OtoParser()
        parser.load_oto_file("/path/to/voice/oto.ini")
        entry = parser.get("a い")   # OtoEntry or None
    """

    def __init__(self) -> None:
        # alias → OtoEntry の辞書（読み込み順で first-wins）
        self._db: Dict[str, OtoEntry] = {}
        self._normalized_db: Dict[str, OtoEntry] = {}
        # Keep pitch-tagged duplicates for note-aware selection while the public
        # alias dictionary remains first-wins for compatibility/native metadata.
        self._alias_variants: Dict[str, List[Tuple[int, OtoEntry]]] = {}
        self._prefix_map: List[PrefixMapEntry] = []
        self.loaded_layers: Tuple[str, ...] = ()
        self.loaded_oto_ini_paths: Tuple[str, ...] = ()
        self.layer_info: List[_VoiceLayerInfo] = []
        self.layer_notifications: List[str] = []
        self._unresolved_reported: set[Tuple[str, str, Optional[int]]] = set()

    @staticmethod
    def _is_within(path: str, root: str) -> bool:
        try:
            return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)
        except (OSError, ValueError):
            return False

    @staticmethod
    def _contains_root_marker(directory: str) -> bool:
        try:
            names = {name.casefold() for name in os.listdir(directory)}
        except OSError:
            return False
        return any(marker in names for marker in ("character.txt", "readme.txt"))

    @staticmethod
    def _strip_supplement_suffix(name: str) -> str:
        normalized = unicodedata.normalize("NFKC", str(name)).strip()
        return re.sub(
            r"[ ._-]*(?:エクストラ|extra|append)$",
            "",
            normalized,
            flags=re.IGNORECASE,
        ).strip() or normalized

    @classmethod
    def _has_oto_layer(cls, directory: str) -> bool:
        if not os.path.isdir(directory) or os.path.islink(directory):
            return False
        for current, dirs, files in os.walk(directory, topdown=True, followlinks=False):
            dirs[:] = sorted(
                name for name in dirs
                if not os.path.islink(os.path.join(current, name))
            )
            if any(name.casefold() == "oto.ini" for name in files):
                return True
        return False

    @classmethod
    def _find_companion_voice_roots(
        cls,
        root: str,
        voices_root: Optional[str],
    ) -> List[str]:
        """Find only exact name-linked main/Extra siblings directly under voices/."""
        if not voices_root:
            return []
        root_path = os.path.realpath(root)
        voices_path = os.path.realpath(voices_root)
        if os.path.normcase(os.path.dirname(root_path)) != os.path.normcase(voices_path):
            return []

        root_name = os.path.basename(root_path)
        root_base = cls._strip_supplement_suffix(root_name)
        normalized_root_name = unicodedata.normalize("NFKC", root_name).casefold()
        is_supplement = root_base.casefold() != normalized_root_name
        try:
            siblings = sorted(
                os.listdir(voices_path),
                key=lambda value: (value.casefold(), value),
            )
        except OSError:
            return []

        candidates: List[str] = []
        if is_supplement:
            # Selecting 重音テトエクストラ pairs only with an exact 重音テト sibling.
            for sibling in siblings:
                if unicodedata.normalize("NFKC", sibling).casefold() == root_base.casefold():
                    candidates.append(os.path.join(voices_path, sibling))
                    break
        else:
            # Selecting 重音テト also makes its explicitly named Extra sibling available.
            for sibling in siblings:
                if sibling == root_name:
                    continue
                sibling_base = cls._strip_supplement_suffix(sibling)
                normalized_sibling = unicodedata.normalize("NFKC", sibling).casefold()
                if (
                    sibling_base.casefold() == normalized_root_name
                    and sibling_base.casefold() != normalized_sibling
                ):
                    candidates.append(os.path.join(voices_path, sibling))

        return [
            candidate for candidate in candidates
            if cls._has_oto_layer(candidate)
        ]

    @staticmethod
    def _infer_voices_dir(start: str, voices_dir: Optional[str] = None) -> Optional[str]:
        if voices_dir:
            candidate = os.path.abspath(os.path.expanduser(voices_dir))
            return candidate if os.path.isdir(candidate) else None
        current = os.path.abspath(start)
        while True:
            if os.path.basename(current).casefold() in {"voices", "voice_banks"} and os.path.isdir(current):
                return current
            parent = os.path.dirname(current)
            if parent == current:
                return None
            current = parent

    @classmethod
    def find_voice_root(
        cls,
        start: str,
        voices_dir: Optional[str] = None,
        max_up: Optional[int] = None,
    ) -> str:
        """Find the extracted bank root without a default parent-depth ceiling.

        Search upward for UTAU root markers, stopping at the configured voices
        directory when one is known. Callers may still pass max_up explicitly
        when they need a narrower search. Without a recognizable voices
        directory, the selected directory remains the fallback boundary.
        """
        selected = os.path.abspath(os.path.expanduser(start))
        voices_root = cls._infer_voices_dir(selected, voices_dir)
        current = selected
        depth = 0
        while True:
            if cls._contains_root_marker(current):
                return current
            if voices_root and os.path.normcase(current) == os.path.normcase(voices_root):
                break
            if voices_root and not cls._is_within(current, voices_root):
                break
            if max_up is not None and depth >= max(0, int(max_up)):
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent
            depth += 1

        if voices_root and cls._is_within(selected, voices_root):
            relative = os.path.relpath(selected, voices_root)
            if relative in ("", "."):
                return voices_root
            first_child = relative.split(os.sep, 1)[0]
            return os.path.join(voices_root, first_child)
        return selected

    @staticmethod
    def _layer_aliases_and_coverage(ini_path: str) -> Tuple[set[str], float]:
        """Return CV coverage while streaming the source oto.ini."""
        hiragana_cv = set(
            "あいうえお"
            "かきくけこさしすせそたちつてとなにぬねの"
            "はひふへほまみむめもやゆよらりるれろわをん"
            "がぎぐげござじずぜぞだぢづでどばびぶべぼ"
            "ぱぴぷぺぽぁぃぅぇぉゃゅょゔっ"
        )
        covered: set[str] = set()
        if not os.path.isfile(ini_path):
            return covered, 0.0
        encoding = OtoParser._detect_text_encoding(ini_path)
        try:
            with open(ini_path, "r", encoding=encoding, errors="strict", newline=None) as source:
                for raw_line in source:
                    if "=" not in raw_line:
                        continue
                    filename, params = raw_line.split("=", 1)
                    fields = params.split(",")
                    alias = (
                        fields[0].strip()
                        if fields and fields[0].strip()
                        else os.path.splitext(filename.strip())[0]
                    )
                    alias = unicodedata.normalize("NFKC", alias)
                    alias = re.sub(
                        r"^(?:[-_]\s*|[aieuon](?:\s+|_))",
                        "",
                        alias,
                        flags=re.IGNORECASE,
                    )
                    alias = re.sub(r"_?[A-Ga-g][#b]?-?\d+$", "", alias).strip()
                    if "/" in alias or "\\" in alias:
                        alias = re.split(r"[/\\]", alias)[-1]
                    tokens = alias.split()
                    mora = tokens[-1] if tokens else alias
                    if "ァ" <= mora <= "ヶ":
                        mora = "".join(
                            chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char
                            for char in mora
                        )
                    if mora not in hiragana_cv:
                        mora = _ROMAJI_TO_KANA.get(mora.casefold(), mora)
                    if mora in hiragana_cv:
                        covered.add(mora)
        except MemoryError as exc:
            raise VoicebankResourceLimitError(
                f"oto.ini の音源レイヤー解析中にメモリ不足が発生しました: {ini_path}"
            ) from exc

        return covered, len(covered) / max(len(hiragana_cv), 1)

    @staticmethod
    def _is_supplement_layer(directory: str, root: str, coverage: float) -> Tuple[bool, str]:
        relative = os.path.relpath(directory, root)
        parts = [os.path.basename(root).casefold()] + [
            part.casefold() for part in relative.split(os.sep) if part not in ("", ".")
        ]
        marked = any(
            token in part
            for part in parts
            for token in ("エクストラ", "extra", "append")
        )
        if marked:
            return True, "name"
        if coverage < 0.55:
            return True, "low_cv_coverage"
        return False, ""

    def collect_voice_layers(
        self,
        selected: str,
        voices_dir: Optional[str] = None,
        max_up: int = 3,
        cancel_event: Optional[threading.Event] = None,
    ) -> Tuple[List[str], str, List[_VoiceLayerInfo]]:
        """Collect bounded oto.ini layers and order main banks before supplements."""
        selected_path = os.path.abspath(os.path.expanduser(selected))
        if not os.path.isdir(selected_path):
            return [], selected_path, []

        root = self.find_voice_root(selected_path, voices_dir=voices_dir, max_up=max_up)
        voices_root = self._infer_voices_dir(selected_path, voices_dir)
        root_specs = [root]
        root_specs.extend(
            companion
            for companion in self._find_companion_voice_roots(root, voices_root)
            if os.path.normcase(os.path.realpath(companion))
            != os.path.normcase(os.path.realpath(root))
        )

        found: List[Tuple[str, str, str]] = []
        seen_roots: set[str] = set()
        for layer_root in root_specs:
            root_key = os.path.normcase(os.path.realpath(layer_root))
            if root_key in seen_roots:
                continue
            seen_roots.add(root_key)
            for current, dirs, files in os.walk(layer_root, topdown=True, followlinks=False):
                if cancel_event is not None and cancel_event.is_set():
                    raise VoicebankLoadCancelled("音源フォルダのスキャンはキャンセルされました。")
                dirs[:] = sorted(
                    directory for directory in dirs
                    if not os.path.islink(os.path.join(current, directory))
                )
                oto_names = sorted(name for name in files if name.casefold() == "oto.ini")
                if oto_names:
                    ini_path = os.path.join(current, oto_names[0])
                    if not os.path.islink(ini_path):
                        found.append((os.path.abspath(current), ini_path, layer_root))

        selected_has_layer = any(
            os.path.normcase(os.path.realpath(directory))
            == os.path.normcase(os.path.realpath(selected_path))
            for directory, _ini_path, _layer_root in found
        )
        info: List[_VoiceLayerInfo] = []
        for directory, ini_path, layer_root in found:
            covered, coverage = self._layer_aliases_and_coverage(ini_path)
            supplement, reason = self._is_supplement_layer(directory, layer_root, coverage)
            selected_subtree = (
                os.path.normcase(os.path.realpath(directory))
                == os.path.normcase(os.path.realpath(selected_path))
                or (not selected_has_layer and self._is_within(directory, selected_path))
            )
            exact_selected = (
                os.path.normcase(os.path.realpath(directory))
                == os.path.normcase(os.path.realpath(selected_path))
            )
            relative_depth = len(os.path.relpath(directory, layer_root).split(os.sep))
            info.append({
                "directory": directory,
                "ini_path": ini_path,
                "root": os.path.abspath(layer_root),
                "coverage": coverage,
                "covered": covered,
                "supplement": supplement,
                "supplement_reason": reason,
                "selected": exact_selected,
                "selected_subtree": selected_subtree,
                "relative_depth": relative_depth,
            })

        def priority(item: _VoiceLayerInfo) -> Tuple[int, int, str]:
            selected_rank = 0 if item["selected"] else 1 if item["selected_subtree"] else 2
            return (
                selected_rank,
                int(item["relative_depth"]),
                str(item["directory"]).casefold(),
            )

        main_layers = sorted((item for item in info if not item["supplement"]), key=priority)
        supplement_layers = sorted((item for item in info if item["supplement"]), key=priority)
        ordered_info = main_layers + supplement_layers
        layers = [str(item["directory"]) for item in ordered_info]

        selected_items = [item for item in info if item["selected_subtree"]]
        selected_covered: set[str] = set()
        for item in selected_items:
            selected_covered.update(item["covered"])
        cv_universe = set(
            "あいうえおかきくけこさしすせそたちつてとなにぬねの"
            "はひふへほまみむめもやゆよらりるれろわをん"
            "がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽ"
            "ぁぃぅぇぉゃゅょゔっ"
        )
        selected_coverage = len(selected_covered) / max(len(cv_universe), 1)
        sibling_main = [
            item for item in main_layers
            if not item["selected_subtree"] and float(item["coverage"]) >= 0.55
        ]
        selected_is_supplement = any(
            bool(item["supplement"]) and bool(item["selected"])
            for item in selected_items
        )
        if selected_items and (selected_coverage < 0.55 or selected_is_supplement) and sibling_main:
            sibling = sibling_main[0]
            if selected_is_supplement and any(
                item["supplement_reason"] == "name" for item in selected_items
            ):
                message = (
                    f"選択音源 {os.path.basename(selected_path)} は補助パックのため、"
                    f"兄弟音源 {os.path.basename(str(sibling['directory']))} もフォールバックとして併用しました"
                )
            else:
                message = (
                    f"選択音源 {os.path.basename(selected_path)} のCV充足率が低いため、"
                    f"兄弟音源 {os.path.basename(str(sibling['directory']))} もフォールバックとして併用しました"
                )
            self.layer_notifications.append(message)
            logger.info(message)

        self.layer_info = ordered_info
        return layers, root, self.layer_info

    @staticmethod
    def _alias_equivalent_spellings(alias: str) -> List[str]:
        """Return the same basic kana/katakana/romaji variants as the Web resolver."""
        original = unicodedata.normalize("NFKC", str(alias))
        candidates: List[str] = []

        def add(value: str) -> None:
            if value and value not in candidates:
                candidates.append(value)

        def katakana_to_hiragana(value: str) -> str:
            return "".join(
                chr(ord(char) - 0x60)
                if "ァ" <= char <= "ヶ"
                else char
                for char in value
            )

        def map_units(
            value: str,
            mapping: Dict[str, str],
            preserve_initial_vowel: bool = False,
        ) -> str:
            scope, separator, leaf = value.rpartition("/")
            target = leaf if separator else value
            scope_prefix = scope + separator if separator else ""
            parts = re.split(r"([ _-]+)", target)
            lexical_indices = [
                index for index, part in enumerate(parts)
                if part and not re.fullmatch(r"[ _-]+", part)
            ]
            first_lexical = lexical_indices[0] if lexical_indices else -1
            for index in lexical_indices:
                token = parts[index]
                lookup = token.casefold()
                if (
                    preserve_initial_vowel
                    and index == first_lexical
                    and len(lexical_indices) > 1
                    and lookup in {"a", "i", "u", "e", "o", "n"}
                ):
                    continue
                parts[index] = mapping.get(lookup, token)
            return scope_prefix + "".join(parts)

        add(original)
        hiragana = katakana_to_hiragana(original)
        add(hiragana)
        add(map_units(hiragana, _KANA_TO_ROMAJI))
        add(map_units(original, _KANA_TO_ROMAJI))
        add(map_units(hiragana, _ROMAJI_TO_KANA, preserve_initial_vowel=True))
        add(map_units(original, _ROMAJI_TO_KANA, preserve_initial_vowel=True))
        return candidates

    def _lookup_alias(self, alias: str) -> Optional[OtoEntry]:
        key = str(alias)
        # Preserve exact first-wins behavior before trying alternative spellings.
        for candidate in self._alias_equivalent_spellings(key):
            normalized = unicodedata.normalize("NFKC", candidate)
            entry = self._normalized_db.get(normalized)
            if entry is not None:
                return entry
            exact = self._db.get(candidate)
            if exact is not None:
                return exact
            # Support callers/tests that populate _db directly instead of using loaders.
            for existing_key, existing_entry in self._db.items():
                if unicodedata.normalize("NFKC", existing_key) == normalized:
                    return existing_entry
        return None

    def _lookup_alias_for_note(
        self,
        alias: str,
        note_num: Optional[int],
    ) -> Optional[OtoEntry]:
        """Resolve same-alias multi-pitch WAV variants without changing first-wins get()."""
        key = str(alias)
        for candidate in self._alias_equivalent_spellings(key):
            normalized = unicodedata.normalize("NFKC", candidate)
            variants = self._alias_variants.get(normalized, [])
            if not variants:
                entry = self._lookup_alias(candidate)
                if entry is not None:
                    return entry
                continue

            preferred_layer = min(layer_index for layer_index, _entry in variants)
            preferred_entries: List[OtoEntry] = []
            seen_entries: set[int] = set()
            for layer_index, entry in variants:
                if layer_index != preferred_layer or id(entry) in seen_entries:
                    continue
                seen_entries.add(id(entry))
                preferred_entries.append(entry)

            if not preferred_entries:
                continue
            if note_num is None:
                return preferred_entries[0]

            pitch_entries: List[Tuple[int, OtoEntry]] = []
            for entry in preferred_entries:
                pitch_midi = _extract_pitch_midi(
                    entry.alias,
                    entry.filename,
                    entry.voice_dir,
                )
                if pitch_midi is not None:
                    pitch_entries.append((pitch_midi, entry))
            if pitch_entries:
                requested = int(round(float(note_num)))
                return min(
                    pitch_entries,
                    key=lambda item: abs(item[0] - requested),
                )[1]
            return preferred_entries[0]

        return None

    def _warn_unresolved(
        self,
        lyric: str,
        prev_vowel: Optional[str],
        note_num: Optional[int],
        tried_keys: List[str],
    ) -> None:
        report_key = (
            unicodedata.normalize("NFKC", str(lyric)),
            str(prev_vowel or ""),
            int(note_num) if note_num is not None else None,
        )
        if report_key in self._unresolved_reported:
            return
        self._unresolved_reported.add(report_key)
        if len(self._unresolved_reported) > 256:
            self._unresolved_reported.clear()
            self._unresolved_reported.add(report_key)
        logger.warning(
            "音源エイリアス未解決: lyric=%r prev_vowel=%r note=%r tried_keys=%s layers=%s",
            lyric,
            prev_vowel,
            note_num,
            list(dict.fromkeys(unicodedata.normalize("NFKC", key) for key in tried_keys)),
            " | ".join(self.loaded_layers),
        )

    def _log_resolution(
        self,
        entry: Optional[OtoEntry],
        requested_alias: str,
    ) -> Optional[OtoEntry]:
        """Record the exact source layer selected for a successful alias lookup."""
        if entry is not None:
            logger.debug(
                "音源エイリアス解決: requested=%r alias=%r layer=%s wav=%s",
                requested_alias,
                entry.alias,
                entry.voice_dir,
                entry.wav_path,
            )
        return entry

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_text_encoding(path: str) -> str:
        """Detect the supported encoding in bounded chunks without loading the file."""
        for encoding in ("utf-8-sig", "cp932", "utf-8", "latin-1"):
            try:
                decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
                with open(path, "rb") as raw_file:
                    while True:
                        chunk = raw_file.read(64 * 1024)
                        if not chunk:
                            break
                        decoder.decode(chunk, final=False)
                    decoder.decode(b"", final=True)
                return encoding
            except (UnicodeDecodeError, LookupError):
                continue
        return "cp932"

    def load_oto_file(
        self,
        ini_path: str,
        voice_root: Optional[str] = None,
        wav_search_roots: Optional[List[str]] = None,
        layer_priority: int = 0,
        max_entries: Optional[int] = None,
        cancel_event: Optional[threading.Event] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> int:
        """Stream one oto.ini into the alias database without default size/count caps."""
        if not os.path.isfile(ini_path):
            logger.warning("oto.ini が見つかりません: %s", ini_path)
            return 0

        limit = None if max_entries is None else max(0, int(max_entries))
        voice_dir = os.path.dirname(os.path.abspath(ini_path))
        encoding = self._detect_text_encoding(ini_path)
        count = 0

        try:
            with open(ini_path, "r", encoding=encoding, errors="strict", newline=None) as source:
                for idx, raw_line in enumerate(source):
                    if idx % 256 == 0 and cancel_event is not None and cancel_event.is_set():
                        raise VoicebankLoadCancelled("音源読み込みはキャンセルされました。")
                    line = raw_line.strip()
                    if not line or "=" not in line:
                        continue

                    entry = self._parse_line(line, voice_dir)
                    if entry is None:
                        continue
                    if limit is not None and count >= limit:
                        raise VoicebankResourceLimitError(
                            f"音源のOTOエントリ数が指定上限 ({limit:,}) を超えました: {ini_path}"
                        )

                    search_root = os.path.abspath(
                        os.path.expanduser(voice_root or voice_dir)
                    )
                    entry.search_root = search_root
                    entry.search_roots = tuple(
                        dict.fromkeys(
                            os.path.abspath(os.path.expanduser(root))
                            for root in (wav_search_roots or [search_root])
                        )
                    )
                    entry.layer_priority = int(layer_priority)
                    normalized_alias = unicodedata.normalize("NFKC", entry.alias)
                    existing = (
                        self._normalized_db.get(normalized_alias)
                        or self._db.get(entry.alias)
                    )
                    if existing is not None:
                        variants = self._alias_variants.setdefault(
                            normalized_alias,
                            [(int(existing.layer_priority), existing)],
                        )
                        variants.append((int(layer_priority), entry))
                        logger.warning(
                            "重複 alias を保持 (first-wins metadata / note-aware pitch): %s (%s) / 既存=%s",
                            entry.alias,
                            entry.wav_path,
                            existing.wav_path,
                        )
                    else:
                        self._db[entry.alias] = entry
                        self._normalized_db.setdefault(normalized_alias, entry)

                    # Preserve explicit pitch-folder scoped aliases.
                    if voice_root is not None:
                        try:
                            rel_dir = os.path.relpath(
                                voice_dir, os.path.abspath(voice_root)
                            )
                        except (OSError, ValueError):
                            rel_dir = "."
                        if rel_dir not in (".", ""):
                            prefix = rel_dir.replace(os.sep, "/").strip("/") + "/"
                            scoped_key = prefix + entry.alias
                            scoped_normalized = unicodedata.normalize("NFKC", scoped_key)
                            scoped_existing = self._normalized_db.get(scoped_normalized)
                            if scoped_existing is None:
                                self._db.setdefault(scoped_key, entry)
                                self._normalized_db.setdefault(scoped_normalized, entry)
                            else:
                                scoped_variants = self._alias_variants.setdefault(
                                    scoped_normalized,
                                    [(int(scoped_existing.layer_priority), scoped_existing)],
                                )
                                scoped_variants.append((int(layer_priority), entry))

                    count += 1
                    if IS_LOW_RAM and idx > 0 and idx % 300 == 0:
                        time.sleep(0.003)
                        if idx % 1200 == 0:
                            gc.collect()
                    if progress_callback is not None and idx > 0 and idx % 2048 == 0:
                        progress_callback(ini_path, idx, count)
        except (VoicebankLoadCancelled, VoicebankResourceLimitError):
            raise
        except MemoryError as exc:
            raise VoicebankResourceLimitError(
                f"音源の解析中にメモリ不足が発生しました: {ini_path}"
            ) from exc

        if hasattr(self, "_lyric_index"):
            self._lyric_index.clear()
        logger.debug("oto.ini ロード完了 (%d エントリ): %s", count, ini_path)
        return count

    def load_voice_dir(
        self,
        voice_dir: str,
        use_cache: bool = True,
        reset: bool = True,
        voices_dir: Optional[str] = None,
        write_cache: bool = True,
        cancel_event: Optional[threading.Event] = None,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> int:
        """Load one voicebank and its bounded fallback layers.

        Search is confined to the extracted bank root (character.txt/readme.txt)
        or, when those markers are absent, the selected direct child of voices/.
        The selected normal layer is first; append/extra/incomplete layers are
        deliberately kept after every normal layer.
        """
        selected = os.path.abspath(os.path.expanduser(voice_dir))
        if reset:
            self.clear()

        try:
            layers, scan_root, layer_info = self.collect_voice_layers(
                selected,
                voices_dir=voices_dir,
                cancel_event=cancel_event,
            )
        except (VoicebankLoadCancelled, VoicebankResourceLimitError):
            if reset:
                self.clear()
            raise
        except MemoryError as exc:
            if reset:
                self.clear()
            raise VoicebankResourceLimitError(
                "音源フォルダの走査中にメモリ不足が発生しました。"
            ) from exc
        if not layers:
            self.loaded_layers = ()
            self.loaded_oto_ini_paths = ()
            return 0

        self.loaded_layers = tuple(layers)
        self.loaded_oto_ini_paths = tuple(str(item["ini_path"]) for item in layer_info)
        self.layer_info = layer_info
        wav_search_roots = list(dict.fromkeys(
            os.path.abspath(str(item["root"])) for item in layer_info
        ))
        prefix_roots: List[str] = []
        for item in layer_info:
            if not item["supplement"]:
                candidate_root = str(item["root"])
                if candidate_root not in prefix_roots:
                    prefix_roots.append(candidate_root)
        for candidate_root in (scan_root, selected):
            if candidate_root not in prefix_roots:
                prefix_roots.append(candidate_root)
        prefix_map_path = next(
            (
                candidate
                for candidate_root in prefix_roots
                if (candidate := _find_prefix_map_path(candidate_root)) is not None
            ),
            None,
        )
        if reset:
            prefix_map_dir = (
                os.path.dirname(prefix_map_path) if prefix_map_path else scan_root
            )
            self._load_prefix_map(prefix_map_dir)
            clear_voice_dir_file_map_cache()

        cache_path = os.path.join(selected, ".oto_cache.json")
        source_signature: List[List[object]] = []
        for layer_index, item in enumerate(layer_info):
            ini_path = str(item["ini_path"])
            try:
                stat = os.stat(ini_path)
                source_signature.append([
                    os.path.abspath(ini_path),
                    stat.st_mtime_ns,
                    stat.st_size,
                    layer_index,
                    os.path.abspath(str(item["root"])),
                ])
            except OSError:
                continue

        if prefix_map_path is not None:
            try:
                stat = os.stat(prefix_map_path)
                source_signature.append([
                    os.path.abspath(prefix_map_path),
                    stat.st_mtime_ns,
                    stat.st_size,
                    -1,
                ])
            except OSError:
                pass
        source_signature.sort(key=lambda item: str(item[0]))

        cache_is_bounded = False
        if os.path.isfile(cache_path):
            try:
                cache_is_bounded = os.path.getsize(cache_path) <= MAX_OTO_CACHE_BYTES
            except OSError:
                cache_is_bounded = False
        if use_cache and reset and cache_is_bounded:
            try:
                import json
                with open(cache_path, "r", encoding="utf-8") as cache_file:
                    cached_data = json.load(cache_file)
                if (
                    cached_data.get("cache_version") == 6
                    and cached_data.get("source_signature") == source_signature
                ):
                    for item in cached_data.get("entries", []):
                        entry = OtoEntry(
                            alias=item["alias"],
                            filename=item["filename"],
                            voice_dir=item.get("voice_dir", selected),
                            search_root=item.get("search_root", item.get("voice_dir", selected)),
                            search_roots=tuple(
                                str(root)
                                for root in item.get(
                                    "search_roots",
                                    [item.get("search_root", item.get("voice_dir", selected))],
                                )
                            ),
                            left_blank=float(item.get("left_blank", 0)),
                            fixed_range=float(item.get("fixed_range", 0)),
                            right_blank=float(item.get("right_blank", 0)),
                            preutterance=float(item.get("preutterance", 0)),
                            overlap=float(item.get("overlap", 0)),
                            layer_priority=int(item.get("layer_priority", 0)),
                        )
                        db_key = item.get("db_key", entry.alias)
                        self._db[db_key] = entry
                        self._normalized_db.setdefault(
                            unicodedata.normalize("NFKC", db_key), entry
                        )
                    for item in cached_data.get("alias_variants", []):
                        entry = OtoEntry(
                            alias=item["alias"],
                            filename=item["filename"],
                            voice_dir=item.get("voice_dir", selected),
                            search_root=item.get("search_root", item.get("voice_dir", selected)),
                            search_roots=tuple(
                                str(root)
                                for root in item.get(
                                    "search_roots",
                                    [item.get("search_root", item.get("voice_dir", selected))],
                                )
                            ),
                            left_blank=float(item.get("left_blank", 0)),
                            fixed_range=float(item.get("fixed_range", 0)),
                            right_blank=float(item.get("right_blank", 0)),
                            preutterance=float(item.get("preutterance", 0)),
                            overlap=float(item.get("overlap", 0)),
                            layer_priority=int(item.get("layer_priority", 0)),
                        )
                        normalized_key = unicodedata.normalize("NFKC", item["key"])
                        self._alias_variants.setdefault(normalized_key, []).append(
                            (int(item.get("layer_priority", 0)), entry)
                        )
                    logger.info(
                        "oto.ini レイヤーキャッシュからロード (%d alias / %d 層): %s",
                        len(self._db),
                        len(self.loaded_layers),
                        selected,
                    )
                    return len(self._db)
            except MemoryError as exc:
                if reset:
                    self.clear()
                raise VoicebankResourceLimitError(
                    "音源キャッシュの読み込み中にメモリ不足が発生しました。キャッシュを削除して再試行してください。"
                ) from exc
            except (OSError, ValueError, TypeError, KeyError) as exc:
                logger.warning("oto.ini キャッシュ読み込みをスキップ (%s)", exc)
        elif use_cache and reset and os.path.isfile(cache_path):
            logger.warning(
                "oto.ini キャッシュが上限 (%s bytes) を超えたため、再生成します: %s",
                MAX_OTO_CACHE_BYTES,
                cache_path,
            )

        total = 0
        try:
            for layer_priority, item in enumerate(layer_info):
                if cancel_event is not None and cancel_event.is_set():
                    raise VoicebankLoadCancelled("音源読み込みはキャンセルされました。")
                ini_path = str(item["ini_path"])
                loaded = self.load_oto_file(
                    ini_path,
                    voice_root=str(item["root"]),
                    wav_search_roots=wav_search_roots,
                    layer_priority=layer_priority,
                    cancel_event=cancel_event,
                    progress_callback=progress_callback,
                )
                total += loaded
                logger.info(
                    "oto.ini レイヤー読込: %s (%d エントリ, 合計 %d, CV %.0f%%, %s)",
                    item["directory"],
                    loaded,
                    total,
                    float(item["coverage"]) * 100.0,
                    "補助" if item["supplement"] else "メイン",
                )
                if progress_callback is not None:
                    progress_callback(str(item["directory"]), total, len(layer_info))
                if IS_LOW_RAM:
                    time.sleep(0.005)
                    gc.collect()
        except (VoicebankLoadCancelled, VoicebankResourceLimitError):
            if reset:
                self.clear()
            raise
        except MemoryError as exc:
            if reset:
                self.clear()
            raise VoicebankResourceLimitError(
                "音源のインデックス作成中にメモリ不足が発生しました。"
            ) from exc

        if write_cache and reset:
            try:
                import json

                with open(cache_path, "w", encoding="utf-8") as cache_file:
                    cache_file.write('{"cache_version":6,"entries":[')
                    first_entry = True
                    for alias_key, entry in self._db.items():
                        if not first_entry:
                            cache_file.write(",")
                        first_entry = False
                        json.dump(
                            {
                                "db_key": alias_key,
                                "alias": entry.alias,
                                "filename": entry.filename,
                                "voice_dir": entry.voice_dir,
                                "search_root": entry.search_root,
                                "search_roots": list(entry.search_roots),
                                "left_blank": entry.left_blank,
                                "fixed_range": entry.fixed_range,
                                "right_blank": entry.right_blank,
                                "preutterance": entry.preutterance,
                                "overlap": entry.overlap,
                                "layer_priority": entry.layer_priority,
                            },
                            cache_file,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )

                    cache_file.write('],"alias_variants":[')
                    first_variant = True
                    for normalized_key, variants in self._alias_variants.items():
                        for layer_priority, entry in variants:
                            if not first_variant:
                                cache_file.write(",")
                            first_variant = False
                            json.dump(
                                {
                                    "key": normalized_key,
                                    "layer_priority": layer_priority,
                                    "alias": entry.alias,
                                    "filename": entry.filename,
                                    "voice_dir": entry.voice_dir,
                                    "search_root": entry.search_root,
                                    "search_roots": list(entry.search_roots),
                                    "left_blank": entry.left_blank,
                                    "fixed_range": entry.fixed_range,
                                    "right_blank": entry.right_blank,
                                    "preutterance": entry.preutterance,
                                    "overlap": entry.overlap,
                                },
                                cache_file,
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                    cache_file.write('],"source_signature":')
                    json.dump(source_signature, cache_file, ensure_ascii=False)
                    cache_file.write("}")
                logger.info("oto.ini レイヤーキャッシュ保存完了: %s", cache_path)
            except MemoryError:
                logger.warning("メモリ不足のため oto.ini キャッシュ保存をスキップします: %s", cache_path)
            except (OSError, TypeError, ValueError) as exc:
                logger.warning("oto.ini キャッシュ保存エラー (%s)", exc)

        return total

    def get(self, alias: str) -> Optional[OtoEntry]:
        """Alias の完全一致検索。NFKC 同値も first-wins で検索する。"""
        return self._lookup_alias(alias)

    def resolve_alias(
        self,
        lyric: str,
        prev_vowel: Optional[str] = None,
        note_num: Optional[int] = None,
    ) -> Optional[OtoEntry]:
        """VCV -> CV -> 単独音の順で、現在ノートに対応する oto.ini エントリを解決する."""
        if not lyric:
            return None

        # Keep rest markers consistent with the Web/WASM resolvers.  A rest
        # must never be turned into an audio alias even if a voicebank happens
        # to contain an alias with the same spelling.
        rest_lyrics = {
            "r", "r_", "r_0", "[r]", "息", "br", "pau", "sil",
            "吸", "吸気", "息吸い", "", " ", "　", "休", "休符",
            "・", "-", "ー", "~", "null",
        }
        lyric = unicodedata.normalize("NFKC", str(lyric))
        if lyric.strip().lower() in {item.lower() for item in rest_lyrics}:
            return None

        # Multi-pitch USTs may carry a folder-qualified lyric such as
        # "D4/あ" or "D4\\あ". Resolve that exact scoped key before applying
        # VCV/CV normalization so another pitch folder cannot be selected.
        scoped_lyric = str(lyric).strip().replace("\\\\", "/").replace("\\", "/")
        if "/" in scoped_lyric:
            exact_scoped = self._lookup_alias_for_note(scoped_lyric, note_num)
            if exact_scoped is not None:
                return self._log_resolution(exact_scoped, lyric)

        # Strip only a standalone VCV context marker. A leading "n" in a
        # romanized CV syllable such as "no" is a consonant, not a prefix.
        clean_lyric = re.sub(
            r"^(?:[-_]\s*|[aieuon](?:\s+|_))",
            "",
            lyric,
            flags=re.IGNORECASE,
        ).strip() or lyric
        clean_lyric = re.sub(
            r"_?[A-Ga-g][#b]?[0-9]$",
            "",
            clean_lyric,
        ).strip() or clean_lyric

        attempted_keys: List[str] = [lyric, clean_lyric]
        if prev_vowel:
            attempted_keys.extend((
                f"{prev_vowel} {clean_lyric}",
                f"{prev_vowel}_{clean_lyric}",
                f"{prev_vowel}{clean_lyric}",
            ))
        attempted_keys.extend((
            f"- {clean_lyric}", f"_{clean_lyric}", f"-{clean_lyric}", clean_lyric
        ))
        if note_num is not None:
            for candidate_key in list(attempted_keys):
                mapped_key = map_alias(candidate_key, note_num, self._prefix_map)
                if mapped_key and mapped_key != candidate_key:
                    attempted_keys.append(mapped_key)

        def match_pref(prefix: str) -> Optional[OtoEntry]:
            attempted_keys.append(prefix)
            entry = self._lookup_alias_for_note(prefix, note_num)
            if entry is not None:
                return entry

            prefix_lower = unicodedata.normalize("NFKC", prefix).casefold()
            default_matches: Dict[int, OtoEntry] = {}
            pitch_matches: Dict[int, Tuple[int, OtoEntry]] = {}
            for alias, candidate in self._db.items():
                alias_lower = unicodedata.normalize("NFKC", alias).casefold()
                if not (
                    alias_lower.startswith(prefix_lower + "_")
                    or alias_lower.startswith(prefix_lower + " ")
                ):
                    continue

                # A pitch-qualified alias is never an arbitrary fallback when
                # prefix.map is present. Without a map, use its explicit pitch
                # tag and the requested MIDI note to choose the closest layer.
                pitch_midi = _extract_pitch_midi(
                    alias, candidate.filename, candidate.voice_dir
                )
                if _PITCH_SUFFIX_RE.search(alias.strip()) or pitch_midi is not None:
                    if not self._prefix_map and pitch_midi is not None:
                        pitch_matches[id(candidate)] = (pitch_midi, candidate)
                    continue

                default_matches[id(candidate)] = candidate

            if len(default_matches) == 1:
                return next(iter(default_matches.values()))
            if len(default_matches) > 1 or self._prefix_map:
                return None

            if pitch_matches:
                if note_num is None:
                    return next(iter(pitch_matches.values()))[1] if len(pitch_matches) == 1 else None
                requested = int(round(float(note_num)))
                best_distance = min(
                    abs(pitch_midi - requested)
                    for pitch_midi, _candidate in pitch_matches.values()
                )
                best = [
                    candidate
                    for pitch_midi, candidate in pitch_matches.values()
                    if abs(pitch_midi - requested) == best_distance
                ]
                return best[0] if len(best) == 1 else None

            return None

        def match_mapped(alias: str) -> Optional[OtoEntry]:
            explicitly_qualified = (
                "/" in alias
                or "\\" in alias
                or " " in alias
                or "_" in alias
                or alias.startswith("-")
            )
            if explicitly_qualified:
                explicit_entry = self._lookup_alias_for_note(alias, note_num)
                if explicit_entry is not None:
                    return explicit_entry
            if note_num is None:
                return None
            mapped = map_alias(alias, note_num, self._prefix_map)
            if mapped == alias:
                return None
            attempted_keys.append(mapped)
            return self._lookup_alias_for_note(mapped, note_num)

        # When no prefix.map range covers this note, use only exact aliases
        # from the voicebank's unmarked/default layer. Do not run the broader
        # suffix/partial matcher in this case: it could borrow an unrelated
        # pitch layer. This mirrors OtoDatabase::resolveAlias() in C++ and
        # keeps ordinary CV/VCV aliases usable below the first mapped boundary.
        if note_num is not None and self._prefix_map:
            from modules.data.prefix_map import select_prefix_map
            explicit_alias = (
                self._lookup_alias_for_note(str(lyric), note_num) is not None
                and (" " in str(lyric) or "_" in str(lyric) or str(lyric).startswith("-"))
            )
            if select_prefix_map(self._prefix_map, int(note_num)) is None and not explicit_alias:
                exact_candidates: List[str] = []
                if prev_vowel:
                    exact_candidates.extend((
                        f"{prev_vowel} {clean_lyric}",
                        f"{prev_vowel}_{clean_lyric}",
                        f"{prev_vowel}{clean_lyric}",
                    ))
                exact_candidates.extend((
                    f"- {clean_lyric}",
                    f"_{clean_lyric}",
                    f"-{clean_lyric}",
                    clean_lyric,
                    str(lyric),
                ))
                for candidate in dict.fromkeys(exact_candidates):
                    entry = self._lookup_alias_for_note(candidate, note_num)
                    if entry is not None:
                        return self._log_resolution(entry, lyric)
                # No exact default alias exists. Keep the pitch-layer boundary
                # strict instead of selecting a partially matching subbank.
                self._warn_unresolved(lyric, prev_vowel, note_num, attempted_keys)
                return None

        # 明示的な VCV/CV alias はそのまま優先する。
        if self._lookup_alias_for_note(lyric, note_num) is not None and (
            " " in lyric or "_" in lyric or lyric.startswith("-")
        ):
            return self._log_resolution(self._lookup_alias_for_note(lyric, note_num), lyric)

        if prev_vowel:
            entry = (
                match_mapped(f"{prev_vowel} {clean_lyric}")
                or match_mapped(f"{prev_vowel}_{clean_lyric}")
                or match_mapped(f"{prev_vowel}{clean_lyric}")
                or match_pref(f"{prev_vowel} {clean_lyric}")
                or match_pref(f"{prev_vowel}_{clean_lyric}")
                or match_pref(f"{prev_vowel}{clean_lyric}")
            )
            if entry is not None:
                return self._log_resolution(entry, lyric)

        entry = (
            match_mapped(f"- {clean_lyric}")
            or match_mapped(f"_{clean_lyric}")
            or match_mapped(f"-{clean_lyric}")
            or match_mapped(clean_lyric)
            or match_pref(f"- {clean_lyric}")
            or match_pref(f"_{clean_lyric}")
            or match_pref(f"-{clean_lyric}")
        )
        if entry is not None:
            return self._log_resolution(entry, lyric)

        exact_entry = self._lookup_alias_for_note(lyric, note_num)
        if exact_entry is not None:
            return self._log_resolution(exact_entry, lyric)

        entry = match_pref(clean_lyric)
        if entry is not None:
            return self._log_resolution(entry, lyric)

        # When a previous vowel is explicitly supplied, do not fall back to
        # an arbitrary VCV alias with the same lyric. That would silently
        # replace the requested context (for example u い with a い).
        if prev_vowel:
            self._warn_unresolved(lyric, prev_vowel, note_num, attempted_keys)
            return None

        # Do not infer a VCV predecessor when none was supplied. A first
        # note or a note after a rest must use an exact CV/default alias, never
        # whichever "a い" / "u い" entry happens to be indexed first.
        self._warn_unresolved(lyric, prev_vowel, note_num, attempted_keys)
        return None

    def _build_lyric_index(self) -> None:
        """Alias 末尾の歌詞部分から逆引きインデックスを構築する。"""
        self._lyric_index: Dict[str, List[OtoEntry]] = {}
        for alias, entry in self._db.items():
            if "/" in alias or "\\\\" in alias:
                continue
            parts = alias.strip().split()
            pure_lyric = parts[-1] if parts else alias
            self._lyric_index.setdefault(pure_lyric, []).append(entry)

    def _load_prefix_map(self, voice_dir: str) -> None:
        """Load the optional root-level UTAU prefix.map."""
        self._prefix_map = []
        path = _find_prefix_map_path(voice_dir)
        if path is None:
            return
        try:
            self._prefix_map = parse_prefix_map(self._read_safe(path))
            logger.info("prefix.map ロード完了 (%d 行): %s", len(self._prefix_map), path)
        except OSError as exc:
            logger.warning("prefix.map 読み込み失敗 (%s): %s", path, exc)

    def clear(self) -> None:
        """ロード済み oto.ini データをすべて破棄する。"""
        self._db.clear()
        self._normalized_db.clear()
        self._alias_variants.clear()
        self._prefix_map = []
        self.loaded_layers = ()
        self.loaded_oto_ini_paths = ()
        self.layer_info = []
        self.layer_notifications = []
        self._unresolved_reported.clear()
        if hasattr(self, "_lyric_index"):
            self._lyric_index.clear()
        # 音源切替後に、削除・追加・リネームされた WAV の古い
        # ケース非依存パス解決結果を再利用しない。
        clear_voice_dir_file_map_cache()

    def get_preutterance_sec(self, alias: str, default: float = 0.05) -> float:
        entry = self.get(alias)
        return entry.preutterance_sec if entry else default

    def get_overlap_sec(self, alias: str, default: float = 0.02) -> float:
        entry = self.get(alias)
        return entry.overlap_sec if entry else default

    def all_aliases(self) -> List[str]:
        return list(self._db.keys())

    def has_vcv(self) -> bool:
        """スペースを含む alias が存在する場合に VCV 対応音源と判定する。"""
        return any(" " in alias for alias in self._db)

    @staticmethod
    def _read_safe(path: str) -> str:
        """Web版と同じく UTF-8 を優先し、失敗したら Shift-JIS/CP932 を読む。"""
        for enc in ("utf-8-sig", "cp932", "utf-8", "latin-1"):
            try:
                with open(path, "r", encoding=enc, errors="strict") as f:
                    return f.read()
            except (UnicodeDecodeError, LookupError):
                continue
        with open(path, "r", encoding="cp932", errors="ignore") as f:
            return f.read()

    @staticmethod
    def _parse_line(line: str, voice_dir: str) -> Optional[OtoEntry]:
        """filename.wav=alias,offset,consonant,cutoff,preutterance,overlap を読む。"""
        try:
            filename_part, params_part = line.split("=", 1)
            filename_part = filename_part.strip()
            parts = [part.strip() for part in params_part.split(",")]
            alias = (
                parts[0]
                if parts and parts[0]
                else os.path.splitext(filename_part)[0]
            )

            def as_float(index: int, fallback: float = 0.0) -> float:
                try:
                    value = parts[index]
                    return float(value) if value else fallback
                except (IndexError, TypeError, ValueError):
                    return fallback

            return OtoEntry(
                alias=alias,
                filename=filename_part,
                voice_dir=voice_dir,
                left_blank=as_float(1),
                fixed_range=as_float(2),
                right_blank=as_float(3),
                preutterance=as_float(4),
                overlap=as_float(5),
            )
        except Exception as exc:
            logger.debug("oto.ini 行のパース失敗 (%s): %s", exc, line)
            return None
