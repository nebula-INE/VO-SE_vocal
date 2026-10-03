# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
import os
# PyQt6 から PySide6 に変更
try:
    from PySide6.QtCore import QUrl, Signal, QObject
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
except ImportError:
    from modules.utils.pyside_stub import QUrl, Signal, QObject, QMediaPlayer, QAudioOutput

class AudioPlayer(QObject):
    # PySide6 では pyqtSignal ではなく Signal を使います
    position_changed = Signal(int) 
    
    def __init__(self, volume=0.8):
        super().__init__()
        self.player = QMediaPlayer()
        self.audio_output = QAudioOutput()
        
        self.player.setAudioOutput(self.audio_output)
        self.audio_output.setVolume(volume) # configからの音量を適用
        
        # 位置変更の通知設定
        self.player.positionChanged.connect(self.position_changed.emit)

    def play_file(self, file_path):
        """指定したWavファイルを再生"""
        if os.path.exists(file_path):
            # 絶対パスを確実に渡す
            self.player.setSource(QUrl.fromLocalFile(os.path.abspath(file_path)))
            self.player.play()

    def set_volume(self, value):
        """0.0 ～ 1.0 の範囲で音量を設定"""
        self.audio_output.setVolume(value)

    def set_position(self, position_ms: int) -> None:
        """再生位置をミリ秒単位で変更する。"""
        self.player.setPosition(max(0, int(position_ms)))

    def stop(self):
        self.player.stop()

    def pause(self):
        self.player.pause()
