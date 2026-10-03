# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
import os
import zipfile
import json
import tempfile
try:
    from PySide6.QtCore import QObject, Signal as pyqtSignal, Slot as pyqtSlot
except ImportError:
    from modules.utils.pyside_stub import QObject, Signal as pyqtSignal, Slot as pyqtSlot

# ==========================================================================
# 1. MIDI読み込み非同期ワーカー
# ==========================================================================
class MidiLoadWorker(QObject):
    """
    メインスレッド（UI）を止めずに、バックグラウンドで重いMIDIバイナリデータを
    パースしてノート配列を抽出するワーカー。
    """
    finished = pyqtSignal(list)
    error = pyqtSignal(str)

    def __init__(self, file_path, midi_manager):
        super().__init__()
        self.file_path = file_path
        self.midi_manager = midi_manager

    @pyqtSlot()
    def run(self):
        try:
            if not os.path.exists(self.file_path):
                raise FileNotFoundError(f"MIDIファイルが見つかりません: {self.file_path}")
            notes = self.midi_manager.load_midi_pure_data(self.file_path)
            self.finished.emit(notes)
        except Exception as e:
            self.error.emit(str(e))


# ==========================================================================
# 2. ZIP音源インポート非同期ワーカー（プログレス連動型）
# ==========================================================================
class VoiceImportWorker(QObject):
    """
    巨大な歌声ライブラリ（ZIP）の解凍、および展開先でのフォルダ構築を
    UIスレッドを1ミリもフリーズさせずに実行するワーカー。
    """
    progress = pyqtSignal(int)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, zip_path, target_dir):
        super().__init__()
        self.zip_path = zip_path
        self.target_dir = target_dir

    @pyqtSlot()
    def run(self):
        try:
            if not os.path.exists(self.zip_path):
                raise FileNotFoundError(f"音源ZIPファイルが見つかりません: {self.zip_path}")

            with zipfile.ZipFile(self.zip_path, 'r') as zip_ref:
                infolist = zip_ref.infolist()
                uncompress_size = sum((file.file_size for file in infolist))
                extracted_size = 0
                
                os.makedirs(self.target_dir, exist_ok=True)

                for file in infolist:
                    zip_ref.extract(file, self.target_dir)
                    extracted_size += file.file_size
                    
                    if uncompress_size > 0:
                        pct = int((extracted_size / uncompress_size) * 100)
                        self.progress.emit(pct)

            voice_name = os.path.basename(self.target_dir)
            self.finished.emit(voice_name)
        except Exception as e:
            self.error.emit(str(e))


# ==========================================================================
# 3. 大規模プロジェクト保存（JSON書き出し）非同期ワーカー
# ==========================================================================
class ProjectSaveWorker(QObject):
    """
    タイムライン上の数千〜数万個のオブジェクトデータをJSONシリアライズし、
    ディスクI/Oブロックを起こさずに安全に書き出すワーカー。

    [FIX] アトミック書き込みに os.replace() を使用するよう変更。
    旧実装の os.remove() + os.rename() は Windows では既存ファイルへの
    rename が失敗する（PermissionError）ことがある。
    os.replace() は POSIX でも Windows でも原子的に置き換えを行う。
    """
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, file_path, project_data):
        super().__init__()
        self.file_path = file_path
        self.project_data = project_data

    @pyqtSlot()
    def run(self):
        temp_name = None
        try:
            dir_name = os.path.dirname(self.file_path)
            if dir_name and not os.path.exists(dir_name):
                os.makedirs(dir_name, exist_ok=True)

            # 一時ファイルへ書き出し
            with tempfile.NamedTemporaryFile(
                'w',
                dir=dir_name or None,
                delete=False,
                encoding='utf-8',
                suffix='.tmp'
            ) as tf:
                json.dump(self.project_data, tf, ensure_ascii=False, indent=4)
                temp_name = tf.name

            # [FIX] os.replace() を使ってアトミックに置換（Windows対応）
            # POSIX では rename(2) と同等。Windows でも既存ファイルを上書きできる。
            os.replace(temp_name, self.file_path)
            temp_name = None  # 成功したので後始末不要

            self.finished.emit()
        except Exception as e:
            # 失敗時は一時ファイルを残さないようクリーンアップ
            if temp_name and os.path.exists(temp_name):
                try:
                    os.remove(temp_name)
                except OSError:
                    pass
            self.error.emit(str(e))
