"""Safe, cross-platform update notification patch for MainWindow."""
from __future__ import annotations

import logging
import os
import webbrowser

from PySide6.QtWidgets import QMessageBox

logger = logging.getLogger(__name__)


def install_update_notification_patch(main_window_class) -> None:
    """Install update methods before MainWindow schedules its startup timer."""
    if getattr(main_window_class, "_vose_update_notification_patch_installed", False):
        return

    def _check_for_updates(self) -> None:
        if os.environ.get("VOSE_SKIP_UPDATE_CHECK", "").lower() in {
            "1", "true", "yes", "on"
        }:
            return

        try:
            from modules.updater.update_checker import UpdateChecker

            checker = UpdateChecker(self)
            checker.result_ready.connect(self._on_update_result)
            self._update_checker = checker
            checker.start()
        except Exception:
            logger.debug("Could not start VO-SE update check", exc_info=True)

    def _on_update_result(
        self,
        has_update: bool,
        latest_version: str,
        page_url: str,
        _download_url: str,
    ) -> None:
        if not has_update or not page_url:
            return

        self._pending_update = (latest_version, page_url)
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, self._show_update_dialog)

    def _show_update_dialog(self) -> None:
        pending = getattr(self, "_pending_update", None)
        if not pending:
            return

        latest_version, page_url = pending
        reply = QMessageBox.question(
            self,
            "アップデートがあります",
            (
                f"VO-SE Pro の新しいビルド {latest_version} が公開されています。\n"
                "Releaseページを開いて、使用中のOSに合った最新版をダウンロードしますか？"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                opened = webbrowser.open(page_url)
                if not opened:
                    self.statusBar().showMessage(
                        "Releaseページをブラウザで開けませんでした。",
                        6000,
                    )
            except Exception:
                logger.warning("Could not open the VO-SE release page", exc_info=True)
                self.statusBar().showMessage(
                    "Releaseページを開けませんでした。ブラウザでGitHub Releasesを確認してください。",
                    7000,
                )

    # Replacing these class methods before construction is important: MainWindow
    # schedules _check_for_updates with a bound callback during __init__.
    main_window_class._check_for_updates = _check_for_updates
    main_window_class._on_update_result = _on_update_result
    main_window_class._show_update_dialog = _show_update_dialog
    main_window_class._vose_update_notification_patch_installed = True
    logger.info("VO-SE Release update notification patch installed")
