from pathlib import Path
from typing import Callable

import pytest

from modules.data.midi_manager import MidiInputManager
from modules.gui.main_window_patch import _patch_midi_realtime


ROOT = Path(__file__).resolve().parents[1]


class _Port:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.closed = False

    def close(self) -> None:
        if self.error is not None:
            raise self.error
        self.closed = True


class _Signal:
    def __init__(self) -> None:
        self.callback: Callable[[str], None] | None = None

    def connect(self, callback: Callable[[str], None]) -> None:
        self.callback = callback


class _Selector:
    def __init__(self) -> None:
        self.currentTextChanged = _Signal()
        self.items: list[str] = []
        self.current_text = ""
        self.signals_blocked = False

    def clear(self) -> None:
        self.items.clear()

    def addItem(self, value: str) -> None:
        self.items.append(value)

    def blockSignals(self, blocked: bool) -> bool:
        previous = self.signals_blocked
        self.signals_blocked = blocked
        return previous

    def setCurrentText(self, value: str) -> None:
        self.current_text = value


class _StatusBar:
    def __init__(self) -> None:
        self.messages: list[tuple[str, int]] = []

    def showMessage(self, message: str, timeout: int = 0) -> None:
        self.messages.append((message, timeout))


class _Window:
    def __init__(self) -> None:
        self.midi_port_selector = _Selector()
        self.status_bar = _StatusBar()
        self._midi_input_manager: MidiInputManager | None = None

    def statusBar(self) -> _StatusBar:
        return self.status_bar


def test_stop_closes_port_and_releases_reference() -> None:
    manager = MidiInputManager("Test MIDI")
    port = _Port()
    manager.port = port

    manager.stop()

    assert port.closed
    assert manager.port is None


def test_stop_propagates_close_failure_and_retains_port_for_retry() -> None:
    manager = MidiInputManager("Test MIDI")
    port = _Port(OSError("device is busy"))
    manager.port = port

    with pytest.raises(RuntimeError, match="could not be closed"):
        manager.stop()

    assert manager.port is port
    assert not port.closed


def test_stop_without_open_port_is_safe() -> None:
    manager = MidiInputManager("Test MIDI")

    manager.stop()

    assert manager.port is None


def test_port_change_aborts_and_restores_selection_when_close_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        MidiInputManager,
        "get_available_ports",
        staticmethod(lambda: ["MIDI A", "MIDI B"]),
    )
    window = _Window()
    _patch_midi_realtime(window)

    manager = window._midi_input_manager
    assert manager is not None
    port = _Port(OSError("device is busy"))
    manager.port_name = "MIDI A"
    manager.port = port

    callback = window.midi_port_selector.currentTextChanged.callback
    assert callback is not None
    callback("MIDI B")

    assert manager.port is port
    assert manager.port_name == "MIDI A"
    assert port.closed is False
    assert window.midi_port_selector.current_text == "MIDI A"
    assert window.midi_port_selector.signals_blocked is False
    assert any(
        "MIDI 入力の切断に失敗" in message
        for message, _ in window.status_bar.messages
    )


def test_main_window_cancels_close_when_midi_shutdown_fails() -> None:
    source = (
        ROOT / "modules" / "gui" / "main_window.py"
    ).read_text(encoding="utf-8")
    close_handler = source.split("def closeEvent(self, event):", 1)[1].split(
        "# ==============================================================================",
        1,
    )[0]
    midi_shutdown = close_handler.split(
        "# The MIDI setup patch stores the live input manager under this name.",
        1,
    )[1].split('engine = getattr(self, "vo_se_engine", None)', 1)[0]

    assert "MIDI入力終了待ち" in midi_shutdown
    assert "QMessageBox.warning(" in midi_shutdown
    assert "event.ignore()" in midi_shutdown
    assert "return" in midi_shutdown
