# pyright: reportMissingImports=false
# Optional AI-training dependencies (torch/torchaudio) are excluded from runtime CI.
# modules/ai/adaptation_dataset.py
import torch
import torchaudio
import numpy as np
import os
from pathlib import Path
from torch.utils.data import Dataset
from modules.data.oto_parser import OtoParser

class UTAUAdaptationDataset(Dataset):
    def __init__(
        self,
        voice_dir: str,
        sample_rate: int = 44100,
        n_fft: int = 1024,
        hop_length: int = 256,
        n_mels: int = 80,
        max_duration_sec: float = 2.0,  # 長すぎるWAVはカット
    ):
        self.sample_rate = sample_rate
        self.hop_length = hop_length
        self.n_mels = n_mels
        
        # 1. oto.ini をパースしてWAVパス一覧を取得
        self.oto_parser = OtoParser()
        self.oto_parser.load_voice_dir(voice_dir)
        self.wav_paths = []
        self.aliases = []
        for alias, entry in self.oto_parser._db.items():
            if os.path.exists(entry.wav_path):
                self.wav_paths.append(entry.wav_path)
                self.aliases.append(alias)
        
        # 2. 音声の前処理（メルスペクトログラム抽出）を事前キャッシュ
        #    → 学習中に毎回WORLDを呼ぶと遅いので、事前にnpyで保存
        self.cache_dir = Path(voice_dir) / ".vose_cache"
        self.cache_dir.mkdir(exist_ok=True)
        
        self.mel_cache = []
        self.wav_cache = []  # ターゲット波形（ダウンサンプリング済み）
        
        for wav_path in self.wav_paths:
            mel_path = self.cache_dir / f"{Path(wav_path).stem}_mel.npy"
            wav_target_path = self.cache_dir / f"{Path(wav_path).stem}_target.npy"
            
            if mel_path.exists() and wav_target_path.exists():
                # キャッシュからロード
                mel = np.load(mel_path)
                target = np.load(wav_target_path)
            else:
                # 新規抽出
                mel, target = self._extract_mel_and_target(wav_path, max_duration_sec)
                np.save(mel_path, mel)
                np.save(wav_target_path, target)
            
            self.mel_cache.append(torch.from_numpy(mel).float())
            self.wav_cache.append(torch.from_numpy(target).float())
    
    def _extract_mel_and_target(self, wav_path, max_duration_sec):
        # 1. WAV読み込み（モノラル化・リサンプリング）
        waveform, sr = torchaudio.load(wav_path)
        if sr != self.sample_rate:
            waveform = torchaudio.functional.resample(waveform, sr, self.sample_rate)
        if waveform.shape[0] > 1:
            waveform = torch.mean(waveform, dim=0, keepdim=True)  # モノラル化
        
        # 2. 長さ制限（メモリ節約のため）
        max_samples = int(max_duration_sec * self.sample_rate)
        if waveform.shape[1] > max_samples:
            waveform = waveform[:, :max_samples]
        
        # 3. RMS正規化（-18dBFS = 0.125 にピークを合わせる）
        rms = torch.sqrt(torch.mean(waveform ** 2))
        if rms > 0.001:
            gain = 0.125 / rms
            waveform = waveform * gain
        waveform = torch.clamp(waveform, -1.0, 1.0)
        
        # 4. メルスペクトログラム抽出（BigVGANの入力と同じパラメータ）
        #    ※ C++側のWORLD→メル変換と完全に一致させること！
        mel_spectrogram = torchaudio.transforms.MelSpectrogram(
            sample_rate=self.sample_rate,
            n_fft=1024,
            hop_length=self.hop_length,
            n_mels=self.n_mels,
            power=1.0,  # 振幅スペクトル（BigVGANは振幅メルを期待）
            norm="slaney",
        )
        mel = mel_spectrogram(waveform)  # [1, n_mels, frames]
        mel = torch.log(torch.clamp(mel, min=1e-5))  # log圧縮（推論時と合わせる）
        
        # 5. ターゲット波形（教師信号）はそのままfloat32で保持
        target = waveform.squeeze(0).numpy().astype(np.float32)
        mel = mel.squeeze(0).numpy().astype(np.float32)  # [n_mels, frames]
        
        # 転置して [frames, n_mels] にする（BigVGANの入力形状に合わせる）
        mel = mel.T  # [frames, 80]
        
        return mel, target
    
    def __len__(self):
        return len(self.mel_cache)
    
    def __getitem__(self, idx):
        # 可変長データをそのまま返す（バッチ処理でパディング）
        return {
            "mel": self.mel_cache[idx],        # [frames, 80]
            "target": self.wav_cache[idx],     # [samples]
            "alias": self.aliases[idx],
        }

    @staticmethod
    def collate_fn(batch):
        """可変長シーケンスをパディングしてバッチ化"""
        mels = [item["mel"] for item in batch]
        targets = [item["target"] for item in batch]
        aliases = [item["alias"] for item in batch]
        
        # メルスペクトログラムのパディング（時間方向）
        mel_lengths = [mel.shape[0] for mel in mels]
        max_mel_len = max(mel_lengths)
        padded_mels = torch.zeros(len(batch), max_mel_len, mels[0].shape[1])
        for i, mel in enumerate(mels):
            padded_mels[i, :mel.shape[0], :] = mel
        
        # 波形のパディング（メル長 × hop_length に合わせる）
        # ※ 簡易的に最大長に合わせる（実際は学習時にクロップしても可）
        target_lengths = [t.shape[0] for t in targets]
        max_target_len = max(target_lengths)
        padded_targets = torch.zeros(len(batch), max_target_len)
        for i, t in enumerate(targets):
            padded_targets[i, :t.shape[0]] = t
        
        return {
            "mel": padded_mels,                 # [B, T_mel, 80]
            "target": padded_targets,           # [B, T_wav]
            "mel_lengths": torch.tensor(mel_lengths),
            "target_lengths": torch.tensor(target_lengths),
            "aliases": aliases,
        }
