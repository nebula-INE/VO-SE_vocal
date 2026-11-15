"""Safe, cross-platform in-app update flow for MainWindow."""
from __future__ import annotations

import logging
import os
import webbrowser

from PySide6.QtWidgets import QMessageBox

logger = logging.getLogger(__name__)


def _get_application_instance():
    """Return QApplication without exposing the global Qt class to test patches."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance()


def install_update_notification_patch(main_window_class) -> None:
    """Install update methods before MainWindow schedules its startup timer."""
    if getattr(main_window_class, "_vose_update_notification_patch_installed", False):
        return

    def _check_for_updates(self) -> None:
        enabled_values = {"1", "true", "yes", "on"}
        # Smoke tests validate startup stability, not network access or modal
        # dialogs. Avoid an external request while their event loop is running.
        if any(
            os.environ.get(flag, "").lower() in enabled_values
            for flag in ("VOSE_SKIP_UPDATE_CHECK", "VOSE_STARTUP_SMOKE_TEST")
        ):
            return

        try:
            from modules.updater.update_checker import UpdateChecker

            application = _get_application_instance()
            if application is None:
                return

            # Parent the worker to QApplication, not MainWindow. The window may
            # be closed while the network request is still running.
            checker = UpdateChecker(application)
            checker.result_ready.connect(self._on_update_result)
            self._update_checker = checker

            application.aboutToQuit.connect(
                lambda checker=checker: checker.wait()
            )
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

        latest_version, _page_url = pending
        reply = QMessageBox.question(
            self,
            "アップデートがあります",
            (
                f"VO-SE Pro の新しいビルド {latest_version} が公開されています。\n"
                "アプリ内で更新ファイルをダウンロードし、検証後に適用しますか？\n"
                "更新の適用時にアプリは自動的に終了して再起動します。"
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._start_update_download()

    def _start_update_download(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QProgressDialog
        from modules.updater.update_manager import UpdateDownloadWorker

        current = getattr(self, "_update_download_worker", None)
        if current is not None and current.isRunning():
            return

        progress = QProgressDialog(
            "Releaseの更新ファイルを確認しています…",
            "キャンセル",
            0,
            100,
            self,
        )
        progress.setWindowTitle("VO-SE Pro アップデート")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setValue(0)

        application = _get_application_instance()
        if application is None:
            raise RuntimeError("QApplication is not available for the update download.")

        # Keep the QThread alive even if the user closes the main window.
        worker = UpdateDownloadWorker(application)
        worker.progress.connect(self._on_update_download_progress)
        worker.completed.connect(self._on_update_download_completed)
        worker.failed.connect(self._on_update_download_failed)
        worker.cancelled.connect(self._on_update_download_cancelled)
        worker.finished.connect(self._on_update_worker_finished)
        progress.canceled.connect(worker.cancel)

        self._update_download_progress = progress
        self._update_download_worker = worker

        application.aboutToQuit.connect(
            lambda worker=worker: (worker.cancel(), worker.wait())
        )

        progress.show()
        worker.start()

    def _on_update_worker_finished(self) -> None:
        self._update_download_worker = None

    def _on_update_download_progress(self, percent: int, label: str) -> None:
        progress = getattr(self, "_update_download_progress", None)
        if progress is None:
            return
        progress.setLabelText(label)
        progress.setValue(percent)

    def _on_update_download_completed(self, downloaded_path: str) -> None:
        progress = getattr(self, "_update_download_progress", None)
        if progress is not None:
            progress.setValue(100)
            progress.close()
        self._update_download_progress = None

        try:
            from modules.updater.update_installer import prepare_and_schedule_update

            message = prepare_and_schedule_update(downloaded_path)
        except Exception as exc:
            logger.warning("Could not prepare VO-SE update", exc_info=True)
            QMessageBox.critical(
                self,
                "アップデートを適用できません",
                (
                    f"{exc}\n\n"
                    "検証済みの更新ファイルは保存されています。"
                    "Releaseページから手動で更新できます。"
                ),
            )
            pending = getattr(self, "_pending_update", None)
            if pending:
                try:
                    webbrowser.open(pending[1])
                except Exception:
                    logger.debug("Could not open Release page", exc_info=True)
            return

        QMessageBox.information(
            self,
            "アップデート準備完了",
            message,
        )
        application = _get_application_instance()
        if application is not None:
            application.quit()

    def _on_update_download_failed(self, message: str) -> None:
        progress = getattr(self, "_update_download_progress", None)
        if progress is not None:
            progress.close()
        self._update_download_progress = None
        QMessageBox.critical(
            self,
            "アップデートのダウンロードに失敗しました",
            (
                f"{message}\n\n"
                "ファイルのサイズとSHA-256を検証できなかったため、"
                "更新は適用していません。"
            ),
        )

    def _on_update_download_cancelled(self) -> None:
        progress = getattr(self, "_update_download_progress", None)
        if progress is not None:
            progress.close()
        self._update_download_progress = None
        try:
            self.statusBar().showMessage("アップデートをキャンセルしました。", 5000)
        except Exception:
            pass

    # Replacing these class methods before construction is important: MainWindow
    # schedules _check_for_updates with a bound callback during __init__.
    main_window_class._check_for_updates = _check_for_updates
    main_window_class._on_update_result = _on_update_result
    main_window_class._show_update_dialog = _show_update_dialog
    main_window_class._start_update_download = _start_update_download
    main_window_class._on_update_worker_finished = _on_update_worker_finished
    main_window_class._on_update_download_progress = _on_update_download_progress
    main_window_class._on_update_download_completed = _on_update_download_completed
    main_window_class._on_update_download_failed = _on_update_download_failed
    main_window_class._on_update_download_cancelled = _on_update_download_cancelled
    main_window_class._vose_update_notification_patch_installed = True
    logger.info("VO-SE in-app update patch installed")
