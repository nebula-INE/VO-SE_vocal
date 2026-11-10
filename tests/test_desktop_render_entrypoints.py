from pathlib import Path

from modules.gui.mixins.project_io_mixin import _ExternalProjectLoadWorker


ROOT = Path(__file__).resolve().parents[1]


def test_toolbar_render_entrypoint_uses_async_worker():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")

    render_handler = source.split("def on_render_button_clicked(self):", 1)[1].split(
        "@Slot()\n    def on_ai_button_clicked", 1
    )[0]
    assert "return self.request_render()" in render_handler
    assert "QThreadPool.globalInstance().start(worker)" in source
    assert "deepcopy(list(notes))" in source
    assert "parameters=deepcopy(parameters)" in source


def test_file_menu_wav_export_uses_same_async_render_pipeline():
    source = (ROOT / "modules" / "gui" / "mixins" / "project_io_mixin.py").read_text(
        encoding="utf-8"
    )
    export_handler = source.split("def on_export_button_clicked(self: Any):", 1)[1].split(
        "def parse_ust_dict_to_note", 1
    )[0]
    assert "start_render(" in export_handler
    assert "play_after_success=False" in export_handler
    assert "export_to_wav_v2(" not in export_handler


def test_render_outputs_are_staged_before_replacing_user_file():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    assert 'suffix=".tmp.wav"' in source
    assert "os.replace(produced_path, final_path)" in source


def test_analysis_thread_does_not_override_qthread_finished_signal():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    worker = source.split("class AnalysisThread(QThread):", 1)[1].split(
        "# メインウィンドウクラス", 1
    )[0]
    assert "completed = Signal(dict)" in worker
    assert "finished = Signal(dict)" not in worker
    assert "worker.finished.connect(self._on_analysis_thread_finished)" in source


def test_window_waits_for_analysis_and_voice_import_workers_before_close():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    close_handler = source.split("def closeEvent(self, event):", 1)[1].split(
        "# レリタリング実行メゾット", 1
    )[0]
    assert 'getattr(self, "analysis_thread", None)' in close_handler
    assert 'getattr(gallery, "_import_worker", None)' in close_handler
    assert "worker.wait(15000)" in close_handler
    assert "event.ignore()" in close_handler


def test_window_waits_for_external_project_parser_before_close():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    close_handler = source.split("def closeEvent(self, event):", 1)[1].split(
        "# レリタリング実行メゾット", 1
    )[0]
    assert 'getattr(self, "_project_load_worker", None)' in close_handler
    assert 'done.wait(15.0)' in close_handler
    assert "QCoreApplication.processEvents()" in close_handler
    assert 'getattr(self, "_audio_mixer_command_thread", None)' in close_handler
    assert "audio_command_worker.wait(15000)" in close_handler


def test_external_project_worker_signals_failure_and_always_sets_done(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{broken json", encoding="utf-8")
    worker = _ExternalProjectLoadWorker(str(path))
    failures = []
    worker.signals.failed.connect(
        lambda file_path, error: failures.append((file_path, error))
    )

    worker.run()

    assert worker.done.is_set()
    assert len(failures) == 1
    assert failures[0][0] == str(path)


def test_project_open_dialog_includes_native_vose_extension():
    source = (ROOT / "modules" / "gui" / "mixins" / "project_io_mixin.py").read_text(
        encoding="utf-8"
    )
    dispatcher = source.split(
        "def import_external_project(self: Any) -> None:", 1
    )[1].split("def _on_external_project_loaded", 1)[0]
    assert "*.vose" in dispatcher


def test_legacy_ust_menu_routes_to_async_project_loader():
    source = (ROOT / "modules" / "gui" / "main_window_patch.py").read_text(encoding="utf-8")
    callback = source.split("def _open_ust_project_async():", 1)[1].split(
        "act_import_ust.triggered.connect", 1
    )[0]
    assert 'getattr(window, "load_external_project_async", None)' in callback
    assert "window.load_ust_file(file_path)" in callback



def test_voice_gallery_uses_worker_scanned_voice_index():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    worker = source.split("class _VoiceImportWorker", 1)[1].split(
        "class VoiceCardGallery", 1
    )[0]
    assert 'voices = dict(getattr(self._manager, "voices", {}) or {})' in worker

    gallery = source.split("class VoiceCardGallery", 1)[1]
    import_done = gallery.split("def _on_import_done", 1)[1].split(
        "def _on_import_failed", 1
    )[0]
    assert "self.setup_gallery(voices=voices)" in import_done

    setup = gallery.split("def setup_gallery(self, voices=None):", 1)[1].split(
        "def _register_card", 1
    )[0]
    assert "voices if isinstance(voices, dict) else self.manager.scan_voices()" in setup


def test_window_waits_for_voicebank_zip_worker_before_close():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    close_handler = source.split("def closeEvent(self, event):", 1)[1].split(
        "# レリタリング実行メゾット", 1
    )[0]
    assert 'getattr(self, "_voice_bank_import_worker", None)' in close_handler
    assert "worker.wait(15000)" in close_handler


def test_voice_library_initial_and_manual_scans_run_on_worker():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    worker = source.split("class _VoiceScanWorker", 1)[1].split(
        "class VoiceCardGallery", 1
    )[0]
    assert "voices = self.manager.scan_voices()" in worker
    assert "self.completed.emit(dict(voices or {}))" in worker

    gallery = source.split("def setup_gallery(self, voices=None):", 1)[1].split(
        "def _on_voice_scan_completed", 1
    )[0]
    assert "_VoiceScanWorker(self.manager, self)" in gallery
    assert "worker.start()" in gallery
    assert "self.manager.scan_voices()" not in gallery


def test_voice_scan_worker_is_waited_for_during_window_shutdown():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    close_handler = source.split("def closeEvent(self, event):", 1)[1].split(
        "# レリタリング実行メゾット", 1
    )[0]
    assert 'getattr(gallery, "_scan_worker", None)' in close_handler
    assert "voice_scan_worker" in close_handler
