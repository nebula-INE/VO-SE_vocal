import torch
import os
import numpy as np
from pathlib import Path
from typing import Any
from modules.data.oto_parser import OtoParser
from modules.tools.batch_voice_optimizer import BatchVoiceOptimizer
from torch.utils.data import DataLoader, TensorDataset

class VoiceAdaptationEngine:
    def __init__(self, base_model_path: str = "models/bigvgan_base.onnx"):
        self.base_model_path = base_model_path
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.oto_parser = OtoParser()
        self.optimizer = BatchVoiceOptimizer(target_sr=16000)

    # ============================================================
    # 特徴抽出（BatchVoiceOptimizer の private メソッドを再実装）
    # ============================================================
    def _extract_acoustic_features(self, wav_path: str) -> Any:
        """
        WAV から音響特徴量を抽出する（簡易版）。
        実際は BatchVoiceOptimizer の内部ロジックを移植するか、
        独立した抽出器を利用すること。
        """
        # ここではダミー実装（実際のプロジェクトでは適切な特徴量を返す）
        import soundfile as sf
        import numpy as np
        data, sr = sf.read(wav_path)
        data = np.asarray(data, dtype=np.float64)
        # 簡易的な特徴量（例として RMS と F0 推定のダミー）
        rms = np.sqrt(np.mean(data**2))
        # 仮の f0_mean など（本来は WORLD 等で推定）
        return type('Features', (), {
            'f0_mean': 200.0,
            'f0_std': 20.0,
            'centroid_mean': 3000.0,
            'zcr_mean': 0.1,
            'rms_mean': rms
        })()

    # ============================================================
    # 音響指紋抽出
    # ============================================================
    def extract_acoustic_fingerprint(self, voice_dir: str) -> dict:
        """音源フォルダから音響指紋（統計量＋Embeddingベース）を抽出"""
        self.oto_parser.load_voice_dir(voice_dir)
        aliases = self.oto_parser.all_aliases()

        wav_paths = []
        for alias in aliases:
            entry = self.oto_parser.get(alias)
            if entry:
                wav_paths.append(entry.wav_path)

        features_list = []
        for wav_path in wav_paths:
            features = self._extract_acoustic_features(wav_path)  # 自分で実装
            features_list.append(features)

        fingerprint = {
            "f0_mean": np.mean([f.f0_mean for f in features_list]),
            "f0_std": np.std([f.f0_std for f in features_list]),
            "spectral_centroid_mean": np.mean([f.centroid_mean for f in features_list]),
            "zcr_mean": np.mean([f.zcr_mean for f in features_list]),
            "rms_mean": np.mean([f.rms_mean for f in features_list]),
            "num_samples": len(features_list),
            "oto_params": {
                alias: {
                    "preutterance": entry.preutterance,
                    "overlap": entry.overlap,
                    "fixed_range": entry.fixed_range
                } for alias, entry in self.oto_parser._db.items()
            }
        }
        return fingerprint

    # ============================================================
    # DataLoader 準備（ダミー実装）
    # ============================================================
    def _prepare_dataloader(self, voice_dir: str, fingerprint: dict) -> DataLoader:
        """
        学習用の DataLoader を構築する（実際は WAV からメルスペクトルと波形のペアを作成）。
        ここではダミーデータで代用。
        """
        # 実際の実装では voice_dir 内の WAV を読み込み、WORLD でメルスペクトルを抽出して
        # TensorDataset を作成する。ここでは簡易的にランダムテンソルを返す。
        dummy_mel = torch.randn(10, 80, 256)   # (batch, mel_bins, frames)
        dummy_wav = torch.randn(10, 1, 65536)  # (batch, channels, samples)
        dataset = TensorDataset(dummy_mel, dummy_wav)
        return DataLoader(dataset, batch_size=2, shuffle=True)

    # ============================================================
    # LoRA 適応学習
    # ============================================================
    def adapt_bigvgan_with_lora(self, voice_dir: str, fingerprint: dict, output_dir: str = ".vose_adapt"):
        """BigVGANにLoRAアダプターを追加学習させる"""
        try:
            from peft import LoraConfig, get_peft_model, TaskType
            # transformers に BigVGANModel は存在しないため、ダミーで回避
            # 実際には適切な BigVGAN 実装をインポートすること
            # 実際のロード処理はプロジェクトに合わせる
            # base_model = BigVGANModel.from_pretrained(self.base_model_path)
            # 代わりにダミーモデルを作成
            class DummyBigVGAN(torch.nn.Module):
                def forward(self, x):
                    return torch.randn_like(x)
            base_model = DummyBigVGAN()
            base_model.to(self.device)
        except ImportError:
            # 必要なライブラリがない場合のフォールバック
            print("Warning: PEFT or transformers not installed. Using dummy model.")
            base_model = torch.nn.Linear(80, 1).to(self.device)

        # LoRA 設定（ダミーモデルでも動作するよう target_modules を調整）
        try:
            lora_config = LoraConfig(
                task_type=TaskType.FEATURE_EXTRACTION,
                r=8,
                lora_alpha=16,
                target_modules=["q_proj", "v_proj", "k_proj", "out_proj"],  # ダミーモデルには存在しないが、その場合は無視
                lora_dropout=0.05,
            )
            model = get_peft_model(base_model, lora_config)  # type: ignore
        except Exception as e:
            print(f"LoRA config error: {e}. Using base model without LoRA.")
            model = base_model

        model.train()

        # DataLoader の準備
        train_loader = self._prepare_dataloader(voice_dir, fingerprint)

        optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
        loss_fn = torch.nn.L1Loss()

        for epoch in range(3):
            for batch in train_loader:
                mel_spec, target_wav = batch
                mel_spec = mel_spec.to(self.device)
                target_wav = target_wav.to(self.device)

                pred_wav = model(mel_spec)  # 実際の BigVGAN は (B, T, mel) -> (B, samples)
                loss = loss_fn(pred_wav, target_wav)

                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

        # 保存
        os.makedirs(output_dir, exist_ok=True)
        voice_name = Path(voice_dir).name
        save_path = os.path.join(output_dir, f"{voice_name}_lora.pt")
        torch.save(model.state_dict(), save_path)

        # ONNX エクスポート
        self._convert_lora_to_onnx(model, save_path.replace(".pt", ".onnx"))

        return save_path

    # ============================================================
    # ONNX エクスポート（型エラーを回避）
    # ============================================================
    def _convert_lora_to_onnx(self, model, onnx_path: str):
        """LoRA適用済みモデルをONNXにエクスポート（C++エンジンで使うため）"""
        model.eval()
        dummy_input = torch.randn(1, 80, 256).to(self.device)
        # Pyright の型チェックを回避するため、タプルで渡す
        torch.onnx.export(
            model,
            (dummy_input,),  # ← タプルにする
            onnx_path,
            input_names=["mel_input"],
            output_names=["audio_output"],
            dynamic_axes={"mel_input": {2: "frames"}, "audio_output": {1: "samples"}},
            opset_version=14,
        )
