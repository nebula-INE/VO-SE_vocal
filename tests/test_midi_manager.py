import pytest

from modules.data.midi_manager import MidiInputManager
from modules.gui.main_window_patch import _patch_midi_realtime


class _Port:
    def __init__(self, error=None):
        self.error = error
        self.closed = False

    def close(self):
        if self.error is not None:
            raise self.error
        self.closed = True


class _Signal:
    def connect(self, callback):
        self.callback = callback


class _Selector:
    def __init__(self):
        self.currentTextChanged = _Signal()
        self.items = []
        self.current_text = ""
        self.signals_blocked = False

    def clear(self):
        self.items.clear()

    def addItem(self, value):
        self.items.append(value)

    def blockSignals(self, blocked):
        previous = self.signals_blocked
        self.signals_blocked = blocked
        return previous

    def setCurrentText(self, value):
        self.current_text = value


class _StatusBar:
    def __init__(self):
        self.messages = []

    def showMessage(self, message, timeout=0):
        self.messages.append((message, timeout))


class _Window:
    def __init__(self):
        self.midi_port_selector = _Selector()
        self.status_bar = _StatusBar()

    def statusBar(self):
        return self.status_bar


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


def test_port_change_aborts_and_restores_selection_when_close_fails(monkeypatch):
    monkeypatch.setattr(
        MidiInputManager,
        "get_available_ports",
        staticmethod(lambda: ["MIDI A", "MIDI B"]),
    )
    window = _Window()
    _patch_midi_realtime(window)

    manager = window._midi_input_manager
    port = _Port(OSError("device is busy"))
    manager.port_name = "MIDI A"
    manager.port = port

    window.midi_port_selector.currentTextChanged.callback("MIDI B")

    assert manager.port is port
    assert manager.port_name == "MIDI A"
    assert port.closed is False
    assert window.midi_port_selector.current_text == "MIDI A"
    assert window.midi_port_selector.signals_blocked is False
    assert any(
        "MIDI 入力の切断に失敗" in message
        for message, _ in window.status_bar.messages
    )
