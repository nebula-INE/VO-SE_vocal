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
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# System RAM check helper for 8GB or lower RAM environments
def get_system_ram_gb() -> float:
    try:
        import os
        page_size = os.sysconf('SC_PAGE_SIZE')
        
        pages = os.sysconf('SC_PHYS_PAGES') if hasattr(os, 'sysconf') else 0
        page_size = os.sysconf('SC_PAGE_SIZE') if hasattr(os, 'sysconf') else 0
        return (pages * page_size) / (1024 ** 3)
    except Exception:
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
                f_lower = f.lower()
                if f_lower not in file_map:
                    file_map[f_lower] = os.path.join(root, f)
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
        """フルパスで WAV へのパスを返す (大文字小文字表記ブレ・拡張子自動解決、高速化)"""
        exact_path = os.path.join(self.voice_dir, self.filename)
        if os.path.exists(exact_path):
            return exact_path

        # Case-insensitive & relative path resolution using cached file map
        target_lower = os.path.basename(self.filename).lower()
        file_map = get_voice_dir_file_map(self.voice_dir)

        if target_lower in file_map:
            return file_map[target_lower]
        if (target_lower + ".wav") in file_map:
            return file_map[target_lower + ".wav"]

        # Fallback to exact path
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

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def load_oto_file(self, ini_path: str) -> int:
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
                self._db[entry.alias] = entry
                count += 1

            # Low RAM throttle: pause slightly and collect garbage every 300 lines
            if IS_LOW_RAM and idx > 0 and idx % 300 == 0:
                time.sleep(0.003)
                if idx % 1200 == 0:
                    gc.collect()

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
        source_signature.sort(key=lambda item: item[0])

        # 2. Try loading from .oto_cache.json if valid
        if use_cache and os.path.exists(cache_path):
            try:
                import json
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)
                cached_signature = cached_data.get("source_signature")
                if cached_signature == source_signature:
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
                        self._db[entry.alias] = entry
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
            total += self.load_oto_file(ini_p)
            if IS_LOW_RAM:
                time.sleep(0.005)
                gc.collect()

        # 4. Save cache asynchronously / safely
        try:
            import json
            cache_entries = []
            for entry in self._db.values():
                cache_entries.append({
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

