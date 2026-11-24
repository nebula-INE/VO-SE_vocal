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

import os
import re

import logging
import gc
import time
import unicodedata
from dataclasses import dataclass
from modules.data.prefix_map import (
    PrefixMapEntry,
    map_alias,
    note_name_to_midi,
    parse_prefix_map,
)
from typing import Callable, Dict, List, Optional, Tuple, cast

logger = logging.getLogger(__name__)

_PITCH_SUFFIX_RE = re.compile(r"(?:_?[A-Ga-g][#b]?-?\d+)$", re.IGNORECASE)
_PITCH_TAG_RE = re.compile(
    r"(?:^|[_\s/\\.\-])([A-Ga-g])([#b]?)(-?\d+)(?=$|[_\s/\\.\-])"
)
_PITCH_SUFFIX_TAG_RE = re.compile(r"([A-Ga-g])([#b]?)(-?\d+)$", re.IGNORECASE)


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

# Global cache for voice directory file maps to eliminate O(N) disk walks
_DIR_FILE_MAP_CACHE: Dict[str, Dict[str, str]] = {}

def get_voice_dir_file_map(voice_dir: str) -> Dict[str, str]:
    if voice_dir in _DIR_FILE_MAP_CACHE:
        return _DIR_FILE_MAP_CACHE[voice_dir]
    file_map: Dict[str, str] = {}
    if os.path.exists(voice_dir):
        for root, _dirs, files in os.walk(voice_dir):
            for f in files:
                full_path = os.path.join(root, f)
                rel_path = os.path.relpath(full_path, voice_dir)
                rel_key = rel_path.replace("\\", "/").lower()
                if rel_key not in file_map:
                    file_map[rel_key] = full_path
    _DIR_FILE_MAP_CACHE[voice_dir] = file_map
    return file_map

