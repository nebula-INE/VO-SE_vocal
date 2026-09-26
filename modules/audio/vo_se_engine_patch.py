# modules/audio/vo_se_engine_patch.py
"""
VO-SE Vocal — vo_se_engine.py への差分パッチ（ポルタメント対応版）

このファイルは vo_se_engine.py の VO_SE_Engine クラスに対して
モンキーパッチを当てる形で優先度1〜2の機能を追加する。

本番運用では vo_se_engine.py 本体にマージすること。

追加・修正メソッド:
  [NEW-1] VO_SE_Engine.refresh_voice_library_v2()
          → VcvResolver を初期化し、音源ロード時に VCV 対応フラグをセットする
  [NEW-2] VO_SE_Engine.export_to_wav_v2()
          → VcvResolver を通じた VCV 解決 + UST vibrato カーブ + **ポルタメントカーブ** の注入
  [NEW-3] VO_SE_Engine._build_vibrato_curves()
          → UstNote の VBR パラメーターから depth/rate カーブを生成
  [NEW-4] VO_SE_Engine.load_ust_project()
          → UST ファイルを UstParser で読み込み、notes_list に変換して返す
  [NEW-5] VO_SE_Engine._build_portamento_curve()   ★新規
          → UST の PBS/PBW/PBY からピッチオフセットカーブ（セント単位）を生成
"""
from __future__ import annotations

import math
import os
import ctypes
import logging
from typing import Any, Dict, List, Optional, Tuple
import re

import numpy as np

from modules.data.oto_parser import OtoParser
from modules.audio.vcv_resolver import VcvResolver
from modules.data.ust_parser import UstParser, UstConverter, UstVibratoParams

logger = logging.getLogger(__name__)


def build_vibrato_curves(
    duration_sec: float,
    vibrato_params: Optional[UstVibratoParams],
    resolution: int = 128,
    note_start_offset_sec: float = 0.0,
) -> Tuple[np.ndarray, np.ndarray]:
    """UstVibratoParams から depth/rate カーブを生成する。"""
    depth_curve = np.zeros(resolution, dtype=np.float64)
    rate_curve = np.zeros(resolution, dtype=np.float64)

    if vibrato_params is None or vibrato_params.length <= 0:
        return depth_curve, rate_curve

    times = np.linspace(0.0, duration_sec + note_start_offset_sec, resolution)
    vib_start = note_start_offset_sec + duration_sec * (1.0 - vibrato_params.length / 100.0)
    vib_end = note_start_offset_sec + duration_sec

    for idx, t in enumerate(times):
        if t < vib_start or t >= vib_end:
            continue

        vib_elapsed = t - vib_start
        vib_total = vib_end - vib_start
        fade_in_sec = vib_total * vibrato_params.fade_in / 100.0
        fade_out_sec = vib_total * vibrato_params.fade_out / 100.0

        if vib_elapsed < fade_in_sec and fade_in_sec > 0:
            env = vib_elapsed / fade_in_sec
        elif vib_elapsed > vib_total - fade_out_sec and fade_out_sec > 0:
            env = (vib_total - vib_elapsed) / fade_out_sec
        else:
            env = 1.0

        # C++ apply_vibrato() の depth は「15cent = 1.0」の正規化値。
        # UTAU VBR depth は cents なので、単位を合わせてから渡す。
        # (35 cents -> 35 / 15 = 2.333..., すなわち約35cent)
        depth_curve[idx] = (vibrato_params.depth / 15.0) * env
        rate_curve[idx] = vibrato_params.rate_hz

    return depth_curve, rate_curve


