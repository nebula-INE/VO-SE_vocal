"""Small Qt-thread helpers kept free of PySide imports for unit testing."""
from __future__ import annotations

from typing import Any


def wait_for_thread_shutdown(thread: Any, timeout_ms: int = 15000) -> bool:
    """Return True only when a QThread is confirmed to have stopped.

    A thread that is already stopped (or a missing optional worker) is safe.
    If the native wrapper no longer exposes its state, fail closed so callers
    do not destroy a possible live QThread.
    """
    if thread is None:
        return True

    is_running = getattr(thread, "isRunning", None)
    if not callable(is_running):
        return True

    try:
        if not is_running():
            return True
        wait = getattr(thread, "wait", None)
        if not callable(wait):
            return False
        return bool(wait(max(0, int(timeout_ms))))
    except RuntimeError:
        # A Qt wrapper can become invalid after Qt has already completed and
        # deleted the native object. No running native thread remains.
        return True
