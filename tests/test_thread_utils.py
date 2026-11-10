from modules.gui.thread_utils import wait_for_thread_shutdown


class _Worker:
    def __init__(self, running: bool, wait_result: bool = True):
        self.running = running
        self.wait_result = wait_result
        self.wait_timeout = None

    def isRunning(self):
        return self.running

    def wait(self, timeout_ms):
        self.wait_timeout = timeout_ms
        self.running = not self.wait_result
        return self.wait_result


def test_missing_or_already_stopped_worker_is_safe():
    assert wait_for_thread_shutdown(None)
    assert wait_for_thread_shutdown(_Worker(running=False))


def test_running_worker_is_waited_for_with_timeout():
    worker = _Worker(running=True, wait_result=True)

    assert wait_for_thread_shutdown(worker, 2500)
    assert worker.wait_timeout == 2500


def test_worker_that_exceeds_timeout_is_not_reported_as_stopped():
    worker = _Worker(running=True, wait_result=False)

    assert not wait_for_thread_shutdown(worker, 100)
    assert worker.wait_timeout == 100