def parse_ust_flag_overrides(
    flags: str,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """UST Flags の g/B/t をレンダー用の0..1値へ正規化する。"""
    gender = None
    tension = None
    breath = None
    if not flags:
        return gender, tension, breath

    for match in re.finditer(r"([gBt])([+-]?\d+(?:\.\d+)?)", str(flags)):
        letter = match.group(1)
        value = float(match.group(2))
        if letter == "g":
            gender = float(np.clip(0.5 + value / 200.0, 0.0, 1.0))
        elif letter == "B":
            breath = float(np.clip(value / 100.0, 0.0, 1.0))
        elif letter == "t":
            tension = float(np.clip(value / 100.0, 0.0, 1.0))
    return gender, tension, breath


def build_portamento_curve(
    ust_note: Any,
    resolution: int = 128,
) -> Optional[np.ndarray]:
    """UST ノートからピッチオフセットカーブを生成する。"""
    if isinstance(ust_note, dict):
        pbs = ust_note.get("_ust_pbs", "")
        pbw = ust_note.get("_ust_pbw", "")
        pby = ust_note.get("_ust_pby", "")
        pbm = ust_note.get("_ust_pbm", "")
    else:
        pbs = getattr(ust_note, "_ust_pbs", "")
        pbw = getattr(ust_note, "_ust_pbw", "")
        pby = getattr(ust_note, "_ust_pby", "")
        pbm = getattr(ust_note, "_ust_pbm", "")

    if not pbw:
        return None

    from modules.data.ust_parser import UstNote

    # NoteEvent 互換辞書/オブジェクトの場合も、実際のノート長とテンポを
    # 使ってポルタメントの時間軸を構築する。固定の480tick/120BPMだと
    # テンポ変化や長音符でカーブ位置がずれる。
    if isinstance(ust_note, dict):
        duration_sec = float(ust_note.get("duration", 0.0))
        tempo = float(ust_note.get("_ust_tempo", 120.0) or 120.0)
        length = max(1, int(round(duration_sec * tempo * 480.0 / 60.0)))
    else:
        duration_sec = float(getattr(ust_note, "duration", 0.0) or 0.0)
        tempo = float(getattr(ust_note, "_ust_tempo", 120.0) or 120.0)
        if duration_sec > 0.0:
            length = max(1, int(round(duration_sec * tempo * 480.0 / 60.0)))
        else:
            length = int(getattr(ust_note, "length", 480))
            tempo = float(getattr(ust_note, "tempo", tempo) or tempo)

    dummy_note = UstNote(
        index=0,
        length=length,
        lyric="",
        note_num=60,
        tempo=max(tempo, 1.0),
        pbs=pbs,
        pbw=pbw,
        pby=pby,
        pbm=pbm,
    )
    curve_list = UstConverter.extract_portamento_curve(dummy_note, resolution)
    if not curve_list or len(curve_list) != resolution:
        return None

    # UstConverter は半音単位で返すが、C++ NoteEvent.portamento_offsets は
    # セント単位として解釈するため、ここで 100 倍して単位を統一する。
    return np.asarray(curve_list, dtype=np.float64) * 100.0


def _refresh_voice_library_v2(self) -> None:
    """VcvResolver を再初期化しながら音源フォルダを再スキャンする。"""
    self.oto_map = {}

    if not os.path.exists(self.voice_lib_path):
        os.makedirs(self.voice_lib_path, exist_ok=True)
        self.vcv_resolver = None
        return

    if not hasattr(self, "oto_parser") or self.oto_parser is None:
        self.oto_parser = OtoParser()

    self.oto_parser.clear()

    for root, _dirs, files in os.walk(self.voice_lib_path):
        files_lower = [f.lower() for f in files]
        if "oto.ini" in files_lower:
            real_name = files[files_lower.index("oto.ini")]
            ini_path = os.path.join(root, real_name)
            loaded = self.oto_parser.load_oto_file(ini_path)
            logger.debug("oto.ini ロード: %d エントリ (%s)", loaded, ini_path)

        for fname in files:
            if fname.lower().endswith(".wav"):
                lyric = os.path.splitext(fname)[0]
                self.oto_map[lyric] = os.path.abspath(os.path.join(root, fname))

    self.vcv_resolver = VcvResolver(self.oto_parser, use_g2p=True)
    logger.info(
        "音源ライブラリ更新: %d WAV / VCV=%s",
        len(self.oto_map),
        self.oto_parser.has_vcv(),
    )


def _export_to_wav_v2(
    self,
    notes,
    parameters,
    file_path,
    mode_flag: int = 0,
    progress_callback=None,
    cancel_check=None,
    **kwargs,
) -> str:
    """VCV + UST ビブラート + ポルタメント対応の WAV export。"""
    _ = (progress_callback, cancel_check, kwargs)

    if not self.lib:
        raise RuntimeError("Engine Core library missing!")

    oto_parser = getattr(self, "oto_parser", None)
    notes, timeline = self.text_analyzer.align_vocal_timing(notes, oto_parser)

    if timeline and hasattr(self, "pipeline_bridge") and self.pipeline_bridge:
        self.pipeline_bridge.send_timeline_to_core(timeline)

    note_count = len(notes)
    from modules.audio.vo_se_engine import CNoteEvent
    c_notes_array = (CNoteEvent * note_count)()
    self._temp_refs = []

    for i, note in enumerate(notes):
        vcv_resolver = getattr(self, "vcv_resolver", None)
        wav_path = ""

        if vcv_resolver is not None:
            prev_lyric = notes[i - 1].lyric if i > 0 else None
            _alias, oto_entry = vcv_resolver.resolve_note(note.lyric, prev_lyric)
            if oto_entry is not None:
                wav_path = oto_entry.wav_path

        if not wav_path or not os.path.exists(wav_path):
            wav_path = self.oto_map.get(note.lyric) or self.oto_map.get(
                getattr(note, "phonemes", ""), ""
            )
            # 未解決時にライブラリ先頭の別音素を使うと、
            # 「あ」が見つからないから「か」を歌う、といった誤発音になる。
            # 解決不能なノートは wav_path を空のままにし、C++ 側で無音として扱う。

        res = 128
        p_curve = self._get_sampled_curve(parameters["Pitch"], note, res, is_pitch=True).astype(np.float64)
        g_curve = self._get_sampled_curve(parameters["Gender"], note, res).astype(np.float64)
        t_curve = self._get_sampled_curve(parameters["Tension"], note, res).astype(np.float64)
        b_curve = self._get_sampled_curve(parameters["Breath"], note, res).astype(np.float64)
        flag_gender, flag_tension, flag_breath = parse_ust_flag_overrides(
            str(getattr(note, "_ust_flags", "") or "")
        )
        if flag_gender is not None:
            g_curve.fill(flag_gender)
        if flag_tension is not None:
            t_curve.fill(flag_tension)
        if flag_breath is not None:
            b_curve.fill(flag_breath)

        ust_vib_dict = getattr(note, "_ust_vibrato", None)
        ust_vib: Optional[UstVibratoParams] = None
        if isinstance(ust_vib_dict, dict):
            try:
                ust_vib = UstVibratoParams(**ust_vib_dict)
            except Exception:
                pass

        if ust_vib is not None:
            # UST VBR は phase / height / fade を含むため、ネイティブ側の
            # 簡易 apply_vibrato() には渡さず pitch_curve に正確に焼き込む。
            duration_sec = float(note.duration)
            times = np.linspace(0.0, duration_sec, res)
            vib_start = duration_sec * (1.0 - ust_vib.length / 100.0)
            vib_total = max(duration_sec - vib_start, 0.0)
            fade_in_sec = vib_total * ust_vib.fade_in / 100.0
            fade_out_sec = vib_total * ust_vib.fade_out / 100.0

            semitone_offset = np.zeros(res, dtype=np.float64)
            for idx, t in enumerate(times):
                if t < vib_start or vib_total <= 0.0:
                    continue
                elapsed = t - vib_start
                env = 1.0
                if fade_in_sec > 0.0 and elapsed < fade_in_sec:
                    env = elapsed / fade_in_sec
                if fade_out_sec > 0.0 and elapsed > vib_total - fade_out_sec:
                    env = min(env, (vib_total - elapsed) / fade_out_sec)
                env = max(0.0, min(1.0, env))

                phase = ust_vib.phase / 100.0
                cycles = elapsed * (1000.0 / max(ust_vib.cycle, 1e-6)) / 1000.0 + phase
                cents = math.sin(2.0 * math.pi * cycles) * ust_vib.depth * env + ust_vib.height
                semitone_offset[idx] = cents / 100.0

            p_curve *= np.power(2.0, semitone_offset / 12.0)
            vib_depth = np.zeros(res, dtype=np.float64)
            vib_rate = np.zeros(res, dtype=np.float64)
        elif float(getattr(note, "vibrato_depth", 0.0)) > 0:
            depth = float(note.vibrato_depth)
            rate = float(getattr(note, "vibrato_rate", 5.5))
            times = np.linspace(0.0, float(note.duration), res)
            vib_depth = (np.sin(2 * math.pi * rate * times) * depth).astype(np.float64)
            vib_rate = np.full(res, rate, dtype=np.float64)
        else:
            vib_depth = np.zeros(res, dtype=np.float64)
            vib_rate = np.zeros(res, dtype=np.float64)

        portamento_curve = build_portamento_curve(note, resolution=res)
        if portamento_curve is not None:
            portamento_arr = portamento_curve.astype(np.float64)
            portamento_len = res
        else:
            portamento_arr = None
            portamento_len = 0

        self._temp_refs.extend([p_curve, g_curve, t_curve, b_curve, vib_depth, vib_rate])
        if portamento_arr is not None:
            self._temp_refs.append(portamento_arr)

        # ctypes.c_char_p の None は C++ 側の nullptr になる。
        # b"" は「NUL終端の空文字列へのポインタ」であり nullptr ではない。
        c_notes_array[i].wav_path = wav_path.encode("utf-8") if wav_path else None
        c_notes_array[i].pitch_curve = p_curve.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].gender_curve = g_curve.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].tension_curve = t_curve.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].breath_curve = b_curve.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].vibrato_depth_curve = vib_depth.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].vibrato_rate_curve = vib_rate.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].pitch_length = res
        c_notes_array[i].vibrato_curve_length = res
        c_notes_array[i].intensity = float(np.clip(getattr(note, "_ust_intensity", 100.0), 0.0, 200.0))
        c_notes_array[i].modulation = float(np.clip(getattr(note, "_ust_modulation", 100.0), 0.0, 100.0))

        if portamento_arr is not None:
            c_notes_array[i].portamento_offsets = portamento_arr.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
            c_notes_array[i].portamento_length = portamento_len
        else:
            c_notes_array[i].portamento_offsets = None
            c_notes_array[i].portamento_length = 0

    try:
        self.lib.execute_render(
            c_notes_array,
            note_count,
            os.path.abspath(file_path).encode("utf-8"),
            mode_flag,
        )
    finally:
        self._temp_refs = []

    return os.path.abspath(file_path)


