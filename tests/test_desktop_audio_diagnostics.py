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
    assert 'self._start_audio_mixer_command("start"' in prepared_handler

    command_worker = source.split("class _AudioMixerCommandThread", 1)[1].split(
        "class MainWindow", 1
    )[0]
    assert "self.mixer.set_tracks(self.tracks)" in command_worker
    assert "self.mixer.play(self.start_sec)" in command_worker

    completion_handler = source.split(
        "def _on_audio_mixer_command_completed", 1
    )[1].split("def _on_audio_mixer_command_failed", 1)[0]
    assert '"last_playback_errors": []' in completion_handler
    assert "output_underflow_count" in completion_handler


def test_pause_and_stop_commands_do_not_block_the_gui_thread():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    play_handler = source.split("def on_play_pause_toggled(self):", 1)[1].split(
        "def on_record_toggled", 1
    )[0]
    stop_handler = source.split("def stop_and_clear_playback(self) -> None:", 1)[1].split(
        "def on_single_note_modified", 1
    )[0]
    assert 'self._start_audio_mixer_command("pause")' in play_handler
    assert 'self._start_audio_mixer_command("stop")' in stop_handler
    assert "mixer.stop()" not in play_handler
    assert "stop_mixer()" not in stop_handler


def test_loop_restart_uses_background_mixer_command_not_gui_call():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    update_handler = source.split("def update_playback_ui(self):", 1)[1].split(
        "def _format_timecode", 1
    )[0]
    assert 'self._start_audio_mixer_command("start"' in update_handler
    assert "mixer.play(0.0)" not in update_handler


def test_background_mixer_start_stops_transport_timer_until_device_ready():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    command_start = source.split("def _start_audio_mixer_command(", 1)[1].split(
        "def _complete_playback_stop_ui", 1
    )[0]
    start_branch = command_start.split('if operation == "start":', 1)[1].split(
        "else:", 1
    )[0]
    assert "self.playback_timer.stop()" in start_branch
    assert "self.is_playing = False" in start_branch


def test_pause_completion_restores_exact_mixer_position_to_transport():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    completion = source.split("def _on_audio_mixer_command_completed", 1)[1].split(
        "def _on_audio_mixer_command_failed", 1
    )[0]
    pause = completion.split('if operation == "pause":', 1)[1].split(
        'if operation == "stop":', 1
    )[0]
    assert 'getattr(mixer, "position_sec", None)' in pause
    assert "self._set_transport_time(self.current_playback_time)" in pause


def test_playback_snapshot_locks_editor_before_shallow_note_copy():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    handler = source.split("def on_play_pause_toggled(self):", 1)[1].split(
        "def on_record_toggled", 1
    )[0]
    lock_position = handler.index("self._set_playback_preparation_ui(True)")
    copy_position = handler.index("notes_snapshot = list(source_notes)")
    assert lock_position < copy_position
    assert "current_track.notes = list(" in handler
    assert "deepcopy(source_notes)" not in handler
    assert "deepcopy(" not in handler

def test_legacy_space_shortcut_uses_unified_async_transport():
    source = (ROOT / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    shortcut = source.split("def toggle_playback(self, event=None):", 1)[1].split(
        "def refresh_canvas", 1
    )[0]
    assert "self.on_play_pause_toggled()" in shortcut
    assert "play_file(" not in shortcut
    assert "pause()" not in shortcut
