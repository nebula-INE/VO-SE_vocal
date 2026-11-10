import pytest
from pathlib import Path

from modules.data.midi_manager import MidiInputManager


class _Port:
    def __init__(self, error=None):
        self.error = error
        self.closed = False

    def close(self):
        if self.error is not None:
            raise self.error
        self.closed = True


def test_stop_closes_port_and_releases_reference():
    manager = MidiInputManager("Test MIDI")
    port = _Port()
    manager.port = port

    manager.stop()

    assert port.closed
    assert manager.port is None


def test_stop_propagates_close_failure_and_retains_port_for_retry():
    manager = MidiInputManager("Test MIDI")
    port = _Port(OSError("device is busy"))
    manager.port = port

    with pytest.raises(RuntimeError, match="could not be closed"):
        manager.stop()

    assert manager.port is port
    assert not port.closed


def test_stop_without_open_port_is_safe():
    manager = MidiInputManager("Test MIDI")

    manager.stop()

    assert manager.port is None

def test_desktop_midi_port_switch_keeps_manager_when_shutdown_fails():
    root = Path(__file__).resolve().parents[1]
    source = (root / "modules" / "gui" / "main_window.py").read_text(encoding="utf-8")
    handler = source.split("def on_midi_port_changed(self):", 1)[1].split(
        "def update_gui_with_midi", 1
    )[0]

    assert "except Exception as exc:" in handler
    assert "self.status_label.setText(f\"MIDIの切断に失敗: {exc}\")" in handler
    failure_path = handler.split("except Exception as exc:", 1)[1].split(
        "self.midi_manager = None", 1
    )[0]
    assert "self.midi_manager.stop()" in handler
    assert "return" in failure_path


def test_compatibility_midi_port_switch_reports_shutdown_failure():
    root = Path(__file__).resolve().parents[1]
    source = (root / "modules" / "gui" / "main_window_patch.py").read_text(encoding="utf-8")
    handler = source.split("def _on_port_changed(port_name: str) -> None:", 1)[1].split(
        "# セレクタのシグナルに接続", 1
    )[0]

    assert "except Exception as exc:" in handler
    assert "MIDI入力を切断できません" in handler
    assert handler.index("return") < handler.index("window._midi_input_manager.start()")
