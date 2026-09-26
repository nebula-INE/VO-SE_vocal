import os
import subprocess
import sys
import time
import unittest
import pytest

@pytest.mark.smoke
class TestSmoke(unittest.TestCase):
    def test_app_startup(self):
        """パッケージ化されたアプリまたはmain.pyが起動し、クラッシュしないことを確認する"""
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

        if os.path.exists(app_path):
            cmd = [app_path]
        else:
            cmd = [sys.executable, "main.py"]

        proc = subprocess.Popen(
            cmd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        # Windows の PyInstaller --onefile は自己展開に時間がかかるため、
        # 固定2秒ではなく Smoke Test 用の正常終了を最大30秒待つ。
        try:
            stdout, stderr = proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            self.fail(
                "Application did not exit during the smoke-test window.\n"
                f"STDOUT:{stdout}\nSTDERR:{stderr}"
            )

        returncode = proc.returncode
        print(f"Startup check code {returncode}\nSTDOUT:{stdout}\nSTDERR:{stderr}")
        self.assertEqual(
            returncode,
            0,
            f"Application exited with code {returncode}.\n"
            f"STDOUT:{stdout}\nSTDERR:{stderr}",
        )

if __name__ == "__main__":
    unittest.main()
