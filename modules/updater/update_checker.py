# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
# modules/updater/auto_updater.py
import os, sys, subprocess, tempfile, urllib.request
try:
    from PySide6.QtCore import QThread, Signal
except ImportError:
    from modules.utils.pyside_stub import QThread, Signal

class DownloadThread(QThread):
    progress = Signal(int)          # 0〜100
    finished = Signal(str)          # 保存先パス
    error = Signal(str)

    def __init__(self, url):
        super().__init__()
        self.url = url

    def run(self):
        try:
            tmp = tempfile.mktemp(suffix=".exe")
            with urllib.request.urlopen(self.url) as res:
                total = int(res.headers.get("Content-Length", 0))
                downloaded = 0
                with open(tmp, "wb") as f:
                    while True:
                        chunk = res.read(65536)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            self.progress.emit(int(downloaded / total * 100))
            self.finished.emit(tmp)
        except Exception as e:
            self.error.emit(str(e))


def apply_update_and_restart(new_exe_path: str):
    """
    新EXEを現在のEXEと入れ替えて再起動する。
    Windows: バッチスクリプトで上書き（実行中ファイルを直接消せないため）
    macOS:   シェルスクリプトで上書き
    """
    current_exe = sys.executable

    if sys.platform == "win32":
        bat = tempfile.mktemp(suffix=".bat")
        script = f"""
@echo off
timeout /t 2 /nobreak > nul
move /y "{new_exe_path}" "{current_exe}"
start "" "{current_exe}"
del "%~f0"
"""
        with open(bat, "w") as f:
            f.write(script)
        subprocess.Popen(["cmd", "/c", bat], creationflags=subprocess.DETACHED_PROCESS)

    else:  # macOS
        sh = tempfile.mktemp(suffix=".sh")
        script = f"""#!/bin/bash
sleep 2
mv -f "{new_exe_path}" "{current_exe}"
chmod +x "{current_exe}"
open "{current_exe}"
rm -- "$0"
"""
        with open(sh, "w") as f:
            f.write(script)
        os.chmod(sh, 0o755)
        subprocess.Popen([sh])

    sys.exit(0)  # 現プロセスを終了
