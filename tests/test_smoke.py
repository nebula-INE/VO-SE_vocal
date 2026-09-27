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

        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
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
                proc.terminate()
                try:
                    stdout, stderr = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate()

                print(
                    f"Startup check: process remained alive for {startup_window:.0f}s.\n"
                    f"STDOUT:{stdout}\nSTDERR:{stderr}"
                )
                return

            stdout, stderr = proc.communicate()
            if returncode == 0:
                print(
                    "Startup check: application exited normally during the smoke-test window.\n"
                    f"STDOUT:{stdout}\nSTDERR:{stderr}"
                )
                return

            self.fail(
                f"Application exited unexpectedly with code {returncode}.\n"
                f"STDOUT:{stdout}\nSTDERR:{stderr}"
            )
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate()


if __name__ == "__main__":
    unittest.main()
