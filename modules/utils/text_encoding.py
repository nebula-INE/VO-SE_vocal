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

import os
import ntpath
import shutil
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
    """UTF-8 フラグの無い名前をまとめて見て、ZIP 全体のエンコーディングを決める。

    1 つの名前だけで判断すると偶然の一致で誤判定するため、ZIP 内の
    「非 ASCII でフラグ無しの名前」がすべて同じエンコーディングで読めるかを確かめる。
    戻り値: "utf-8" | "cp932" | "cp437"（= zipfile の読みをそのまま使う）
    """
    raws = []
    for info in infos:
        if info.flag_bits & 0x800 or info.filename.isascii():
            continue
        try:
            raws.append(info.filename.encode("cp437"))
        except UnicodeEncodeError:
            return "cp437"       # すでに正しい文字列として読めている
    if not raws:
        return "cp437"
    for encoding in ("utf-8", "cp932"):
        try:
            decoded = [r.decode(encoding) for r in raws]
        except UnicodeDecodeError:
            continue
        if any(_has_japanese(d) for d in decoded):
            return encoding
    return "cp437"


def decode_zip_member_names(infos) -> List[str]:
    """ZIP メンバーの名前を、正しい日本語に直して返す（infos と同じ順序）。"""
    infos = list(infos)
    encoding = detect_zip_name_encoding(infos)
    names = []
    for info in infos:
        if info.flag_bits & 0x800 or encoding == "cp437" or info.filename.isascii():
            names.append(info.filename)       # UTF-8 フラグ付き / 判定不能 / ASCII のみ
        else:
            names.append(info.filename.encode("cp437").decode(encoding))
    return names


def safe_extract_zip(
    zf: zipfile.ZipFile,
    dest_dir: str,
    progress: Optional[Callable[[int], None]] = None,
    *,
    max_total_size: int = 8 * 1024**3,
    max_member_size: int = 2 * 1024**3,
    max_members: int = 100_000,
) -> List[str]:
    """文字化けを直しつつ、dest_dir の外へ書き出さないように ZIP を展開する。

    不正なパス（絶対パス・`..` で外へ出るもの）は ValueError。
    戻り値: 展開したファイルの相対パス（正しい名前）の一覧。
    """
    root = os.path.realpath(dest_dir)
    os.makedirs(root, exist_ok=True)

    if max_total_size <= 0 or max_member_size <= 0 or max_members <= 0:
        raise ValueError("ZIP 展開上限は正の整数で指定してください。")

    infos = zf.infolist()
    if len(infos) > max_members:
        raise ValueError(
            f"ZIP内のファイル数が上限を超えています: {len(infos)} > {max_members}"
        )

    # Validate the complete archive before creating any extracted files. This
    # prevents a late oversized member from leaving a partially installed bank.
    total_size = 0
    for info in infos:
        if info.file_size < 0 or info.file_size > max_member_size:
            raise ValueError(
                f"ZIP内のファイルサイズが上限を超えています: "
                f"{info.filename} ({info.file_size} bytes)"
            )
        total_size += info.file_size
        if total_size > max_total_size:
            raise ValueError(
                f"ZIP展開後の合計サイズが上限を超えています: "
                f"{total_size} > {max_total_size} bytes"
            )

    names = decode_zip_member_names(infos)
    total = total_size or 1
    done = 0
    extracted: List[str] = []

    for info, decoded_name in zip(infos, names):
        # ZIP名は通常POSIX形式だが、Windows形式の区切りも正規化する。
        # ntpathで検査することで、Linux/macOS上でもWindowsドライブ指定を拒否する。
        name = decoded_name.replace("\\", "/")
        if "__MACOSX/" in name or name.endswith(".DS_Store"):
            continue
        drive, _ = ntpath.splitdrive(name)
        if (
            drive
            or ntpath.isabs(name)
            or os.path.isabs(name)
            or name.startswith("/")
        ):
            raise ValueError(f"不正なZIPパスです: {info.filename}")
        destination = os.path.realpath(os.path.join(root, name))
        try:
            inside = os.path.commonpath([root, destination]) == root
        except ValueError:            # ドライブが違う等
            inside = False
        if not inside:
            raise ValueError(f"ZIP展開先が対象フォルダ外です: {info.filename}")

        if info.is_dir() or name.endswith("/"):
            os.makedirs(destination, exist_ok=True)
            continue

        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with zf.open(info) as source, open(destination, "wb") as target:
            shutil.copyfileobj(source, target)
        extracted.append(name)
        done += info.file_size
        if progress is not None:
            progress(min(100, int(done * 100 / total)))

    return extracted


def repair_mojibake_tree(root: str) -> List[Tuple[str, str]]:
    """root 直下の各音源フォルダについて、文字化け(CP437 誤読)した名前を直してリネームする。

    以前のバージョンで取り込んだ（すでに化けた名前で保存された）音源を直すためのもの。

    誤判定でユーザーのフォルダ名を書き換えないよう、音源フォルダ（root 直下の 1 つ）ごとに判断する。
      * そのフォルダ内に「はっきり化けている」名前（日本語が 2 文字以上で半分以上）が
        1 つでもあれば、そのフォルダ内の名前をすべて直す
        （"a か.wav" のように ASCII が混ざる名前も一緒に直すため）
      * 1 つも無ければ（欧文の正しい名前など）何もしない
    同名が既にある場合は上書きせずそのまま残す。戻り値は (旧パス, 新パス) の一覧。
    """
    renamed: List[Tuple[str, str]] = []
    if not root or not os.path.isdir(root):
        return renamed

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

    for entry in sorted(os.listdir(root)):
        entry_path = os.path.join(root, entry)
        if not os.path.isdir(entry_path):
            _rename(root, entry, strict=True)
            continue

        walked = list(os.walk(entry_path, topdown=False))
        names = [entry] + [n for _d, dirs, files in walked for n in dirs + files]
        if not any(fix_cp437_mojibake(n, strict=True) != n for n in names):
            continue           # この音源フォルダには化けた名前が無い
        for dirpath, dirnames, filenames in walked:     # 深い方から先に直す
            for name in filenames + dirnames:
                _rename(dirpath, name, strict=False)
        _rename(root, entry, strict=False)

    return renamed
