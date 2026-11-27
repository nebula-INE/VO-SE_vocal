# modules/utils/text_encoding.py
"""
ZIP 内の日本語ファイル名の文字化け対策。

背景
----
Windows のエクスプローラーや UTAU 系ツールで作った ZIP は、ファイル名を
Shift-JIS (CP932) のバイト列で保存し、「UTF-8 フラグ」(general purpose bit 11) を
立てない。Python の zipfile はフラグが無い名前を CP437 として読むため、

    「あ.wav」(CP932 のバイト列 82 A0 ...)  →  「éá.wav」のような別の文字列

になる。これをそのまま展開すると、フォルダ名が化けるだけでなく、wav のファイル名が
oto.ini（CP932 で書かれた日本語名）と一致しなくなり、音源として使えなくなる。

このモジュールは
  * ZIP のメンバー名を正しい日本語へ戻す        (decode_zip_member_name)
  * 安全に展開する（パストラバーサル対策込み）    (safe_extract_zip)
  * すでに化けた名前で展開されたフォルダを直す    (repair_mojibake_tree)
を提供する。
"""
from __future__ import annotations

import errno
import logging
import os
import ntpath
import shutil
import tempfile
import zipfile
from typing import Callable, List, Optional, Tuple


def _has_japanese(text: str) -> bool:
    """ひらがな・カタカナ・漢字・全角記号のいずれかを含むか。"""
    for ch in text:
        o = ord(ch)
        if (0x3040 <= o <= 0x30FF       # ひらがな・カタカナ
                or 0x3400 <= o <= 0x4DBF   # CJK 拡張A
                or 0x4E00 <= o <= 0x9FFF   # CJK 統合漢字
                or 0xFF00 <= o <= 0xFFEF   # 全角・半角形
                or 0x3000 <= o <= 0x303F):  # CJK 記号
            return True
    return False


def _japanese_stats(text: str) -> Tuple[int, float]:
    """(日本語の文字数, 全体に占める割合)"""
    if not text:
        return 0, 0.0
    n = sum(1 for ch in text if _has_japanese(ch))
    return n, n / len(text)


def _reinterpret(text: str, encodings=("utf-8", "cp932")) -> Optional[str]:
    """CP437 として誤読された文字列を、別のエンコーディングで読み直す（失敗なら None）。"""
    try:
        raw = text.encode("cp437")
    except UnicodeEncodeError:
        return None          # CP437 に無い文字を含む = 化けではない（正しい日本語など）
    # UTF-8 を先に試す。Shift-JIS の先頭バイト(0x81-0x9F)は UTF-8 としては不正なので、
    # 日本語名が UTF-8 と誤判定されることはまず無い。
    for encoding in encodings:
        try:
            fixed = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if fixed != text and _has_japanese(fixed):
            return fixed
    return None


def fix_cp437_mojibake(text: str, strict: bool = False) -> str:
    """CP437 として誤読された文字列を、UTF-8 または CP932 として読み直して返す。

    直せるのは「日本語として読み直せた」場合だけで、それ以外（ASCII のみ、
    正しい日本語など）は元の文字列をそのまま返す。

    strict=True のときは、欧文の正しい名前（"Ünïcode" など。CP932 として読むと
    偶然「嗜議ode」のように読めてしまう）を誤って直さないよう、
    「日本語が 2 文字以上で、全体の半分以上を占める」場合だけ直す。
    """
    if not text or text.isascii():
        return text
    fixed = _reinterpret(text)
    if fixed is None:
        return text
    if strict:
        n, ratio = _japanese_stats(fixed)
        if n < 2 or ratio < 0.5:
            return text
    return fixed


def detect_zip_name_encoding(infos) -> str:
    """Inspect legacy member names without retaining duplicate byte/string lists."""
    for encoding in ("utf-8", "cp932"):
        saw_japanese = False
        valid = True
        for info in infos:
            if info.flag_bits & 0x800 or info.filename.isascii():
                continue
            try:
                decoded = info.filename.encode("cp437").decode(encoding)
            except (UnicodeEncodeError, UnicodeDecodeError):
                valid = False
                break
            saw_japanese = saw_japanese or _has_japanese(decoded)
        if valid and saw_japanese:
            return encoding
    return "cp437"


