import pytest

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
