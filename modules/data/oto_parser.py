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
from dataclasses import dataclass
from modules.data.prefix_map import PrefixMapEntry, map_alias, parse_prefix_map
from typing import Callable, Dict, List, Optional, cast

logger = logging.getLogger(__name__)

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
        # alias → OtoEntry の辞書（複数 oto.ini をマージして保持）
        self._db: Dict[str, OtoEntry] = {}
        self._prefix_map: List[PrefixMapEntry] = []

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
                if entry.alias in self._db:
                    existing = self._db[entry.alias]
                    logger.warning(
                        "重複 alias をスキップ: %s (%s) / 既存=%s",
                        entry.alias,
                        entry.wav_path,
                        existing.wav_path,
                    )
                else:
                    self._db[entry.alias] = entry

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
    ) -> int:
        """
        指定フォルダ（サブフォルダ含む）の oto.ini を全部ロードする。

        load_oto_file() は複数ファイルを意図的にマージするAPIですが、
        load_voice_dir() は通常「1つの音源バンクをロードする」APIです。
        そのためデフォルトでは前の音源のエントリを破棄し、音源切替時の
        エントリ混入を防ぎます。複数バンクを明示的に統合したい場合は
        reset=False を指定してください。

        Returns:
            この呼び出しでロード対象になったエントリ数
        """
        if reset:
            self.clear()
            self._load_prefix_map(voice_dir)
            # 音源切替・再スキャン時に、前回のファイル一覧を使わない。
            clear_voice_dir_file_map_cache()

        if not os.path.exists(voice_dir):
            return 0

        cache_path = os.path.join(voice_dir, ".oto_cache.json")

        if not os.path.exists(voice_dir):
            return 0

        cache_path = os.path.join(voice_dir, ".oto_cache.json")

        # 1. Build a complete source signature. Checking only the newest mtime
        #    leaves stale cache entries when an oto.ini is deleted or replaced.
        ini_files = []
        source_signature = []
        for root, _dirs, files in os.walk(voice_dir):
            for fname in files:
                if fname.lower() == "oto.ini":
                    full_p = os.path.abspath(os.path.join(root, fname))
                    ini_files.append(full_p)
                    try:
                        stat = os.stat(full_p)
                        source_signature.append(
                            [full_p, stat.st_mtime_ns, stat.st_size]
                        )
                    except OSError:
                        continue
        ini_files.sort()
        prefix_map_path = os.path.join(voice_dir, "prefix.map")
        if os.path.isfile(prefix_map_path):
            try:
                stat = os.stat(prefix_map_path)
                source_signature.append(
                    [os.path.abspath(prefix_map_path), stat.st_mtime_ns, stat.st_size]
                )
            except OSError:
                pass
        source_signature.sort(key=lambda item: item[0])

        # 2. Try loading from .oto_cache.json if valid
        if use_cache and os.path.exists(cache_path):
            try:
                import json
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                cached_signature = cached_data.get("source_signature")
                cached_version = cached_data.get("cache_version", 1)
                if cached_version >= 2 and cached_signature == source_signature:
                    for item in cached_data.get("entries", []):
                        entry = OtoEntry(
                            alias=item["alias"],
                            filename=item["filename"],
                            voice_dir=item.get("voice_dir", voice_dir),
                            left_blank=float(item.get("left_blank", 0)),
                            fixed_range=float(item.get("fixed_range", 0)),
                            right_blank=float(item.get("right_blank", 0)),
                            preutterance=float(item.get("preutterance", 0)),
                            overlap=float(item.get("overlap", 0))
                        )
                        self._db[item.get("db_key", entry.alias)] = entry
                    logger.info(
                        "oto.ini キャッシュから超高速ロード成功 (%d エントリ): %s",
                        len(self._db),
                        voice_dir,
                    )
                    return len(self._db)
            except Exception as ex:
                logger.warning("oto.ini キャッシュ読み込みスキップ (%s)", ex)

        # 3. Cache missed / outdated -> Perform full parsing
        total = 0
        for ini_p in ini_files:
            total += self.load_oto_file(ini_p, voice_root=voice_dir)
            if IS_LOW_RAM:
                time.sleep(0.005)
                gc.collect()

        # 4. Save cache asynchronously / safely
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
                    "overlap": entry.overlap
                })
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "cache_version": 2,
                        "entries": cache_entries,
                        "source_signature": source_signature,
                    },
                    f,
                    ensure_ascii=False,
                )
            logger.info("oto.ini 高速キャッシュ保存完了: %s", cache_path)
        except Exception as ex_save:
            logger.warning("oto.ini キャッシュ保存エラー (%s)", ex_save)

        return total


    def get(self, alias: str) -> Optional[OtoEntry]:
        """Alias の完全一致検索。"""
        return self._db.get(alias)

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
        if str(lyric).strip().lower() in {item.lower() for item in rest_lyrics}:
            return None

        # Multi-pitch USTs may carry a folder-qualified lyric such as
        # "D4/あ" or "D4\\あ". Resolve that exact scoped key before applying
        # VCV/CV normalization so another pitch folder cannot be selected.
        scoped_lyric = str(lyric).strip().replace("\\\\", "/").replace("\\", "/")
        if "/" in scoped_lyric:
            exact_scoped = self._db.get(scoped_lyric)
            if exact_scoped is not None:
                return exact_scoped

        clean_lyric = re.sub(
            r"^[-aieuon_]\s*",
            "",
            lyric,
            flags=re.IGNORECASE,
        ).strip() or lyric
        clean_lyric = re.sub(
            r"_?[A-Ga-g][#b]?[0-9]$",
            "",
            clean_lyric,
        ).strip() or clean_lyric

        def match_pref(prefix: str) -> Optional[OtoEntry]:
            entry = self._db.get(prefix)
            if entry is not None:
                return entry
            prefix_lower = prefix.lower()
            for alias, candidate in self._db.items():
                alias_lower = alias.lower()
                if (
                    alias_lower.startswith(prefix_lower + "_")
                    or alias_lower.startswith(prefix_lower + " ")
                ):
                    return candidate
            return None

        def match_mapped(alias: str) -> Optional[OtoEntry]:
            if note_num is None:
                return None
            mapped = map_alias(alias, note_num, self._prefix_map)
            if mapped == alias:
                return None
            return self._db.get(mapped)

        # 明示的な VCV/CV alias はそのまま優先する。
        if lyric in self._db and (
            " " in lyric or "_" in lyric or lyric.startswith("-")
        ):
            return self._db[lyric]

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

        if lyric in self._db:
            return self._db[lyric]

        entry = match_pref(clean_lyric)
        if entry is not None:
            return entry

        # When a previous vowel is explicitly supplied, do not fall back to
        # an arbitrary VCV alias with the same lyric. That would silently
        # replace the requested context (for example u い with a い).
        if prev_vowel:
            return None

        if not hasattr(self, "_lyric_index"):
            self._build_lyric_index()
        indexed = self._lyric_index.get(clean_lyric, [])
        if indexed:
            return indexed[0]

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
        for name in ("prefix.map", "Prefix.map"):
            path = os.path.join(voice_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                self._prefix_map = parse_prefix_map(self._read_safe(path))
                logger.info("prefix.map ロード完了 (%d 行): %s", len(self._prefix_map), path)
            except OSError as exc:
                logger.warning("prefix.map 読み込み失敗 (%s): %s", path, exc)
            break

    def clear(self) -> None:
        """ロード済み oto.ini データをすべて破棄する。"""
        self._db.clear()
        self._prefix_map = []
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
