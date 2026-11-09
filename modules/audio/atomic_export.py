"""Atomic file replacement helpers for rendered WAV output.

The render engine writes only to a temporary file in the destination directory.
The user's previous export is replaced only after rendering has completed and the
renderer has returned a valid file. Keeping the temporary file beside the target
also keeps os.replace() on the same filesystem.
"""
from __future__ import annotations

import os
import tempfile
from typing import Callable


def render_wav_atomically(
    destination: str | os.PathLike[str],
    render_to_path: Callable[[str], str | os.PathLike[str] | None],
) -> str:
    """Render to a unique sibling file, then atomically replace destination.

    A failed renderer, missing/invalid result path, or failed replace leaves an
    existing destination untouched. Temporary files are removed on every exit.
    """
    target = os.path.abspath(os.fsdecode(os.fspath(destination)))
    output_dir = os.path.dirname(target) or os.getcwd()
    os.makedirs(output_dir, exist_ok=True)

    basename = os.path.basename(target)
    stem, extension = os.path.splitext(basename)
    suffix = f".tmp{extension or '.wav'}"
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{stem}.",
        suffix=suffix,
        dir=output_dir,
    )
    os.close(descriptor)

    try:
        os.remove(temporary_path)
        result = render_to_path(temporary_path)
        if result is None:
            raise RuntimeError("レンダリング結果のWAVパスが返されませんでした。")

        result_path = os.path.abspath(os.fsdecode(os.fspath(result)))
        if result_path != os.path.abspath(temporary_path):
            raise RuntimeError("レンダラーが指定した一時WAV以外のパスを返しました。")
        if not os.path.isfile(temporary_path):
            raise RuntimeError("レンダリング結果の一時WAVが生成されませんでした。")

        os.replace(temporary_path, target)
        return target
    finally:
        try:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)
        except OSError:
            # Do not mask the original render/replace exception during cleanup.
            pass