def _load_ust_project(self, ust_path: str) -> List[Dict[str, Any]]:
    """UST ファイルをネイティブパーサーで読み込み、NoteEvent 互換辞書リストを返す。"""
    parser = UstParser()
    project = parser.load(ust_path)

    voice_dir = str(project.voice_dir or "").strip()
    if voice_dir:
        current_voice_dir = str(getattr(self, "voice_lib_path", "") or "")
        voice_dir = voice_dir.replace("%VOICE%", current_voice_dir)
        if not os.path.isabs(voice_dir):
            voice_dir = os.path.abspath(os.path.join(
                os.path.dirname(os.path.abspath(ust_path)), voice_dir
            ))
        else:
            voice_dir = os.path.abspath(voice_dir)
        if os.path.isdir(voice_dir):
            self.voice_lib_path = voice_dir
            self._ust_voice_dir = voice_dir
            _refresh_voice_library_v2(self)
        else:
            logger.warning("UST VoiceDir が見つかりません: %s", voice_dir)

    note_dicts = UstConverter.to_note_dicts(project)
    logger.info(
        "UST ロード完了: %d ノート / Tempo=%.1f (%s)",
        len(note_dicts),
        project.tempo,
        os.path.basename(ust_path),
    )
    return note_dicts


def apply_patch(engine_class) -> None:
    """VO_SE_Engine クラスに新メソッドをバインドする。"""
    engine_class.refresh_voice_library_v2 = _refresh_voice_library_v2
    engine_class.export_to_wav_v2 = _export_to_wav_v2
    engine_class.load_ust_project = _load_ust_project
    logger.info("VO_SE_Engine パッチ適用完了 (VCV + UST + Vibrato + Portamento)")