def clear_voice_dir_file_map_cache() -> None:
    _DIR_FILE_MAP_CACHE.clear()
    gc.collect()


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

    @property
    def wav_path(self) -> str:
        """フルパスで WAV へのパスを返す (区切り文字・大文字小文字差を吸収)。"""
        # oto.ini は Windows 環境で "\" 区切りを保存することがあるため、
        # 実行環境の区切り文字へ正規化してから解決する。
        normalized_filename = self.filename.replace("\\", os.sep).replace("/", os.sep)
        if os.path.isabs(normalized_filename):
            exact_path = os.path.normpath(normalized_filename)
        else:
            exact_path = os.path.normpath(
                os.path.join(self.voice_dir, normalized_filename)
            )
        # 先に実ファイルシステム上の実際の名前へ解決する。
        # macOS/Windows の case-insensitive filesystem では
        # os.path.isfile(exact_path) が表記違いでも True になるため、
        # これを先に行わないと `SubVoice` が `subvoice` のまま返る。
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
                if os.path.isfile(current):
                    return current

        if os.path.isfile(exact_path):
            return exact_path

        # Case-insensitive & nested-path resolution using a relative-path cache.
        file_map = get_voice_dir_file_map(self.voice_dir)
        target_lower = (
            normalized_filename.replace(os.sep, "/").replace("\\", "/").lower()
        )
        resolved = file_map.get(target_lower)
        if resolved:
            return resolved

        # 拡張子省略の oto.ini にも対応するが、相対パス全体をキーにする。
        if not target_lower.endswith(".wav"):
            resolved = file_map.get(target_lower + ".wav")
            if resolved:
                return resolved

        # ファイルがまだ存在しない場合でも、呼び出し側が存在確認できる
        # 一貫したパスを返す。
        return exact_path

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
        self._prefix_map: List[PrefixMapEntry] = []
        self.loaded_layers: Tuple[str, ...] = ()
        self.loaded_oto_ini_paths: Tuple[str, ...] = ()
        self.layer_info: List[Dict[str, object]] = []
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
    def _infer_voices_dir(start: str, voices_dir: Optional[str] = None) -> Optional[str]:
        if voices_dir:
            candidate = os.path.abspath(os.path.expanduser(voices_dir))
            return candidate if os.path.isdir(candidate) else None
        current = os.path.abspath(start)
        while True:
            if os.path.basename(current).casefold() == "voices" and os.path.isdir(current):
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
        max_up: int = 3,
    ) -> str:
        """Find the extracted bank root without walking into unrelated voicebanks.

        Search the selected directory and at most max_up parents for UTAU root
        markers. If none exists, limit fallback to the direct child of voices/
        containing the selection. Without a recognizable voices directory, the
        selected directory itself is the search boundary.
        """
        selected = os.path.abspath(os.path.expanduser(start))
        voices_root = cls._infer_voices_dir(selected, voices_dir)
        current = selected
        for _ in range(max(0, int(max_up)) + 1):
            if cls._contains_root_marker(current):
                return current
            if voices_root and os.path.normcase(current) == os.path.normcase(voices_root):
                break
            if voices_root and not cls._is_within(current, voices_root):
                break
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

        if voices_root and cls._is_within(selected, voices_root):
            relative = os.path.relpath(selected, voices_root)
            if relative in ("", "."):
                return voices_root
            first_child = relative.split(os.sep, 1)[0]
            return os.path.join(voices_root, first_child)
        return selected

    @staticmethod
    def _layer_aliases_and_coverage(ini_path: str) -> Tuple[set[str], float]:
        """Return the hiragana CV units represented by one oto.ini and coverage."""
        hiragana_cv = set(
            "あいうえお"
            "かきくけこさしすせそたちつてとなにぬねの"
            "はひふへほまみむめもやゆよらりるれろわをん"
            "がぎぐげござじずぜぞだぢづでどばびぶべぼ"
            "ぱぴぷぺぽぁぃぅぇぉゃゅょゔっ"
        )
        covered: set[str] = set()
        try:
            text = OtoParser._read_safe(ini_path)
        except OSError:
            return covered, 0.0

        for raw_line in text.splitlines():
            if "=" not in raw_line:
                continue
            filename, params = raw_line.split("=", 1)
            fields = params.split(",")
            alias = fields[0].strip() if fields and fields[0].strip() else os.path.splitext(filename.strip())[0]
            alias = unicodedata.normalize("NFKC", alias)
            alias = re.sub(r"^(?:[-_]\s*|[aieuon](?:\s+|_))", "", alias, flags=re.IGNORECASE)
            alias = re.sub(r"_?[A-Ga-g][#b]?-?\d+$", "", alias).strip()
            if "/" in alias or "\\" in alias:
                alias = re.split(r"[/\\]", alias)[-1]
            tokens = alias.split()
            mora = tokens[-1] if tokens else alias
            if mora in hiragana_cv:
                covered.add(mora)

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
    ) -> Tuple[List[str], str, List[Dict[str, object]]]:
        """Collect bounded oto.ini layers and order main banks before supplements."""
        selected_path = os.path.abspath(os.path.expanduser(selected))
        if not os.path.isdir(selected_path):
            return [], selected_path, []

        root = self.find_voice_root(selected_path, voices_dir=voices_dir, max_up=max_up)
        found: List[Tuple[str, str]] = []
        for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
            dirs[:] = sorted(
                directory for directory in dirs
                if not os.path.islink(os.path.join(current, directory))
            )
            oto_names = sorted(name for name in files if name.casefold() == "oto.ini")
            if oto_names:
                found.append((os.path.abspath(current), os.path.join(current, oto_names[0])))

        selected_has_layer = any(
            os.path.normcase(os.path.realpath(directory))
            == os.path.normcase(os.path.realpath(selected_path))
            for directory, _ini_path in found
        )
        info: List[Dict[str, object]] = []
        for directory, ini_path in found:
            covered, coverage = self._layer_aliases_and_coverage(ini_path)
            supplement, reason = self._is_supplement_layer(directory, root, coverage)
            selected_subtree = (
                os.path.normcase(os.path.realpath(directory))
                == os.path.normcase(os.path.realpath(selected_path))
                or (not selected_has_layer and self._is_within(directory, selected_path))
            )
            exact_selected = (
                os.path.normcase(os.path.realpath(directory))
                == os.path.normcase(os.path.realpath(selected_path))
            )
            relative_depth = len(os.path.relpath(directory, root).split(os.sep))
            info.append({
                "directory": directory,
                "ini_path": ini_path,
                "coverage": coverage,
                "covered": covered,
                "supplement": supplement,
                "supplement_reason": reason,
                "selected": exact_selected,
                "selected_subtree": selected_subtree,
                "relative_depth": relative_depth,
            })

        def priority(item: Dict[str, object]) -> Tuple[int, int, str]:
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
        selected_is_supplement = any(bool(item["supplement"]) for item in selected_items)
        if selected_items and (selected_coverage < 0.55 or selected_is_supplement) and sibling_main:
            sibling = sibling_main[0]
            message = (
                f"選択音源 {os.path.basename(selected_path)} のCV充足率が低いため、"
                f"兄弟音源 {os.path.basename(str(sibling['directory']))} をフォールバックとして併用しました"
            )
            self.layer_notifications.append(message)
            logger.info(message)

        self.layer_info = [
            {key: value for key, value in item.items() if key != "covered"}
            for item in ordered_info
        ]
        return layers, root, self.layer_info

    def _lookup_alias(self, alias: str) -> Optional[OtoEntry]:
        key = str(alias)
        normalized = unicodedata.normalize("NFKC", key)
        entry = self._normalized_db.get(normalized)
        if entry is not None:
            return entry
        exact = self._db.get(key)
        if exact is not None:
            return exact
        # Support callers/tests that populate _db directly instead of using loaders.
        for existing_key, existing_entry in self._db.items():
            if unicodedata.normalize("NFKC", existing_key) == normalized:
                return existing_entry
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
            list(self.loaded_layers),
        )

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def load_oto_file(self, ini_path: str, voice_root: Optional[str] = None) -> int:
        """
        oto.ini を 1 ファイル読み込んでデータベースに追加する (8GB以下環境対応スロットリング)。

        Returns:
            追加されたエントリ数
        """
        if not os.path.isfile(ini_path):
            logger.warning("oto.ini が見つかりません: %s", ini_path)
            return 0

        voice_dir = os.path.dirname(os.path.abspath(ini_path))
        content = self._read_safe(ini_path)
        count = 0

        lines = content.splitlines()
        for idx, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not line or "=" not in line:
                continue
            entry = self._parse_line(line, voice_dir)
            if entry is not None:
                # 複数 oto.ini を再帰ロードしたとき、同名 alias を
                # 後から読んだファイルで上書きしない。Web 側も同じ
                # first-wins 方針なので、3経路の解決結果を一致させる。
                normalized_alias = unicodedata.normalize("NFKC", entry.alias)
                if entry.alias in self._db or normalized_alias in self._normalized_db:
                    existing = self._lookup_alias(entry.alias)
                    logger.warning(
                        "重複 alias をスキップ (first-wins): %s (%s) / 既存=%s",
                        entry.alias,
                        entry.wav_path,
                        existing.wav_path if existing is not None else "<unknown>",
                    )
                else:
                    self._db[entry.alias] = entry
                self._normalized_db.setdefault(
                    normalized_alias, self._db.get(entry.alias, entry)
                )

                # UTAU/OpenUtau multi-pitch banks can address an entry with
                # a folder-qualified lyric such as "D4/あ" or "D4\\あ".
                # Keep that qualified key in addition to the unqualified
                # alias so duplicate aliases in different pitch folders do
                # not become ambiguous.
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
                        self._db.setdefault(scoped_key, entry)
                        self._normalized_db.setdefault(
                            unicodedata.normalize("NFKC", scoped_key),
                            self._db[scoped_key],
                        )

                count += 1

            # Low RAM throttle: pause slightly and collect garbage every 300 lines
            if IS_LOW_RAM and idx > 0 and idx % 300 == 0:
                time.sleep(0.003)
                if idx % 1200 == 0:
                    gc.collect()

        # Alias 逆引きインデックスを既に構築していた場合は、
        # 新しい oto.ini の追加内容を反映するため無効化する。
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

        layers, scan_root, layer_info = self.collect_voice_layers(
            selected,
            voices_dir=voices_dir,
        )
        if not layers:
            self.loaded_layers = ()
            self.loaded_oto_ini_paths = ()
            return 0

        self.loaded_layers = tuple(layers)
        self.loaded_oto_ini_paths = tuple(str(item["ini_path"]) for item in layer_info)
        self.layer_info = layer_info
        if reset:
            self._load_prefix_map(scan_root)
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
                ])
            except OSError:
                continue

        prefix_map_path = _find_prefix_map_path(scan_root)
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

        if use_cache and reset and os.path.isfile(cache_path):
            try:
                import json
                with open(cache_path, "r", encoding="utf-8") as cache_file:
                    cached_data = json.load(cache_file)
                if (
                    cached_data.get("cache_version") == 3
                    and cached_data.get("source_signature") == source_signature
                ):
                    for item in cached_data.get("entries", []):
                        entry = OtoEntry(
                            alias=item["alias"],
                            filename=item["filename"],
                            voice_dir=item.get("voice_dir", selected),
                            left_blank=float(item.get("left_blank", 0)),
                            fixed_range=float(item.get("fixed_range", 0)),
                            right_blank=float(item.get("right_blank", 0)),
                            preutterance=float(item.get("preutterance", 0)),
                            overlap=float(item.get("overlap", 0)),
                        )
                        db_key = item.get("db_key", entry.alias)
                        self._db[db_key] = entry
                        self._normalized_db.setdefault(
                            unicodedata.normalize("NFKC", db_key), entry
                        )
                    logger.info(
                        "oto.ini レイヤーキャッシュからロード (%d alias / %d 層): %s",
                        len(self._db),
                        len(self.loaded_layers),
                        selected,
                    )
                    return len(self._db)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                logger.warning("oto.ini キャッシュ読み込みをスキップ (%s)", exc)

        total = 0
        for item in layer_info:
            ini_path = str(item["ini_path"])
            loaded = self.load_oto_file(ini_path, voice_root=scan_root)
            total += loaded
            logger.info(
                "oto.ini レイヤー読込: %s (%d エントリ, CV %.0f%%, %s)",
                item["directory"],
                loaded,
                float(item["coverage"]) * 100.0,
                "補助" if item["supplement"] else "メイン",
            )
            if IS_LOW_RAM:
                time.sleep(0.005)
                gc.collect()

        if write_cache and reset:
            try:
                import json
                cache_entries = []
                for alias_key, entry in self._db.items():
                    cache_entries.append({
                        "db_key": alias_key,
                        "alias": entry.alias,
                        "filename": entry.filename,
                        "voice_dir": entry.voice_dir,
                        "left_blank": entry.left_blank,
                        "fixed_range": entry.fixed_range,
                        "right_blank": entry.right_blank,
                        "preutterance": entry.preutterance,
                        "overlap": entry.overlap,
                    })
                with open(cache_path, "w", encoding="utf-8") as cache_file:
                    json.dump(
                        {
                            "cache_version": 3,
                            "entries": cache_entries,
                            "source_signature": source_signature,
                        },
                        cache_file,
                        ensure_ascii=False,
                    )
                logger.info("oto.ini レイヤーキャッシュ保存完了: %s", cache_path)
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
            exact_scoped = self._lookup_alias(scoped_lyric)
            if exact_scoped is not None:
                return exact_scoped

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
            entry = self._lookup_alias(prefix)
            if entry is not None:
                return entry

            prefix_lower = prefix.casefold()
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
            if note_num is None:
                return None
            mapped = map_alias(alias, note_num, self._prefix_map)
            if mapped == alias:
                return None
            attempted_keys.append(mapped)
            return self._lookup_alias(mapped)

        # When no prefix.map range covers this note, use only exact aliases
        # from the voicebank's unmarked/default layer. Do not run the broader
        # suffix/partial matcher in this case: it could borrow an unrelated
        # pitch layer. This mirrors OtoDatabase::resolveAlias() in C++ and
        # keeps ordinary CV/VCV aliases usable below the first mapped boundary.
        if note_num is not None and self._prefix_map:
            from modules.data.prefix_map import select_prefix_map
            explicit_alias = (
                self._lookup_alias(str(lyric)) is not None
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
                    entry = self._lookup_alias(candidate)
                    if entry is not None:
                        return entry
                # No exact default alias exists. Keep the pitch-layer boundary
                # strict instead of selecting a partially matching subbank.
                self._warn_unresolved(lyric, prev_vowel, note_num, attempted_keys)
                return None

        # 明示的な VCV/CV alias はそのまま優先する。
        if self._lookup_alias(lyric) is not None and (
            " " in lyric or "_" in lyric or lyric.startswith("-")
        ):
            return self._lookup_alias(lyric)

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
                return entry

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
            return entry

        if self._lookup_alias(lyric) is not None:
            return self._db[lyric]

        entry = match_pref(clean_lyric)
        if entry is not None:
            return entry

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
        """Shift-JIS / UTF-8 / latin-1 の順で oto.ini を読む。"""
        for enc in ("cp932", "utf-8-sig", "utf-8", "latin-1"):
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