def _decode_zip_member_name(info: zipfile.ZipInfo, encoding: str) -> str:
    if info.flag_bits & 0x800 or encoding == "cp437" or info.filename.isascii():
        return info.filename
    return info.filename.encode("cp437").decode(encoding)


def _zip_member_target(root: str, info: zipfile.ZipInfo, encoding: str):
    """Return a validated destination, or None for harmless OS metadata files."""
    name = _decode_zip_member_name(info, encoding).replace(chr(92), "/")
    if "__MACOSX/" in name or name.endswith(".DS_Store"):
        return None

    drive, _ = ntpath.splitdrive(name)
    if drive or ntpath.isabs(name) or os.path.isabs(name) or name.startswith("/"):
        raise ValueError(f"不正なZIPパスです: {info.filename}")

    destination = os.path.realpath(os.path.join(root, name))
    try:
        inside = os.path.commonpath([root, destination]) == root
    except ValueError:
        inside = False
    if not inside:
        raise ValueError(f"ZIP展開先が対象フォルダ外です: {info.filename}")
    return name, destination


def decode_zip_member_names(infos) -> List[str]:
    """ZIP メンバー名を CP437/UTF-8/CP932 判定に従って復元する。"""
    infos = list(infos)
    encoding = detect_zip_name_encoding(infos)
    return [_decode_zip_member_name(info, encoding) for info in infos]


