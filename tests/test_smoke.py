import os
import subprocess
import sys
import time
import unittest

import pytest


@pytest.mark.smoke
class TestSmoke(unittest.TestCase):
    def test_app_startup(self):
        """パッケージ化されたアプリまたはmain.pyが起動し、一定時間安定して生存することを確認する。"""
        env = os.environ.copy()
        env["VOSE_STARTUP_SMOKE_TEST"] = "1"
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["QT_MEDIA_BACKEND"] = "ffmpeg"
        env["PYTHONUTF8"] = "1"

        if sys.platform == "win32":
            app_path = "dist/VO-SE_vocal_Win.exe"
        elif sys.platform == "darwin":
            app_path = "dist/VO-SE_vocal_Mac.app/Contents/MacOS/VO-SE_vocal_Mac"
        else:
            app_path = "dist/VO-SE_vocal_Linux"

        cmd = [app_path] if os.path.exists(app_path) else [sys.executable, "main.py"]

        # Do not use PIPE for a GUI process. On Windows, descendants can inherit
        # pipe handles, leaving communicate()'s reader threads blocked forever
        # even after the parent process has exited.
        proc = subprocess.Popen(
            cmd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        startup_window = 8.0
        deadline = time.monotonic() + startup_window

        try:
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.25)

            returncode = proc.poll()
            if returncode is None:
                # GUIアプリは通常の終了を待つものではない。
                # 一定時間クラッシュせずイベントループが生存したことを
                # 起動成功とみなし、テスト側から終了させる。
                print(
                    f"Startup check: process remained alive for {startup_window:.0f}s."
                )
                return

            if returncode == 0:
                print(
                    "Startup check: application exited normally during the smoke-test window."
                )
                return

            self.fail(
                f"Application exited unexpectedly with code {returncode}."
            )
        finally:
            _stop_process_tree(proc)


def _stop_process_tree(proc, timeout=5):
    """Stop the smoke-test app without waiting for inherited pipe handles."""
    if proc.poll() is not None:
        return

    if sys.platform == "win32":
        # The packaged Windows app may create child processes. Stop the whole
        # tree so the smoke test does not leave background processes behind.
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    if proc.poll() is None:
        try:
            proc.terminate()
        except OSError:
            pass

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            pass
        proc.wait(timeout=timeout)


if __name__ == "__main__":
    unittest.main()
