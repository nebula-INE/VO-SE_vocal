from __future__ import annotations

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
    import sys
    import types

    class FakeSignal:
        def __init__(self):
            self.callbacks = []

        def connect(self, callback):
            self.callbacks.append(callback)

    class FakeApplication:
        def __init__(self):
            self.aboutToQuit = FakeSignal()

    application = FakeApplication()

    class FakeQApplication:
        @staticmethod
        def instance():
            return application

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

    widgets = sys.modules["PySide6.QtWidgets"]
    monkeypatch.setattr(widgets, "QApplication", FakeQApplication)
    monkeypatch.setattr(
        "modules.updater.update_checker.UpdateChecker",
        FakeWorker,
    )
    monkeypatch.delenv("VOSE_SKIP_UPDATE_CHECK", raising=False)

    install_update_notification_patch(FakeWindow)
    window = FakeWindow()
    window._on_update_result = lambda *_args: None
    FakeWindow._check_for_updates(window)

    assert window._update_checker.parent is application
    assert window._update_checker.started is True
    assert len(application.aboutToQuit.callbacks) == 1
