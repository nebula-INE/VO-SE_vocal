from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_transport_timer_surfaces_audio_device_and_buffer_diagnostics():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    assert "def _report_audio_mixer_diagnostics(self, mixer: Any)" in source
    assert 'getattr(mixer, "get_diagnostics", None)' in source
    assert "output_underflow_count" in source
    assert "output_overflow_count" in source
    assert "queue_underrun_count" in source
    assert "last_playback_errors" in source

    update_handler = source.split("def update_playback_ui(self):", 1)[1].split(
        "def _format_timecode", 1
    )[0]
    assert "self._report_audio_mixer_diagnostics(mixer)" in update_handler


def test_audio_device_errors_are_reported_through_user_facing_playback_path():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    assert "再生エラー:" in source
    assert "self.statusBar().showMessage(f\"再生エラー: {exc}\"" in source



def test_audio_error_baseline_resets_for_each_new_playback():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    prepared_handler = source.split(
        "def _on_vocal_playback_render_prepared(self, results: object) -> None:",
        1,
    )[1].split("def _on_vocal_playback_prepare_thread_finished", 1)[0]
    assert 'mixer.set_tracks(tracks)' in prepared_handler
    assert '"last_playback_errors": []' in prepared_handler
    assert "output_underflow_count" in prepared_handler
