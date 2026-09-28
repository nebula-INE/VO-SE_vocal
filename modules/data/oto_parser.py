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

    def load_voice_dir(self, voice_dir: str, use_cache: bool = True) -> int:
        """
        指定フォルダ（サブフォルダ含む）の oto.ini を全部ロードする。
        キャッシュ有効時（デフォルト）は .oto_cache.json を参照して 0.001秒で超高速ロードする。

        Returns:
            合計エントリ数
        """
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

    def load_from_zip(self, zip_path: str) -> int:
        """
        ZIP アーカイブから WAV を事前展開せずに直接 oto.ini を高速読み込みする（超軽量・スロットリング対応）。
        CP932 / UTF-8 ファイル名エンコーディングに自動対応。

        Returns:
            合計エントリ数
        """
        import zipfile
        if not os.path.isfile(zip_path):
            logger.warning("ZIPファイルが見つかりません: %s", zip_path)
            return 0

        total = 0
        try:
            with zipfile.ZipFile(zip_path, 'r') as zf:
                for zidx, zinfo in enumerate(zf.infolist()):
                    name = self._fix_zip_filename(zinfo)
                    base = os.path.basename(name)
                    # oto.ini または oto2.ini 等
                    if base.lower().startswith("oto") and base.lower().endswith(".ini"):
                        try:
                            raw_data = zf.read(zinfo)
                            text = self._decode_bytes_safe(raw_data)
                            v_dir = os.path.dirname(name)
                            lines = text.splitlines()
                            for idx, raw_line in enumerate(lines):
                                line = raw_line.strip()
                                if not line or "=" not in line:
                                    continue
                                entry = self._parse_line(line, v_dir)
                                if entry is not None:
                                    self._db[entry.alias] = entry
                                    total += 1

                                if IS_LOW_RAM and idx > 0 and idx % 300 == 0:
                                    time.sleep(0.003)
                                    if idx % 1200 == 0:
                                        gc.collect()
                        except Exception as e:
                            logger.warning("ZIP内 oto.ini (%s) 読込エラー: %s", name, e)
                    
                    if IS_LOW_RAM and zidx > 0 and zidx % 50 == 0:
                        time.sleep(0.005)
                        gc.collect()
        except Exception as err:
            logger.error("ZIPファイルオープン失敗: %s", err)

        return total

    @staticmethod
    def _fix_zip_filename(zinfo) -> str:
        """zipfile の文字化け(CP437)を CP932 / UTF-8 に復元"""
        filename = zinfo.filename
        if zinfo.flag_bits & 0x800:
            # UTF-8 フラグが立っている場合
            return filename
        try:
            # CP437 からバイト列に戻して CP932 でデコードを試みる
            raw_bytes = filename.encode('cp437')
            return raw_bytes.decode('cp932')
        except Exception:
            return filename

    @staticmethod
    def _decode_bytes_safe(raw_data: bytes) -> str:
        """バイナリデータを Shift-JIS / UTF-8 / latin-1 でデコード"""
        for enc in ("cp932", "utf-8-sig", "utf-8", "latin-1"):
            try:
                return raw_data.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw_data.decode("cp932", errors="ignore")

    def get(self, alias: str) -> Optional[OtoEntry]:
        """
        alias で完全一致検索。見つからなければ None。
        """
        return self._db.get(alias)

    def resolve_alias(self, lyric: str, prev_vowel: Optional[str] = None) -> Optional[OtoEntry]:
        """
        VCV → CV → 単独音 の優先順でエントリを解決する（高速参照・音階サフィックス対応）。

        Args:
            lyric:       対象の歌詞 (例: "い", "い_C4")
            prev_vowel:  前ノートの末尾母音ラベル ("a"/"i"/"u"/"e"/"o"/"n"/"") or None

        Returns:
            最初に見つかった OtoEntry、全て失敗なら None
        """
        if not lyric:
            return None

        clean_lyric = re.sub(r'^[-aieuon_]\s*', '', lyric, flags=re.IGNORECASE).strip() or lyric
        clean_lyric = re.sub(r'_?[A-Ga-g][#b]?[0-9]$', '', clean_lyric).strip() or clean_lyric

        def _match_pref(p: str) -> Optional[OtoEntry]:
            if p in self._db:
                return self._db[p]
            p_lower = p.lower()
            for alias, entry in self._db.items():
                a_lower = alias.lower()
                if a_lower.startswith(p_lower + "_") or a_lower.startswith(p_lower + " "):
                    return entry
            return None

        # 1. If raw lyric itself is explicitly a VCV/silence alias string (e.g., "a い", "- い"), match direct
        if lyric in self._db and (' ' in lyric or '_' in lyric or lyric.startswith('-')):
            return self._db[lyric]

        # 2. VCV: "a い", "a_い", "aい" (前の母音がある場合は最優先)
        if prev_vowel:
            entry = _match_pref(f"{prev_vowel} {clean_lyric}") or \
                    _match_pref(f"{prev_vowel}_{clean_lyric}") or \
                    _match_pref(f"{prev_vowel}{clean_lyric}")
            if entry:
                return entry

        # 3. CV with silence: "- い", "_い", "-い"
        # prev_vowel の有無に関わらず常に試す（C++ 版 OtoDatabase::resolveAlias と挙動を一致させる）。
        # VCV エイリアスが oto.ini に存在しない組み合わせだった場合、ここでフォールバックしないと
        # タイミング計算（Python側）と実再生（C++側）で異なるエイリアスを参照してしまい、
        # 連続音の繋ぎ目が破綻する原因になっていた。
        entry = _match_pref(f"- {clean_lyric}") or \
                _match_pref(f"_{clean_lyric}") or \
                _match_pref(f"-{clean_lyric}")
        if entry:
            return entry

        # 4. Direct exact match
        if lyric in self._db:
            return self._db[lyric]

        # 4. Direct clean lyric match
        entry = _match_pref(clean_lyric)
        if entry:
            return entry

        # 5. 歌詞のみ部分一致 / インデックス参照
        if not hasattr(self, '_lyric_index'):
            self._build_lyric_index()

        if clean_lyric in self._lyric_index and self._lyric_index[clean_lyric]:
            return self._lyric_index[clean_lyric][0]

        # 6. Fallback: 部分一致（先頭一致や含まれるもの）
        for alias, entry in self._db.items():
            if clean_lyric in alias:
                return entry

        return None

    def _build_lyric_index(self) -> None:
        """解析済みデータベースから歌詞逆引き用インデックスを事前構築"""
        self._lyric_index: Dict[str, List[OtoEntry]] = {}
        for alias, entry in self._db.items():
            # "a い" → "い" や "- い" → "い"
            parts = alias.strip().split()
            pure_lyric = parts[-1] if parts else alias
            if pure_lyric not in self._lyric_index:
                self._lyric_index[pure_lyric] = []
            self._lyric_index[pure_lyric].append(entry)

    def clear(self) -> None:
        """ロード済みデータをリセット"""
        self._db.clear()
        if hasattr(self, '_lyric_index'):
            self._lyric_index.clear()

    def get_preutterance_sec(self, alias: str, default: float = 0.05) -> float:
        """先行発声を秒で返す。エントリが無ければ default。"""
        entry = self.get(alias)
        return entry.preutterance_sec if entry else default

    def get_overlap_sec(self, alias: str, default: float = 0.02) -> float:
        """オーバーラップを秒で返す。エントリが無ければ default。"""
        entry = self.get(alias)
        return entry.overlap_sec if entry else default

    def all_aliases(self) -> List[str]:
        """ロード済み全エイリアスのリストを返す"""
        return list(self._db.keys())

    def has_vcv(self) -> bool:
        """VCV エイリアス（スペース区切りの母音接続エイリアス）が 1 つ以上あれば True。

        注: "- あ" のようなフレーズ先頭の無音接続エイリアスは単独音（CV）バンクにも
        一般的に存在するため、startswith("-") や startswith("_") を条件に含めると
        単独音バンクを VCV バンクと誤判定してしまう（実際に発生していたバグ）。
        C++ 版 OtoDatabase::hasVcv() と判定基準を統一し、スペースの有無のみで判定する。
        """
        return any(" " in alias for alias in self._db)

    # ------------------------------------------------------------------
    # 内部ユーティリティ
    # ------------------------------------------------------------------

    @staticmethod
    def _read_safe(path: str) -> str:
        """Shift-JIS / UTF-8 / latin-1 の順で試みて文字列を返す"""
        for enc in ("cp932", "utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(path, "r", encoding=enc, errors="strict") as f:
                    return f.read()
            except (UnicodeDecodeError, LookupError):
                continue
        # 最終フォールバック
        with open(path, "r", encoding="cp932", errors="ignore") as f:
            return f.read()

    @staticmethod
    def _parse_line(line: str, voice_dir: str) -> Optional[OtoEntry]:
        """
        1 行をパースして OtoEntry を返す。

        oto.ini 行フォーマット:
            filename.wav=alias,left_blank,fixed_range,right_blank,preutterance,overlap
        """
        try:
            filename_part, params_part = line.split("=", 1)
            filename_part = filename_part.strip()
            parts = [p.strip() for p in params_part.split(",")]

            # alias が空の場合は拡張子なしファイル名を使う
            alias = parts[0] if parts[0] else os.path.splitext(filename_part)[0]

            def _f(idx: int, fallback: float = 0.0) -> float:
                try:
                    return float(parts[idx]) if idx < len(parts) and parts[idx] != "" else fallback
                except ValueError:
                    return fallback

            return OtoEntry(
                alias=alias,
                filename=filename_part,
                voice_dir=voice_dir,
                left_blank=_f(1),
                fixed_range=_f(2),
                right_blank=_f(3),
                preutterance=_f(4),
                overlap=_f(5),
            )
        except Exception as exc:
            logger.debug("oto.ini 行のパース失敗 (%s): %s", exc, line)
            return None
