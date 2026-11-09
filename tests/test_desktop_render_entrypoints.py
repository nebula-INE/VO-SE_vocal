from pathlib import Path


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
