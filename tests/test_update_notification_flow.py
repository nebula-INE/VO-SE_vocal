from __future__ import annotations

from PySide6.QtWidgets import QMessageBox

from modules.gui.update_notification_patch import install_update_notification_patch


def test_yes_on_update_prompt_starts_in_app_download(monkeypatch):
    started = []

    class FakeMainWindow:
        pass

    install_update_notification_patch(FakeMainWindow)
    window = FakeMainWindow()
    window._pending_update = (
        "VO-SE PRO build (abcdef0)",
        "https://github.com/nebula-INE/VO-SE_vocal/releases/tag/latest",
    )
    window._start_update_download = lambda: started.append(True)

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.Yes,
    )

    FakeMainWindow._show_update_dialog(window)

    assert started == [True]


def test_no_on_update_prompt_does_not_download(monkeypatch):
    started = []

    class FakeMainWindow:
        pass

    install_update_notification_patch(FakeMainWindow)
    window = FakeMainWindow()
    window._pending_update = (
        "VO-SE PRO build (abcdef0)",
        "https://github.com/nebula-INE/VO-SE_vocal/releases/tag/latest",
    )
    window._start_update_download = lambda: started.append(True)

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args, **_kwargs: QMessageBox.StandardButton.No,
    )

    FakeMainWindow._show_update_dialog(window)

    assert started == []


def test_update_result_schedules_dialog_only_for_new_build(monkeypatch):
    from PySide6.QtCore import QTimer

    class FakeMainWindow:
        pass

    install_update_notification_patch(FakeMainWindow)
    scheduled = []
    monkeypatch.setattr(
        QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )

    window = FakeMainWindow()
    FakeMainWindow._on_update_result(
        window,
        True,
        "VO-SE PRO build (abcdef0)",
        "https://github.com/nebula-INE/VO-SE_vocal/releases/tag/latest",
        "",
    )

    assert window._pending_update[0] == "VO-SE PRO build (abcdef0)"
    assert len(scheduled) == 1
    assert scheduled[0][0] == 0

    FakeMainWindow._on_update_result(window, False, "", "", "")

    assert len(scheduled) == 1
