from types import SimpleNamespace

from modules.gui.main_window import _AudioMixerCommandThread


class _FakeMixer:
    def __init__(self):
        self.calls = []
        self.last_load_errors = ["missing.wav"]

    def set_tracks(self, tracks):
        self.calls.append(("set_tracks", tracks))

    def get_diagnostics(self):
        return {"output_underflow_count": 2, "output_device_name": "Test Device"}

    def play(self, start_sec):
        self.calls.append(("play", start_sec))

    def pause(self):
        self.calls.append(("pause",))

    def stop(self):
        self.calls.append(("stop",))


def test_audio_mixer_start_command_runs_device_setup_off_transport_slot():
    mixer = _FakeMixer()
    tracks = [SimpleNamespace(audio_path="test.wav")]
    worker = _AudioMixerCommandThread(
        "start", mixer, tracks=tracks, start_sec=4.25
    )
    completed = []
    failed = []
    worker.completed.connect(
        lambda operation, result: completed.append((operation, result))
    )
    worker.failed.connect(
        lambda operation, error: failed.append((operation, error))
    )

    worker.run()

    assert mixer.calls == [("set_tracks", tracks), ("play", 4.25)]
    assert failed == []
    assert completed[0][0] == "start"
    assert completed[0][1]["start_sec"] == 4.25
    assert completed[0][1]["load_errors"] == ["missing.wav"]
    assert completed[0][1]["diagnostics"]["output_device_name"] == "Test Device"


def test_audio_mixer_pause_and_stop_commands_call_the_matching_method():
    for operation, expected_call in (("pause", ("pause",)), ("stop", ("stop",))):
        mixer = _FakeMixer()
        worker = _AudioMixerCommandThread(operation, mixer)
        completed = []
        failed = []
        worker.completed.connect(
            lambda name, result: completed.append((name, result))
        )
        worker.failed.connect(
            lambda name, error: failed.append((name, error))
        )

        worker.run()

        assert mixer.calls == [expected_call]
        assert completed == [(operation, {})]
        assert failed == []


def test_audio_mixer_command_reports_device_errors_without_raising_in_worker():
    mixer = _FakeMixer()

    def fail_start(_start_sec):
        raise RuntimeError("device unavailable")

    mixer.play = fail_start
    worker = _AudioMixerCommandThread(
        "start", mixer, tracks=[SimpleNamespace(audio_path="test.wav")]
    )
    failures = []
    worker.failed.connect(
        lambda operation, error: failures.append((operation, error))
    )

    worker.run()

    assert failures == [("start", "device unavailable")]
