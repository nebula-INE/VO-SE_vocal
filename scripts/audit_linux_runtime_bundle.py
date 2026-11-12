#!/usr/bin/env python3
"""Produce a detailed, reproducible size/dependency report for a PyInstaller bundle.

This is diagnostic only: it never removes or rewrites files from the application bundle.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any


FORBIDDEN_MARKERS = (
    "torch/",
    "torch.",
    "torchaudio",
    "triton",
    "nvidia/",
    "transformers",
    "peft",
)

QT_LIBRARY_CANDIDATES = (
    "libqt6qml.so",
    "libqt6quick.so",
    "libqt6pdf.so",
    "libqt6virtualkeyboard.so",
)


def classify_entry(name: str) -> str:
    """Map an archive member to a diagnostic size category."""
    lowered = name.lower().replace("\\", "/")

    if lowered == "pyz.pyz":
        return "python_bytecode"
    if "onnxruntime" in lowered:
        return "onnx_runtime"
    if "pyopenjtalk" in lowered or "openjtalk" in lowered:
        return "japanese_speech"
    if "numpy" in lowered or "scipy" in lowered or "openblas" in lowered:
        return "numerical_python"
    if "libpython" in lowered:
        return "python_runtime"
    if "/qt/plugins/" in lowered or lowered.startswith("pyside6/qt/plugins/"):
        return "qt_plugins"
    if any(token in lowered for token in ("libavcodec", "libavformat", "libavutil", "libswresample", "libopus")):
        return "qt_multimedia_codecs"
    if "pyside6/qt/lib/" in lowered or lowered.startswith("pyside6/qt/"):
        return "qt_runtime"
    if "soundfile" in lowered or "libsndfile" in lowered or "sounddevice" in lowered:
        return "audio_io"
    if lowered.endswith((".so", ".dylib", ".dll")) or ".so." in lowered:
        return "native_other"
    return "python_and_resources"


def parse_needed_libraries(readelf_output: str) -> list[str]:
    """Extract DT_NEEDED library names from readelf -d output."""
    dependencies: list[str] = []
    for line in readelf_output.splitlines():
        if "(NEEDED)" not in line:
            continue
        match = re.search(r"Shared library: \[(.*?)\]", line)
        if match:
            dependencies.append(match.group(1))
    return dependencies


def calculate_reduction_percent(baseline_bytes: int, current_bytes: int) -> float | None:
    if baseline_bytes <= 0:
        return None
    return round((baseline_bytes - current_bytes) * 100.0 / baseline_bytes, 2)


def _is_elf_member(name: str, data: bytes) -> bool:
    lowered = name.lower()
    return (lowered.endswith(".so") or ".so." in lowered) and data.startswith(b"\x7fELF")


def _dependency_audit(archive: Any, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Find direct DT_NEEDED consumers of bundled Qt shared libraries.

    Lack of a DT_NEEDED edge does not prove a library/plugin is unused; Qt can
    load modules dynamically. The report explicitly preserves that caveat.
    """
    readelf = shutil.which("readelf")
    if not readelf:
        return {
            "available": False,
            "reason": "readelf is not installed; ELF dependency scan skipped",
            "qt_candidates": [],
            "scan_errors": [],
        }

    library_names = {
        Path(row["name"]).name
        for row in rows
        if row["name"].lower().endswith((".so", ".dylib", ".dll")) or ".so." in row["name"].lower()
    }
    consumers: dict[str, set[str]] = defaultdict(set)
    scan_errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="vose-runtime-audit-") as temp_dir:
        temp_root = Path(temp_dir)
        for index, row in enumerate(rows):
            name = row["name"]
            lowered_name = name.lower()
            if not (lowered_name.endswith((".so", ".dylib", ".dll")) or ".so." in lowered_name):
                continue
            try:
                data = archive.extract(name)
            except Exception as exc:  # Keep the report useful even if one entry cannot be decoded.
                scan_errors.append(f"{name}: extract failed: {type(exc).__name__}: {exc}")
                continue
            if not _is_elf_member(name, data):
                continue

            # Use a unique temporary name; archive members can share basenames.
            binary_path = temp_root / f"{index:04d}-{Path(name).name}"
            binary_path.write_bytes(data)
            result = subprocess.run(
                [readelf, "-d", "--wide", str(binary_path)],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                scan_errors.append(f"{name}: readelf exited {result.returncode}")
                continue
            for dependency in parse_needed_libraries(result.stdout):
                if dependency in library_names:
                    consumers[dependency].add(name)

    candidates = []
    for row in rows:
        name = row["name"]
        base = Path(name).name.lower()
        if any(base.startswith(candidate) for candidate in QT_LIBRARY_CANDIDATES):
            candidates.append(
                {
                    "name": name,
                    "packed_bytes": row["packed_bytes"],
                    "unpacked_bytes": row["unpacked_bytes"],
                    "direct_packaged_consumers": sorted(consumers.get(Path(name).name, set())),
                    "interpretation": (
                        "No internal DT_NEEDED consumer found; dynamic Qt loading is not ruled out."
                        if not consumers.get(Path(name).name)
                        else "At least one packaged ELF binary links against this library."
                    ),
                }
            )

    return {
        "available": True,
        "method": "readelf -d --wide over ELF members extracted from the PyInstaller CArchive",
        "qt_candidates": candidates,
        "scan_errors": scan_errors,
        "caveat": (
            "DT_NEEDED describes direct ELF link dependencies only. Qt plugins, QML imports, "
            "and application code may load libraries dynamically; do not exclude a candidate "
            "based only on an empty consumer list. Runtime tests remain required."
        ),
    }


def build_report(
    binary_path: Path,
    tarball_path: Path | None,
    baseline_tarball_bytes: int,
) -> dict[str, Any]:
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as exc:
        raise SystemExit("PyInstaller is required to inspect the executable archive") from exc

    archive = CArchiveReader(str(binary_path))
    rows: list[dict[str, Any]] = []
    for name, entry in archive.toc.items():
        try:
            _offset, packed_bytes, unpacked_bytes, _compressed, typecode = entry
            rows.append(
                {
                    "name": name,
                    "packed_bytes": int(packed_bytes),
                    "unpacked_bytes": int(unpacked_bytes),
                    "typecode": str(typecode),
                    "category": classify_entry(name),
                }
            )
        except (TypeError, ValueError):
            continue

    category_totals: dict[str, dict[str, int]] = {}
    for row in rows:
        totals = category_totals.setdefault(
            row["category"], {"entries": 0, "packed_bytes": 0, "unpacked_bytes": 0}
        )
        totals["entries"] += 1
        totals["packed_bytes"] += row["packed_bytes"]
        totals["unpacked_bytes"] += row["unpacked_bytes"]

    training_cuda_entries = [
        row for row in rows if any(marker in row["name"].lower() for marker in FORBIDDEN_MARKERS)
    ]
    current_tarball_bytes = tarball_path.stat().st_size if tarball_path else None

    return {
        "schema_version": 1,
        "binary": {
            "path": str(binary_path),
            "size_bytes": binary_path.stat().st_size,
        },
        "tarball": (
            {"path": str(tarball_path), "size_bytes": current_tarball_bytes}
            if tarball_path
            else None
        ),
        "baseline": {
            "tarball_size_bytes": baseline_tarball_bytes,
            "reduction_percent": (
                calculate_reduction_percent(baseline_tarball_bytes, current_tarball_bytes)
                if current_tarball_bytes is not None
                else None
            ),
            "baseline_note": (
                "PR #78 broad-collection Linux validation tarball; compare only as a validation-build "
                "reference, not as a published release asset."
            ),
        },
        "archive": {
            "entry_count": len(rows),
            "packed_entry_bytes_total": sum(row["packed_bytes"] for row in rows),
            "unpacked_entry_bytes_total": sum(row["unpacked_bytes"] for row in rows),
            "category_totals": dict(sorted(category_totals.items())),
            "largest_entries": sorted(
                rows,
                key=lambda row: (row["unpacked_bytes"], row["packed_bytes"]),
                reverse=True,
            )[:50],
            "entries": sorted(rows, key=lambda row: row["name"].lower()),
        },
        "training_cuda_entries": training_cuda_entries,
        "qt_elf_dependency_audit": _dependency_audit(archive, rows),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--tarball", type=Path)
    parser.add_argument("--json-output", type=Path, required=True)
    parser.add_argument("--baseline-tarball-bytes", type=int, default=401_936_365)
    args = parser.parse_args()

    if not args.binary.is_file():
        parser.error(f"PyInstaller executable does not exist: {args.binary}")
    if args.tarball is not None and not args.tarball.is_file():
        parser.error(f"tarball does not exist: {args.tarball}")

    report = build_report(args.binary, args.tarball, args.baseline_tarball_bytes)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"Executable bytes: {report['binary']['size_bytes']}")
    if report["tarball"]:
        print(f"Tarball bytes: {report['tarball']['size_bytes']}")
        reduction = report["baseline"]["reduction_percent"]
        if reduction is not None:
            print(f"Reduction vs validation baseline: {reduction:.2f}%")
    print(f"Archive entries: {report['archive']['entry_count']}")
    print("Category totals (packed / unpacked bytes):")
    for name, totals in report["archive"]["category_totals"].items():
        print(
            f"  {name}: {totals['packed_bytes']} / {totals['unpacked_bytes']} "
            f"({totals['entries']} entries)"
        )
    print("Largest embedded entries:")
    for row in report["archive"]["largest_entries"][:20]:
        print(
            f"  {row['unpacked_bytes']:>12} unpacked | {row['packed_bytes']:>12} packed | {row['name']}"
        )
    print(f"Qt ELF dependency audit available: {report['qt_elf_dependency_audit']['available']}")
    for candidate in report["qt_elf_dependency_audit"]["qt_candidates"]:
        print(
            f"  Qt candidate {candidate['name']}: "
            f"consumers={candidate['direct_packaged_consumers']}"
        )
    if report["training_cuda_entries"]:
        print("Unexpected training/CUDA archive entries:")
        for row in report["training_cuda_entries"]:
            print(f"  {row['name']}")
        return 1

    print(f"Detailed JSON report: {args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
