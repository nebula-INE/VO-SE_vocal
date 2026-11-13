from __future__ import annotations

import pytest

from modules.gui import update_notification_patch
from modules.gui.update_notification_patch import install_update_notification_patch


def test_update_notification_patch_is_installed_before_window_creation():
    class FakeMainWindow:
        def _check_for_updates(self):
            raise AssertionError("the original inactive updater must be replaced")

    install_update_notification_patch(FakeMainWindow)

    patched_method = FakeMainWindow._check_for_updates
    assert patched_method is not None
    assert getattr(FakeMainWindow, "_vose_update_notification_patch_installed", False)

    # Multiple launchers/import paths may call the installer; it must be idempotent.
    install_update_notification_patch(FakeMainWindow)
    assert FakeMainWindow._check_for_updates is patched_method


def test_update_thread_is_owned_by_application_not_window(monkeypatch):
    class FakeSignal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

    class FakeApplication:
        def __init__(self):
            self.aboutToQuit = FakeSignal()

    application = FakeApplication()

    class FakeWorker:
        def __init__(self, parent):
            self.parent = parent
            self.result_ready = FakeSignal()
            self.started = False

        def start(self):
            self.started = True

        def wait(self):
            return True

    class FakeWindow:
        def statusBar(self):
            return None

    monkeypatch.setattr(
        update_notification_patch,
        "_get_application_instance",
        lambda: application,
    )
    monkeypatch.setattr(
        "modules.updater.update_checker.UpdateChecker",
        FakeWorker,
    )
    monkeypatch.delenv("VOSE_SKIP_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("VOSE_STARTUP_SMOKE_TEST", raising=False)

    install_update_notification_patch(FakeWindow)
    window = FakeWindow()
    window._on_update_result = lambda *_args: None
    FakeWindow._check_for_updates(window)

    assert window._update_checker.parent is application
    assert window._update_checker.started is True
    assert len(application.aboutToQuit.callbacks) == 1


@pytest.mark.parametrize(
    ("skip_flag", "skip_value"),
    [
        ("VOSE_STARTUP_SMOKE_TEST", "1"),
        ("VOSE_SKIP_UPDATE_CHECK", "true"),
    ],
)
def test_update_check_is_skipped_when_suppressed(monkeypatch, skip_flag, skip_value):
    from modules.updater import update_checker

    class FakeWindow:
        pass

    worker_constructions = []

    def unexpected_worker(parent):
        worker_constructions.append(parent)
        raise RuntimeError("suppressed checks must not start the updater")

    monkeypatch.setattr(update_checker, "UpdateChecker", unexpected_worker)
    monkeypatch.delenv("VOSE_STARTUP_SMOKE_TEST", raising=False)
    monkeypatch.delenv("VOSE_SKIP_UPDATE_CHECK", raising=False)
    monkeypatch.setenv(skip_flag, skip_value)

    install_update_notification_patch(FakeWindow)
    window = FakeWindow()
    window._on_update_result = lambda *_args: None
    FakeWindow._check_for_updates(window)

    assert worker_constructions == []
    assert not hasattr(window, "_update_checker")
