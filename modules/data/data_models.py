# data_models.py

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any, Union
import json

@dataclass
class PitchEvent:
    """ピッチベンド（オートメーション）の1点を示すデータ構造"""
    time: float   # 秒単位
    # [解決] Pyrightエラー回避のため float に変更。
    # 内部計算や描画は float で行い、MIDI書き出し等の最終工程でのみ int() 変換します。
    value: float  

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'PitchEvent':
        return PitchEvent(**data)


@dataclass
class NoteEvent:
    """音符および読み上げユニットのデータ構造"""
    # 基本パラメータ
    note_number: int            # MIDIノート番号 (60=C4, 69=A4)
    start_time: float           # 開始時間（秒）
    duration: float             # 長さ（秒）
    lyric: str = "あ"            # 歌詞
    phonemes: Union[List[str], str] = field(default_factory=list) # 解析済み音素
    velocity: int = 100         # 音の強さ (0-127)
    
    # --- 歌唱（Singing）用パラメータ ---
    vibrato_depth: float = 0.0  # ビブラートの深さ (0.0 - 1.0)
    vibrato_rate: float = 5.5   # ビブラートの速さ (Hz)
    formant_shift: float = 0.0  # フォルマントシフト
    
    # --- 読み上げ（Talk）用パラメータ ---
    # 数値が入っている場合は抑揚スライド。Noneは歌唱固定ピッチ。
    pitch_end: Optional[float] = None 
    
    # --- AI/エンジン解析結果 (原音設定の要素) ---
    onset: float = 0.0          # 立ち上がり(物理開始)
    pre_utterance: float = 0.0  # 先行発声
    overlap: float = 0.0        # 前の音との重なり
    has_analysis: bool = False  # 解析済みフラグ

    # --- GUI/編集用フラグ（シリアライズ対象外） ---
    is_selected: bool = field(default=False, repr=False)
    is_playing: bool = field(default=False, repr=False)

    def __repr__(self):
        mode = "Talk" if self.pitch_end is not None else "Sing"
        return f"Note({mode}, pitch={self.note_number}, lyric='{self.lyric}', start={self.start_time:.2f}s)"

    @property
    def lyrics(self) -> str:
        return self.lyric

    @lyrics.setter
    def lyrics(self, value: str) -> None:
        self.lyric = value

    def to_dict(self) -> Dict[str, Any]:
        """保存用に辞書化（GUI用フラグは除外、_ust_* 拡張は保持）"""
        d = asdict(self)
        # 不要な内部状態を削除
        d.pop('is_selected', None)
        d.pop('is_playing', None)

        # UST 拡張属性 (_ust_tempo, _ust_flags 等) を永続化。
        # asdict() は dataclass のフィールドしか含めないため、
        # setattr で動的に付与された属性はここで明示的に拾う。
        for k, v in self.__dict__.items():
            if k.startswith("_ust_"):
                d[k] = v

        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'NoteEvent':
        """辞書データから復元（不要なキーを無視、_ust_* 拡張は属性として復元）"""
        # クラスのフィールドに存在しないキーを除去して初期化（後方互換性のため）
        valid_keys = cls.__dataclass_fields__.keys()

        # 古い保存データ形式（start, note_num等）を現在のフィールド名にマッピング
        mapping = {
            "start": "start_time",
            "note_num": "note_number",
            "lyrics": "lyric"
        }

        normalized_data = {}
        ust_extras = {}
        for k, v in data.items():
            new_key = mapping.get(k, k)
            if new_key in valid_keys:
                normalized_data[new_key] = v
            elif new_key.startswith("_ust_"):
                # UST 拡張属性は dataclass フィールドではないので
                # いったん分離しておき、インスタンス生成後に setattr で戻す。
                ust_extras[new_key] = v

        obj = cls(**normalized_data)
        for k, v in ust_extras.items():
            setattr(obj, k, v)
        return obj


@dataclass
class CharacterInfo:
    """音源キャラクター（ボイスバンク）の定義"""
    id: str
    name: str
    audio_dir: str
    description: str = ""
    waveform_type: str = "sample_based"
    engine_params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectModel:
    """楽曲プロジェクト全体のデータを管理"""
    project_name: str = "Untitled"
    tempo: float = 120.0
    notes: List[NoteEvent] = field(default_factory=list)
    pitch_automation: List[PitchEvent] = field(default_factory=list)
    character_id: str = ""
    
    def serialize(self) -> Dict[str, Any]:
        return {
            "project_name": self.project_name,
            "tempo": self.tempo,
            "character_id": self.character_id,
            "notes": [n.to_dict() for n in self.notes],
            "pitch_automation": [p.to_dict() for p in self.pitch_automation]
        }

    def save_to_file(self, file_path: str):
        """JSONとしてファイル保存"""
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(self.serialize(), f, indent=4, ensure_ascii=False)
