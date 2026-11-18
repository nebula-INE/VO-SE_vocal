"""Small, encoding-safe editor for individual UTAU oto.ini entries."""
from __future__ import annotations

import os
import tempfile

from modules.data.oto_parser import OtoEntry


def _decode_oto(data: bytes) -> tuple[str, str]:
    bom = bytes((0xEF, 0xBB, 0xBF))
    if data.startswith(bom):
        return data.decode("utf-8-sig"), "utf-8-sig"
    for encoding in ("utf-8", "cp932", "shift_jis"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("cp932", errors="replace"), "cp932"


def save_oto_entry(
    entry: OtoEntry,
    *,
    left_blank: float,
    fixed_range: float,
    right_blank: float,
    preutterance: float,
    overlap: float,
) -> bool:
    """Update exactly one filename/alias line, retaining other lines and encoding."""
    ini_path = os.path.join(entry.voice_dir, "oto.ini")
    if not os.path.isfile(ini_path):
        raise FileNotFoundError(f"oto.ini not found: {ini_path}")

    params = (left_blank, fixed_range, right_blank, preutterance, overlap)
    values = []
    for value in params:
        number = float(value)
        if not (-10000.0 <= number <= 10000.0):
            raise ValueError("oto.ini parameter is outside the supported range")
        values.append(f"{number:g}")

    with open(ini_path, "rb") as handle:
        raw = handle.read()
    text, encoding = _decode_oto(raw)
    newline = "\\r\\n" if "\\r\\n" in text else "\\n"
    lines = text.splitlines()
    target_filename = entry.filename.replace("\\\\", "/").casefold()
    updated = False

    for index, raw_line in enumerate(lines):
        if (
            not raw_line.strip()
            or raw_line.lstrip().startswith((";", "#"))
            or "=" not in raw_line
        ):
            continue
        filename, rhs = raw_line.split("=", 1)
        parts = rhs.split(",")
        alias = parts[0].strip() if parts else ""
        filename_matches = (
            filename.strip().replace("\\\\", "/").casefold() == target_filename
        )
        if not filename_matches or alias != entry.alias:
            continue
        lines[index] = f"{filename}={alias}," + ",".join(values)
        updated = True
        break

    if not updated:
        raise ValueError(f"oto.ini entry was not found: {entry.alias}")

    directory = os.path.dirname(ini_path)
    fd, temp_path = tempfile.mkstemp(prefix=".oto-", suffix=".tmp", dir=directory)
    try:
        try:
            os.chmod(temp_path, os.stat(ini_path).st_mode)
        except OSError:
            pass
        with os.fdopen(fd, "wb") as handle:
            rendered = newline.join(lines)
            if text.endswith(("\\n", "\\r")):
                rendered += newline
            handle.write(rendered.encode(encoding, errors="replace"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, ini_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
    return True