def safe_extract_zip(
    zf: zipfile.ZipFile,
    dest_dir: str,
    progress: Optional[Callable[[int], None]] = None,
    *,
    max_total_size: Optional[int] = None,
    max_member_size: Optional[int] = None,
    max_members: Optional[int] = None,
    cancel_event=None,
    collect_paths: bool = True,
) -> List[str]:
    """Extract a ZIP incrementally without arbitrary default size/count ceilings.

    Optional max_* arguments remain available for callers that intentionally need
    an application-specific policy. By default, the archive is constrained by the
    actual free space on the destination volume, not a hard-coded byte/member cap.

    Path traversal and absolute paths are always rejected. File data is copied
    through a temporary sibling file so an interrupted member is never left as a
    partially written destination.
    """
    root = os.path.realpath(dest_dir)
    os.makedirs(root, exist_ok=True)

    for label, limit in (
        ("max_total_size", max_total_size),
        ("max_member_size", max_member_size),
        ("max_members", max_members),
    ):
        if limit is not None and limit <= 0:
            raise ValueError(f"{label} は正の整数で指定してください。")

    # zipfile reads the central directory into ZipInfo objects when opening the
    # archive. We do not add another member-count ceiling or retain a second
    # decoded-name list alongside those objects.
    infos = zf.infolist()
    if max_members is not None and len(infos) > max_members:
        raise ValueError(
            f"ZIP内のファイル数が指定上限を超えています: {len(infos)} > {max_members}"
        )

    encoding = detect_zip_name_encoding(infos)
    total_size = 0

    # Validate every member path and calculate actual payload size before writing.
    # This prevents a late path traversal or insufficient-space failure from
    # leaving a partially installed archive.
    for info in infos:
        if info.file_size < 0:
            raise ValueError(f"ZIP内に不正なファイルサイズがあります: {info.filename}")
        target = _zip_member_target(root, info, encoding)
        if target is None or info.is_dir():
            continue
        if max_member_size is not None and info.file_size > max_member_size:
            raise ValueError(
                f"ZIP内の単一ファイルサイズが指定上限を超えています: "
                f"{info.filename} ({info.file_size} bytes)"
            )
        total_size += info.file_size
        if max_total_size is not None and total_size > max_total_size:
            raise ValueError(
                f"ZIP展開後の合計サイズが指定上限を超えています: "
                f"{total_size} > {max_total_size} bytes"
            )

    try:
        free_space = shutil.disk_usage(root).free
    except OSError:
        # Some virtual/network filesystems do not report quota data reliably.
        # Continue and let the write fail cleanly if the volume is full.
        free_space = None
    if free_space is not None and total_size > free_space:
        raise OSError(
            "ZIPを展開する空き容量が不足しています "
            f"(必要 {total_size:,} bytes / 空き {free_space:,} bytes)。"
        )

    total = total_size or 1
    done = 0
    extracted: List[str] = []

    for info in infos:
        if cancel_event is not None and cancel_event.is_set():
            raise RuntimeError("ZIP展開はキャンセルされました。")

        target_info = _zip_member_target(root, info, encoding)
        if target_info is None:
            continue
        name, destination = target_info

        if info.is_dir() or name.endswith("/"):
            os.makedirs(destination, exist_ok=True)
            continue

        parent = os.path.dirname(destination)
        os.makedirs(parent, exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(
            prefix=".vose-extract-",
            suffix=".part",
            dir=parent,
        )
        try:
            with zf.open(info) as source, os.fdopen(fd, "wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            os.replace(temporary_path, destination)
        except OSError as exc:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                if os.path.exists(temporary_path):
                    os.remove(temporary_path)
            except OSError:
                pass
            if exc.errno == errno.ENOSPC:
                raise OSError(
                    "ZIP展開中に保存先の空き容量が不足しました。"
                ) from exc
            raise
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                if os.path.exists(temporary_path):
                    os.remove(temporary_path)
            except OSError:
                pass
            raise

        if collect_paths:
            extracted.append(name)
        done += info.file_size
        if progress is not None:
            progress(min(100, int(done * 100 / total)))

    return extracted

def repair_mojibake_tree(
    root: str,
    cancel_event=None,
    max_entries: Optional[int] = None,
) -> List[Tuple[str, str]]:
    """Repair garbled names with bounded two-pass traversal and cooperative cancellation."""
    renamed: List[Tuple[str, str]] = []
    if not root or not os.path.isdir(root):
        return renamed

    def _cancelled() -> bool:
        return bool(cancel_event is not None and cancel_event.is_set())

    def _rename(dirpath: str, name: str, strict: bool) -> None:
        fixed = fix_cp437_mojibake(name, strict=strict)
        if fixed == name:
            return
        old, new = os.path.join(dirpath, name), os.path.join(dirpath, fixed)
        if os.path.exists(new):
            return
        try:
            os.rename(old, new)
        except OSError:
            return
        renamed.append((old, new))

    try:
        with os.scandir(root) as root_entries:
            for entry in root_entries:
                if _cancelled():
                    break
                if entry.is_symlink():
                    continue
                if not entry.is_dir(follow_symlinks=False):
                    _rename(root, entry.name, strict=True)
                    continue

                # Pass 1: stop as soon as one clearly garbled child is found.
                contains_mojibake = fix_cp437_mojibake(entry.name, strict=True) != entry.name
                scan_complete = True
                scanned = 0
                if not contains_mojibake:
                    for dirpath, dirnames, filenames in os.walk(
                        entry.path,
                        topdown=True,
                        followlinks=False,
                    ):
                        if _cancelled():
                            scan_complete = False
                            break
                        dirnames[:] = [
                            dirname for dirname in dirnames
                            if not os.path.islink(os.path.join(dirpath, dirname))
                        ]
                        for child_name in dirnames + filenames:
                            scanned += 1
                            if max_entries is not None and scanned > max_entries:
                                scan_complete = False
                                break
                            if fix_cp437_mojibake(child_name, strict=True) != child_name:
                                contains_mojibake = True
                                break
                        if contains_mojibake or not scan_complete:
                            break

                if not scan_complete:
                    logging.warning(
                        "文字化け修復の検査をキャンセルまたは上限で停止しました: %s",
                        entry.path,
                    )
                    if _cancelled():
                        break
                    continue
                if not contains_mojibake:
                    continue

                # Pass 2: rename bottom-up without retaining os.walk results.
                renamed_scan = 0
                rename_complete = True
                for dirpath, dirnames, filenames in os.walk(
                    entry.path,
                    topdown=False,
                    followlinks=False,
                ):
                    if _cancelled():
                        rename_complete = False
                        break
                    for child_name in filenames + dirnames:
                        renamed_scan += 1
                        if max_entries is not None and renamed_scan > max_entries:
                            rename_complete = False
                            break
                        if not os.path.islink(os.path.join(dirpath, child_name)):
                            _rename(dirpath, child_name, strict=False)
                    if not rename_complete:
                        break

                if rename_complete and not _cancelled():
                    _rename(root, entry.name, strict=False)
                else:
                    logging.warning(
                        "文字化け修復を途中で停止しました。再スキャンで残りを修復できます: %s",
                        entry.path,
                    )
                if _cancelled():
                    break
    except OSError as exc:
        logging.warning("文字化け修復をスキップしました (%s): %s", root, exc)

    return renamed
