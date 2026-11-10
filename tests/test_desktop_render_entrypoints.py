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
