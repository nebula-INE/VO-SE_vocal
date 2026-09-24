# modules/data/ust_parser.py
"""
VO-SE Vocal — UST ネイティブパーサー

UTAU の .ust ファイルを NoteEvent 互換形式に変換する。

従来の実装は mido 経由で MIDI として処理していたため、
以下の UST 固有情報が全て失われていた:
  - Flags    : 息音化・スタッカート等の音色フラグ (例: "g-5B50")
  - Vibrato  : ビブラート開始位置・深さ・速度・強度の個別指定
  - Modulation : ピッチモジュレーション深度 (0–100)
  - Intensity  : ノート音量 (0–200、デフォルト100)
  - PBW/PBS/PBY/PBM : ポルタメント形状

本モジュールはこれらを全て保持し、NoteEvent の拡張フィールドとして返す。

クラス:
  UstVibratoParams  : ビブラート設定の dataclass
  UstNote           : UST ノート 1 個の全パラメーターを保持する dataclass
  UstProject        : UST プロジェクト全体 (Tempo・Track・Notes)
  UstParser         : .ust ファイル → UstProject の変換器
  UstConverter      : UstNote → NoteEvent (data_models.NoteEvent 互換辞書)

使い方:
    parser = UstParser()
    project = parser.load("/path/to/song.ust")
    note_dicts = UstConverter.to_note_dicts(project)
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# UTAU のデフォルトテンポ
_DEFAULT_TEMPO = 120.0
# UTAU のデフォルト解像度 (480 ticks / 拍)
_TICKS_PER_BEAT = 480


# ---------------------------------------------------------------------------
# データクラス
# ---------------------------------------------------------------------------

@dataclass
class UstVibratoParams:
    """
    UTAU の VBR= 行を表すデータクラス。

    フォーマット: VBR=length,cycle,depth,fade_in,fade_out,phase,height
      length   : ビブラートをかける長さ (ノート長の %)
      cycle    : 1周期の長さ (ms)
      depth    : 深さ (cents)
      fade_in  : フェードイン長 (% of vib length)
      fade_out : フェードアウト長 (% of vib length)
      phase    : 開始位相 (0–100)
      height   : ピッチ中心のオフセット (–100 ~ 100 cents)
    """
    length: float   = 0.0    # %
    cycle: float    = 160.0  # ms
    depth: float    = 35.0   # cents
    fade_in: float  = 20.0   # %
    fade_out: float = 20.0   # %
    phase: float    = 0.0    # 0–100
    height: float   = 0.0    # cents

    @property
    def depth_semitones(self) -> float:
        """深さをセミトーン単位で返す (100 cents = 1 semitone)"""
        return self.depth / 100.0

    @property
    def rate_hz(self) -> float:
        """サイクル長から周波数 (Hz) を返す"""
        return 1000.0 / self.cycle if self.cycle > 0 else 5.5


@dataclass
class UstNote:
    """
    .ust ファイルの [#XXXX] ブロック 1 個を表すデータクラス。
    全フィールドを保持する（省略なし）。
    """
    index: int                      # ノートインデックス (0 起点)
    length: int                     # ノート長 (ticks)
    lyric: str                      # 歌詞（ひらがな / "R" for rest）
    note_num: int                   # MIDI ノート番号 (60 = C4)
    tempo: float                    # このノートのテンポ (None なら直前を継承)

    # 音量・表情
    intensity: float   = 100.0      # 音量 (0–200)
    modulation: float  = 100.0      # ピッチモジュレーション深度 (0–200)
    flags: str         = ""         # 音色フラグ文字列 (例: "g-5B50")

    # ポルタメント (PBS/PBW/PBY/PBM)
    pbs: str           = ""         # ポルタメント開始オフセット (ms または "ms;semitone")
    pbw: str           = ""         # ポルタメント各セグメント幅 (カンマ区切り ms)
    pby: str           = ""         # ポルタメント各制御点の高さ (カンマ区切り semitone)
    pbm: str           = ""         # ポルタメント補間モード ("" / "r" / "s" / "j")

    # ビブラート
    vibrato: Optional[UstVibratoParams] = None

    # 先行発声・オーバーラップ上書き (空文字 = oto.ini の値を使う)
    pre_utterance: Optional[float] = None  # ms
    overlap: Optional[float]       = None  # ms

    @property
    def is_rest(self) -> bool:
        return self.lyric.strip().upper() == "R"


@dataclass
class UstProject:
    """UST ファイル全体を保持するデータクラス"""
    version: str            = "UST Version 1.2"
    project_name: str       = "Untitled"
    output_file: str        = ""
    voice_dir: str          = ""
    cache_dir: str          = ""
    tempo: float            = _DEFAULT_TEMPO
    flags: str              = ""
    is_mode2: bool          = False

    notes: List[UstNote]    = field(default_factory=list)


# ---------------------------------------------------------------------------
# UstParser
# ---------------------------------------------------------------------------

class UstParser:
    """
    .ust ファイルを読み込んで UstProject を返すパーサー。

    エンコーディング: Shift-JIS (cp932) → UTF-8 → latin-1 の順で試みる。
    """

    # セクションヘッダーのパターン: [#XXXX] または [#SETTING] など
    _SECTION_RE = re.compile(r"^\[#([^\]]+)\]$")

    def load(self, path: str) -> UstProject:
        """
        .ust ファイルをパースして UstProject を返す。

        Args:
            path: .ust ファイルのパス

        Raises:
            FileNotFoundError: ファイルが見つからない場合
            ValueError:        .ust ではないファイルの場合
        """
        if not os.path.isfile(path):
            raise FileNotFoundError(f"UST ファイルが見つかりません: {path}")

        content = self._read_safe(path)
        lines = content.splitlines()
        project = self._parse(lines)

        # [FIX] UTAU では [#SETTING] の Tempo= がデフォルト 120 のままでも、
        # 実際の曲テンポは最初のノート ([#0000] 等) の Tempo= に書かれている
        # ケースが非常に多い。Web版 (src/utils/formatConverter.ts) と同じ
        # 優先順位で project.tempo を決定する:
        #
        #   1. 最初のノートに書かれた Tempo (= UTAU 実挙動での真のテンポ)
        #   2. [#SETTING] の Tempo (既に project.tempo に入っている)
        #   3. デフォルト 120 (UstProject のフィールド既定値)
        #
        # ここで上書きしておかないと、UI のテンポ表示・拍グリッド・
        # .ust 再書き出し時の [#SETTING] Tempo= がすべて 120 のまま
        # 固定されてしまう (音は UstNote.tempo 経由で正しく鳴るため
        # 気付きにくい)。
        if project.notes:
            first_tempo = project.notes[0].tempo
            if first_tempo and first_tempo > 0.0:
                project.tempo = first_tempo

        return project

    # ------------------------------------------------------------------
    # 内部実装
    # ------------------------------------------------------------------

    def _parse(self, lines: List[str]) -> UstProject:
        project = UstProject()
        current_section: Optional[str] = None
        current_block: Dict[str, str] = {}
        current_tempo: float = _DEFAULT_TEMPO

        for raw in lines:
            line = raw.strip()
            if not line:
                continue

            m = self._SECTION_RE.match(line)
            if m:
                # 前のブロックを処理
                if current_section is not None:
                    self._flush_block(project, current_section, current_block, current_tempo)
                    # ブロック内でテンポが変わっていれば引き継ぐ
                    if "Tempo" in current_block:
                        try:
                            current_tempo = float(current_block["Tempo"])
                        except ValueError:
                            pass

                current_section = m.group(1)
                current_block = {}
            else:
                if "=" in line:
                    key, _, value = line.partition("=")
                    current_block[key.strip()] = value.strip()

        # 最後のブロックを処理
        if current_section is not None:
            self._flush_block(project, current_section, current_block, current_tempo)

        return project

    def _flush_block(
        self,
        project: UstProject,
        section: str,
        block: Dict[str, str],
        current_tempo: float,
    ) -> None:
        """セクション種別に応じて project を更新する"""
        upper = section.upper()

        if upper == "SETTING":
            self._apply_setting(project, block)
        elif upper == "PREV" or upper == "NEXT" or upper == "TRACKEND":
            pass  # 特殊セクションはスキップ
        else:
            # [#0000], [#0001], ... ノートセクション
            try:
                index = int(section, 16) if len(section) == 4 else int(section)
            except ValueError:
                logger.debug("不明なセクション: [#%s]", section)
                return
            note = self._parse_note(index, block, current_tempo)
            if note is not None:
                project.notes.append(note)

    @staticmethod
    def _apply_setting(project: UstProject, block: Dict[str, str]) -> None:
        """[#SETTING] ブロックをプロジェクトに反映する"""
        if "Tempo" in block:
            try:
                project.tempo = float(block["Tempo"])
            except ValueError:
                pass
        if "ProjectName" in block:
            project.project_name = block["ProjectName"]
        if "OutFile" in block:
            project.output_file = block["OutFile"]
        if "VoiceDir" in block:
            project.voice_dir = block["VoiceDir"]
        if "CacheDir" in block:
            project.cache_dir = block["CacheDir"]
        if "Flags" in block:
            project.flags = block["Flags"]
        if "Mode2" in block:
            project.is_mode2 = block["Mode2"].strip() == "True"

    @staticmethod
    def _parse_note(
        index: int,
        block: Dict[str, str],
        current_tempo: float,
    ) -> Optional[UstNote]:
        """1 ノートブロックを UstNote に変換する"""
        if "Length" not in block or "NoteNum" not in block:
            return None  # 不完全なブロックは無視

        try:
            length = int(block["Length"])
        except ValueError:
            return None

        try:
            note_num = int(block["NoteNum"])
        except ValueError:
            return None

        lyric = block.get("Lyric", "R")

        # テンポ上書き (このノートだけ別テンポの場合)
        tempo = current_tempo
        if "Tempo" in block:
            try:
                tempo = float(block["Tempo"])
            except ValueError:
                pass

        # 数値フィールド
        def _fv(key: str, default: float) -> float:
            try:
                return float(block[key]) if key in block else default
            except ValueError:
                return default

        intensity   = _fv("Intensity",   100.0)
        modulation  = _fv("Modulation",  100.0)

        # 先行発声・オーバーラップ上書き（空の場合は None = oto.ini に委ねる）
        pre_utterance: Optional[float] = None
        if "PreUtterance" in block and block["PreUtterance"] != "":
            try:
                pre_utterance = float(block["PreUtterance"])
            except ValueError:
                pass

        ov: Optional[float] = None
        if "VoiceOverlap" in block and block["VoiceOverlap"] != "":
            try:
                ov = float(block["VoiceOverlap"])
            except ValueError:
                pass

        # ビブラート VBR=length,cycle,depth,fade_in,fade_out,phase,height
        vibrato: Optional[UstVibratoParams] = None
        if "VBR" in block:
            vbr_parts = [p.strip() for p in block["VBR"].split(",")]

            def _vbr(i: int, default: float) -> float:
                try:
                    return float(vbr_parts[i]) if i < len(vbr_parts) and vbr_parts[i] != "" else default
                except ValueError:
                    return default

            vibrato = UstVibratoParams(
                length   = _vbr(0, 0.0),
                cycle    = _vbr(1, 160.0),
                depth    = _vbr(2, 35.0),
                fade_in  = _vbr(3, 20.0),
                fade_out = _vbr(4, 20.0),
                phase    = _vbr(5, 0.0),
                height   = _vbr(6, 0.0),
            )

        return UstNote(
            index         = index,
            length        = length,
            lyric         = lyric,
            note_num      = note_num,
            tempo         = tempo,
            intensity     = intensity,
            modulation    = modulation,
            flags         = block.get("Flags", ""),
            pbs           = block.get("PBS",   ""),
            pbw           = block.get("PBW",   ""),
            pby           = block.get("PBY",   ""),
            pbm           = block.get("PBM",   ""),
            vibrato       = vibrato,
            pre_utterance = pre_utterance,
            overlap       = ov,
        )

    @staticmethod
    def _read_safe(path: str) -> str:
        """Shift-JIS / UTF-8 / latin-1 の順でファイルを読む"""
        for enc in ("cp932", "utf-8-sig", "utf-8", "latin-1"):
            try:
                with open(path, "r", encoding=enc, errors="strict") as f:
                    return f.read()
            except (UnicodeDecodeError, LookupError):
                continue
        with open(path, "r", encoding="cp932", errors="ignore") as f:
            return f.read()


# ---------------------------------------------------------------------------
# UstConverter
# ---------------------------------------------------------------------------

class UstConverter:
    """
    UstProject → NoteEvent 互換辞書リスト への変換器。

    返却する辞書は data_models.NoteEvent.from_dict() で復元できる形式。
    UST 固有フィールド (flags, modulation, vibrato, portamento) も
    拡張キーとして保持し、エンジン側で参照できるようにする。
    """

    @staticmethod
    def to_note_dicts(project: UstProject) -> List[Dict]:
        """
        UstProject.notes → NoteEvent 辞書リスト に変換。

        休符 ("R") はスキップせず、lyric="R" として保持する。
        タイムラインの時刻は ticks → 秒 に変換する。
        """
        results = []
        current_time_sec = 0.0

        for ust_note in project.notes:
            # ticks → 秒変換 (テンポはノート毎に異なる可能性がある)
            beats = ust_note.length / _TICKS_PER_BEAT
            duration_sec = beats * (60.0 / ust_note.tempo)

            # 先行発声: UST 上書き値 > oto.ini (呼び出し元で解決) > デフォルト
            # ここでは UST 値のみを記録し、oto.ini との最終調整は text_analyzer 側に委ねる
            pre_ms = ust_note.pre_utterance  # None = oto.ini に委ねる
            ov_ms  = ust_note.overlap        # None = oto.ini に委ねる

            # ビブラートカーブの生成
            vibrato_depth = 0.0
            vibrato_rate  = 5.5
            if ust_note.vibrato is not None:
                vibrato_depth = ust_note.vibrato.depth_semitones
                vibrato_rate  = ust_note.vibrato.rate_hz

            note_dict: Dict = {
                # NoteEvent 標準フィールド
                "note_number":   ust_note.note_num,
                "start_time":    current_time_sec,
                "duration":      duration_sec,
                "lyric":         ust_note.lyric,
                "velocity":      int(ust_note.intensity * 127.0 / 200.0),  # 0–127 に正規化
                "vibrato_depth": vibrato_depth,
                "vibrato_rate":  vibrato_rate,

                # 先行発声・オーバーラップ (ms、None なら oto.ini 値を使う)
                "pre_utterance": pre_ms,
                "overlap":       ov_ms,

                # UST 拡張フィールド (エンジン側が参照可能)
                "_ust_flags":      ust_note.flags,
                "_ust_modulation": ust_note.modulation,
                "_ust_pbs":        ust_note.pbs,
                "_ust_pbw":        ust_note.pbw,
                "_ust_pby":        ust_note.pby,
                "_ust_pbm":        ust_note.pbm,
                "_ust_intensity":  ust_note.intensity,
            }

            # ビブラートオブジェクトも保持
            if ust_note.vibrato is not None:
                note_dict["_ust_vibrato"] = {
                    "length":    ust_note.vibrato.length,
                    "cycle":     ust_note.vibrato.cycle,
                    "depth":     ust_note.vibrato.depth,
                    "fade_in":   ust_note.vibrato.fade_in,
                    "fade_out":  ust_note.vibrato.fade_out,
                    "phase":     ust_note.vibrato.phase,
                    "height":    ust_note.vibrato.height,
                }

            results.append(note_dict)
            current_time_sec += duration_sec

        return results

    @staticmethod
    def extract_portamento_curve(
        ust_note: UstNote,
        resolution: int = 128,
    ) -> List[float]:
        """
        PBS/PBW/PBY から ピッチベンドカーブ（semitone 単位）を生成する。

        Args:
            ust_note:   対象ノート
            resolution: サンプル数

        Returns:
            length=resolution の float リスト (単位: semitone)
        """
        curve = [0.0] * resolution

        if not ust_note.pbw:
            return curve

        try:
            widths  = [float(w) for w in ust_note.pbw.split(",") if w.strip()]
            heights = [float(h) for h in ust_note.pby.split(",") if h.strip()] if ust_note.pby else []

            # pbs の開始オフセット (ms) と開始ピッチ (semitone)
            pbs_parts = ust_note.pbs.split(";")
            pbs_offset_ms   = float(pbs_parts[0]) if pbs_parts[0].strip() else 0.0
            pbs_start_pitch = float(pbs_parts[1]) if len(pbs_parts) > 1 and pbs_parts[1].strip() else 0.0

            # ノート全長 (ms) で正規化してカーブを生成
            total_width_ms = sum(widths)
            if total_width_ms <= 0:
                return curve

            control_points: List[Tuple[float, float]] = [(pbs_offset_ms, pbs_start_pitch)]
            t = pbs_offset_ms
            for i, w in enumerate(widths):
                t += w
                h = heights[i] if i < len(heights) else 0.0
                control_points.append((t, h))
            control_points.append((total_width_ms + pbs_offset_ms + 10.0, 0.0))

            cp_times  = [p[0] for p in control_points]
            cp_values = [p[1] for p in control_points]

            # linear interpolation
            step = total_width_ms / resolution
            for j in range(resolution):
                t_j = pbs_offset_ms + j * step
                curve[j] = float(_interp(t_j, cp_times, cp_values))

        except Exception as exc:
            logger.debug("ポルタメントカーブ生成失敗: %s", exc)

        return curve


def _interp(x: float, xs: List[float], ys: List[float]) -> float:
    """単純な線形補間（numpy 不要版）"""
    if not xs or x <= xs[0]:
        return ys[0] if ys else 0.0
    if x >= xs[-1]:
        return ys[-1]
    for i in range(len(xs) - 1):
        if xs[i] <= x <= xs[i + 1]:
            t = (x - xs[i]) / (xs[i + 1] - xs[i])
            return ys[i] + t * (ys[i + 1] - ys[i])
    return 0.0
