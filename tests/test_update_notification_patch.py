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
