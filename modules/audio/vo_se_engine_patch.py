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
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple
import re

import numpy as np

try:
    import soundfile as sf
except ImportError:
    sf = None

from modules.data.oto_parser import OtoParser
from modules.audio.vcv_resolver import VcvResolver
from modules.audio.cvvc_resolver import CvvcResolver
from modules.data.ust_parser import UstParser, UstConverter, UstVibratoParams

logger = logging.getLogger(__name__)

# The native renderer owns process-wide OTO/cache state. Per-engine locks are
# insufficient when a secondary launcher or a voice-bank switch uses another
# VO_SE_Engine instance in the same process.
_NATIVE_ENGINE_LOCK = threading.RLock()


@contextmanager
def voice_library_transaction(engine: Any, voice_path: Optional[str] = None) -> Iterator[None]:
    """Temporarily route one render through a Voicebank under the native-state lock."""
    current_path = str(getattr(engine, "voice_lib_path", "") or "")
    refresh = getattr(engine, "refresh_voice_library_v2", None)
    if not current_path or not callable(refresh):
        yield
        return

    with _NATIVE_ENGINE_LOCK:
        previous_path = os.path.abspath(current_path)
        default_path = str(
            getattr(engine, "_default_voice_lib_path", previous_path) or previous_path
        )
        target_path = (
            os.path.abspath(os.path.expanduser(str(voice_path)))
            if voice_path else os.path.abspath(default_path)
        )
        if voice_path and (
            not os.path.isdir(target_path)
            or not os.path.isfile(os.path.join(target_path, "oto.ini"))
        ):
            raise FileNotFoundError(f"指定されたUTAU音源が見つかりません: {target_path}")

        had_ust_voice_dir = "_ust_voice_dir" in getattr(engine, "__dict__", {})
        previous_ust_voice_dir = getattr(engine, "_ust_voice_dir", None)
        switched = target_path != previous_path
        try:
            if switched:
                engine.voice_lib_path = target_path
                refresh()
            yield
        finally:
            if switched:
                engine.voice_lib_path = previous_path
                refresh()
            if had_ust_voice_dir:
                engine._ust_voice_dir = previous_ust_voice_dir
            elif hasattr(engine, "_ust_voice_dir"):
                try:
                    delattr(engine, "_ust_voice_dir")
                except AttributeError:
                    pass


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
) -> Tuple[Optional[float], Optional[float], Optional[float], float]:
    """UST Flags の g/B/t をレンダー値へ正規化する。t は10cent単位。"""
    gender = None
    tension = None
    breath = None
    pitch_shift_cents = 0.0
    if not flags:
        return gender, tension, breath, pitch_shift_cents

    for match in re.finditer(r"([gBt])([+-]?\d+(?:\.\d+)?)", str(flags)):
        letter = match.group(1)
        value = float(match.group(2))
        if letter == "g":
            gender = float(np.clip(0.5 + value / 200.0, 0.0, 1.0))
        elif letter == "B":
            breath = float(np.clip(value / 100.0, 0.0, 1.0))
        elif letter == "t":
            pitch_shift_cents += value * 10.0
    return gender, tension, breath, pitch_shift_cents


def _extract_ust_portamento_fields(note: Any) -> Tuple[str, str, str, str]:
    if isinstance(note, dict):
        return (
            str(note.get("_ust_pbs", "") or ""),
            str(note.get("_ust_pbw", "") or ""),
            str(note.get("_ust_pby", "") or ""),
            str(note.get("_ust_pbm", "") or ""),
        )
    return (
        str(getattr(note, "_ust_pbs", getattr(note, "pbs", "")) or ""),
        str(getattr(note, "_ust_pbw", getattr(note, "pbw", "")) or ""),
        str(getattr(note, "_ust_pby", getattr(note, "pby", "")) or ""),
        str(getattr(note, "_ust_pbm", getattr(note, "pbm", "")) or ""),
    )


def _get_note_duration_ms(note: Any) -> float:
    if isinstance(note, dict):
        duration_sec = float(note.get("duration", 0.0) or 0.0)
        tempo = float(note.get("_ust_tempo", 120.0) or 120.0)
        length = int(note.get("_ust_length_ticks", 0) or 0)
    else:
        duration_sec = float(getattr(note, "duration", 0.0) or 0.0)
        tempo = float(getattr(note, "_ust_tempo", 120.0) or 120.0)
        length = int(getattr(note, "_ust_length_ticks", getattr(note, "length", 0)) or 0)
    if duration_sec > 0.0:
        return duration_sec * 1000.0
    if length > 0:
        return float(length) * 60000.0 / max(tempo, 1.0) / 480.0
    return 0.0


def _get_note_midi_num(note: Any) -> int:
    if isinstance(note, dict):
        return int(note.get("note_number", note.get("note_num", 60)))
    return int(getattr(note, "note_number", getattr(note, "note_num", 60)))


def build_portamento_curve(
    ust_note: Any,
    resolution: int = 128,
    next_note: Any = None,
    preutterance_ms: float = 0.0,
    time_offset_ms: float = 0.0,
) -> Optional[np.ndarray]:
    """UST ノートからピッチオフセットカーブ（セント単位）を生成する。

    - preutterance_ms > 0 の場合、C++ の lead_frames 分の負時間域 [-lead_frames*5ms, 0) を
      先頭にパディングし、portamento_length = resolution + lead_frames を返す。
    - 連続する後続ノート next_note が負の PBS を持つ場合、現ノート末尾 (tp >= next_pbs_ms)
      に次ノートの先行ポルタメントを織り込む（修正B）。
    """
    from modules.data.ust_parser import _interp_shape, has_explicit_ust_portamento

    pbs, pbw, pby, pbm = _extract_ust_portamento_fields(ust_note)
    duration_ms = _get_note_duration_ms(ust_note)
    if duration_ms <= 0.0:
        return None

    has_cur_pbw = has_explicit_ust_portamento(ust_note)
    cur_pts: List[Tuple[float, float]] = []
    cur_shapes: List[str] = []
    if has_cur_pbw:
        try:
            eval_dur_ms = max(duration_ms, 500.0) if time_offset_ms < 0.0 else duration_ms
            cur_pts, cur_shapes, _ = UstConverter._build_ust_control_points(
                pbs, pbw, pby, pbm, eval_dur_ms
            )
            if len(cur_pts) < 2:
                has_cur_pbw = False
        except Exception:
            has_cur_pbw = False

    has_next_pre_bite = False
    next_pbs_ms = 0.0
    next_delta_semitones = 0.0
    next_pts: List[Tuple[float, float]] = []
    next_shapes: List[str] = []
    if next_note is not None:
        n_pbs, n_pbw, n_pby, n_pbm = _extract_ust_portamento_fields(next_note)
        if has_explicit_ust_portamento(next_note):
            try:
                next_dur_ms = _get_note_duration_ms(next_note)
                next_pts, next_shapes, next_pbs_ms = UstConverter._build_ust_control_points(
                    n_pbs, n_pbw, n_pby, n_pbm, max(next_dur_ms, 500.0)
                )
                if next_pbs_ms < 0.0 and len(next_pts) >= 2:
                    has_next_pre_bite = True
                    next_delta_semitones = float(_get_note_midi_num(next_note) - _get_note_midi_num(ust_note))
            except Exception:
                has_next_pre_bite = False

    if not has_cur_pbw and not has_next_pre_bite:
        return None

    lead_frames = int(math.ceil(max(0.0, float(preutterance_ms)) / 5.0)) if preutterance_ms > 0.0 else 0
    total_len = resolution + lead_frames

    # When resolution matches C++ 5ms frame quantization, sample strictly on the 5ms grid.
    # Otherwise fall back to uniform sampling across duration_ms for standalone callers.
    if abs(duration_ms - resolution * 5.0) <= 5.0 or lead_frames > 0 or time_offset_ms != 0.0:
        step_ms = 5.0
    else:
        step_ms = duration_ms / max(resolution - 1, 1)

    curve_semitones = np.zeros(total_len, dtype=np.float64)
    for j in range(total_len):
        t_j = time_offset_ms + (j - lead_frames) * step_ms
        val = 0.0
        if has_cur_pbw:
            val += float(_interp_shape(t_j, cur_pts, cur_shapes))
        if has_next_pre_bite and time_offset_ms >= 0.0:
            tp = t_j - duration_ms
            if tp >= next_pbs_ms:
                val += next_delta_semitones + float(_interp_shape(tp, next_pts, next_shapes))
        curve_semitones[j] = val

    # UstConverter は半音単位で扱うが、C++ NoteEvent.portamento_offsets は
    # セント単位として解釈するため、ここで 100 倍して単位を統一する。
    return curve_semitones * 100.0


def _refresh_voice_library_v2_unlocked(self) -> None:
    """VcvResolver を再初期化しながら音源フォルダを再スキャンする。"""
    self.oto_map = {}

    if not os.path.exists(self.voice_lib_path):
        os.makedirs(self.voice_lib_path, exist_ok=True)
        self._loaded_oto_ini_paths = tuple()
        self.vcv_resolver = None
        self.cvvc_resolver = None
        sync_oto = getattr(self, "set_oto_data", None)
        if callable(sync_oto):
            sync_oto({})
        return

    if not hasattr(self, "oto_parser") or self.oto_parser is None:
        self.oto_parser = OtoParser()

    # OtoParser owns bounded root discovery and deterministic main/fallback
    # ordering. Do not independently walk voice_lib_path here: that used to
    # make Python and the native OTO database disagree about duplicate aliases.
    self.oto_parser.load_voice_dir(
        self.voice_lib_path,
        use_cache=False,
        reset=True,
        write_cache=False,
    )
    self._loaded_oto_ini_paths = tuple(
        getattr(self.oto_parser, "loaded_oto_ini_paths", ())
    )

    # Preserve the legacy basename fallback map, but walk each layer separately
    # and prune nested oto.ini layers so a parent layer cannot steal a child
    # layer's WAV path. The layer order is the same first-wins order as _db.
    layer_dirs = tuple(getattr(self.oto_parser, "loaded_layers", ()))
    layer_realpaths = {os.path.realpath(path) for path in layer_dirs}
    for layer_dir in layer_dirs:
        for root, dirs, files in os.walk(layer_dir, topdown=True, followlinks=False):
            dirs[:] = sorted(
                directory
                for directory in dirs
                if not os.path.islink(os.path.join(root, directory))
                and os.path.realpath(os.path.join(root, directory)) not in layer_realpaths
            )
            for fname in files:
                if not fname.lower().endswith(".wav"):
                    continue
                lyric = os.path.splitext(fname)[0]
                self.oto_map.setdefault(
                    lyric,
                    os.path.abspath(os.path.join(root, fname)),
                )

    # Include configured aliases even when their WAV filenames differ from the
    # lyric. OtoEntry.wav_path remains relative to the oto.ini that declared it.
    for alias, entry in getattr(self.oto_parser, "_db", {}).items():
        self.oto_map.setdefault(alias, entry.wav_path)

    self.vcv_resolver = VcvResolver(self.oto_parser, use_g2p=True)
    self.cvvc_resolver = CvvcResolver(self.oto_parser)

    sync_oto = getattr(self, "set_oto_data", None)
    if callable(sync_oto):
        sync_oto(getattr(self.oto_parser, "_db", {}))

    logger.info(
        "音源ライブラリ更新: %d WAV / VCV=%s",
        len(self.oto_map),
        self.oto_parser.has_vcv(),
    )


def _refresh_voice_library_v2(self) -> None:
    """Serialize native OTO database replacement against every active render."""
    with _NATIVE_ENGINE_LOCK:
        _refresh_voice_library_v2_unlocked(self)


def _note_duration_frames(note, frame_period_ms: float = 5.0) -> int:
    """NoteEvent の実時間をネイティブ用フレーム数へ変換する。"""
    try:
        duration_sec = max(0.0, float(getattr(note, "duration", 0.0) or 0.0))
    except (TypeError, ValueError):
        duration_sec = 0.0
    return max(1, int(round(duration_sec * 1000.0 / frame_period_ms)))


def _phoneme_sequence_aliases(phonemes: Any) -> List[str]:
    """Return safe alias spellings for one consonant + one vowel phone pair.

    OpenJTalk emits pairs such as ["j", "i"] for "じ"; some banks store that
    sample as "ji". Never collapse longer phone sequences or special phones.
    """
    if isinstance(phonemes, str):
        phones = phonemes.split()
    elif isinstance(phonemes, (list, tuple)):
        phones = [str(phone or "").strip() for phone in phonemes]
    else:
        return []

    phones = [phone for phone in phones if phone]
    # "N" is the moraic nasal, not the consonant "n". Reject special phones
    # before lowercasing so ["N", "a"] can never become the guessed alias "na".
    if any(
        phone == "N" or phone.casefold() in {"cl", "pau", "sil", "sp"}
        for phone in phones
    ):
        return []
    phones = [phone.lower() for phone in phones]
    if len(phones) != 2:
        return []

    consonant, vowel = phones
    if vowel not in {"a", "i", "u", "e", "o"}:
        return []
    if (
        not consonant
        or not consonant.isascii()
        or not consonant.isalpha()
        or any(char in "aeiou" for char in consonant)
    ):
        return []

    return [f"{consonant}{vowel}", f"{consonant} {vowel}"]


def _lookup_oto_map_fallback(oto_map: Any, lyric: Any, phonemes: Any) -> str:
    """Look up exact lyric and conservative phone aliases in a legacy WAV map."""
    if not isinstance(oto_map, dict):
        return ""

    aliases: List[str] = []
    if isinstance(lyric, str) and lyric:
        aliases.append(lyric)

    if isinstance(phonemes, str) and phonemes:
        aliases.append(phonemes)
        aliases.extend(_phoneme_sequence_aliases(phonemes))
    elif isinstance(phonemes, (list, tuple)):
        if (
            len(phonemes) == 1
            and isinstance(phonemes[0], str)
            and phonemes[0]
        ):
            aliases.append(phonemes[0])
        aliases.extend(_phoneme_sequence_aliases(phonemes))

    for alias in dict.fromkeys(aliases):
        candidate = oto_map.get(alias)
        if isinstance(candidate, os.PathLike):
            candidate = os.fspath(candidate)
        if isinstance(candidate, str) and candidate:
            return candidate
    return ""


def _expected_render_duration_sec(notes) -> float:
    """v2 の絶対タイムラインから期待される WAV 長を秒で求める。"""
    end_sec = 0.0
    saw_timed_note = False
    sequential_sec = 0.0
    for note in notes:
        try:
            duration = max(0.0, float(getattr(note, "duration", 0.0) or 0.0))
        except (TypeError, ValueError):
            duration = 0.0
        try:
            start = float(getattr(note, "start_time", -1.0))
        except (TypeError, ValueError):
            start = -1.0
        if np.isfinite(start) and start >= 0.0:
            saw_timed_note = True
            end_sec = max(end_sec, start + duration)
        else:
            sequential_sec += duration
    return end_sec if saw_timed_note else sequential_sec


def _validate_rendered_wav(file_path: str, expected_duration_sec: float) -> None:
    """生成WAVの長さと無音化を検証し、壊れた書き出しを成功扱いしない。"""
    if sf is None:
        raise RuntimeError("WAV検証に必要な soundfile が利用できません。")

    try:
        info = sf.info(file_path)
        actual_duration = float(info.frames) / float(info.samplerate)
    except Exception as exc:
        raise RuntimeError(f"生成されたWAVを読み込めませんでした: {exc}") from exc

    # v2 は C++ 側が 44100Hz / 5ms frame の絶対タイムラインを使う。
    # 数サンプル程度の丸め誤差は許容するが、大きな差は書き出し破綻とする。
    duration_tolerance = max(0.015, 3.0 / max(float(info.samplerate), 1.0))
    if expected_duration_sec > 0.0 and abs(actual_duration - expected_duration_sec) > duration_tolerance:
        raise RuntimeError(
            "レンダリング結果のWAV長がタイムラインと一致しません。"
            f" expected={expected_duration_sec:.3f}s actual={actual_duration:.3f}s"
        )

    # 少なくとも1ノートの音源WAVが解決済みなら、全体が完全無音になるのは
    # 正常な成功結果ではない。チャンク読み込みで長時間曲のメモリ使用量を抑える。
    peak = 0.0
    try:
        with sf.SoundFile(file_path, "r") as audio:
            while True:
                chunk = audio.read(262144, dtype="float32", always_2d=False)
                if chunk is None or len(chunk) == 0:
                    break
                chunk_peak = float(np.max(np.abs(chunk)))
                peak = max(peak, chunk_peak)
    except Exception as exc:
        raise RuntimeError(f"生成されたWAVの音量を検証できませんでした: {exc}") from exc

    if peak <= 1.0e-7:
        raise RuntimeError(
            "レンダリング結果のWAVが完全な無音です。"
            " ネイティブ合成・音源WAV・WORLD解析のいずれかで失敗しています。"
        )


def _read_native_render_error(native_lib: Any) -> str:
    """Read a native failure without allowing exceptions to escape the C ABI."""
    getter = getattr(native_lib, "get_last_render_error", None)
    if not callable(getter):
        return ""
    try:
        try:
            getter.restype = ctypes.c_char_p
        except (AttributeError, TypeError):
            # Python fakes used by unit tests may expose a normal bound method.
            pass
        raw = getter()
    except Exception as exc:
        logger.debug("ネイティブレンダーのエラー取得に失敗: %s", exc)
        return ""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace").strip()
    if isinstance(raw, str):
        return raw.strip()
    try:
        return ctypes.string_at(raw).decode("utf-8", errors="replace").strip()
    except (TypeError, ValueError, OSError):
        return ""


def _log_native_render_worker_count(native_lib: Any) -> None:
    """Log the bounded native worker count when the loaded core exposes it."""
    getter = getattr(native_lib, "get_last_render_worker_count", None)
    if not callable(getter):
        return
    try:
        try:
            getter.restype = ctypes.c_int
        except (AttributeError, TypeError):
            pass
        worker_count = int(getter())
    except Exception as exc:
        logger.debug("ネイティブレンダラーのワーカー数取得に失敗: %s", exc)
        return
    if worker_count > 0:
        logger.info("ネイティブWORLD合成ワーカー数: %d", worker_count)


def _render_to_wav_v2(
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
    last_progress = -1

    def report_progress(percent: int) -> None:
        nonlocal last_progress
        if not callable(progress_callback):
            return
        pct = max(0, min(100, int(percent)))
        if pct < last_progress:
            return
        last_progress = pct
        progress_callback(pct)

    report_progress(2)

    tempo_bpm = kwargs.pop("tempo_bpm", None)
    if tempo_bpm is None:
        tempo_bpm = getattr(self, "_tempo", 120.0)
    try:
        tempo_bpm = float(tempo_bpm)
    except (TypeError, ValueError):
        tempo_bpm = 120.0
    if tempo_bpm <= 0.0:
        tempo_bpm = 120.0

    if not self.lib:
        raise RuntimeError("Engine Core library missing!")

    # 既存ファイルを先に削除する。ネイティブ側でノート合成に失敗した場合に
    # 古いWAVを「今回の書き出し成功結果」と誤認しないため。
    output_path_abs = os.path.abspath(file_path)
    try:
        if os.path.isfile(output_path_abs):
            os.remove(output_path_abs)
    except OSError as exc:
        raise RuntimeError(f"既存のWAVを置き換えられません: {exc}") from exc

    oto_parser = getattr(self, "oto_parser", None)
    notes, timeline = self.text_analyzer.align_vocal_timing(
        notes,
        oto_parser,
        tempo_bpm=tempo_bpm,
    )
    report_progress(5)

    cvvc_resolver = getattr(self, "cvvc_resolver", None)
    if cvvc_resolver is not None and cvvc_resolver.classify_voicebank() == "cvvc":
        notes = cvvc_resolver.expand_notes_for_render(notes, tempo_bpm=tempo_bpm)

    if timeline and hasattr(self, "pipeline_bridge") and self.pipeline_bridge:
        self.pipeline_bridge.send_timeline_to_core(timeline)
    report_progress(8)

    note_count = len(notes)
    from modules.audio.vo_se_engine import CNoteEvent
    resolved_voice_count = 0
    unresolved_note_diagnostics: List[str] = []
    c_notes_array = (CNoteEvent * note_count)()
    self._temp_refs = []

    for i, note in enumerate(notes):
        if callable(cancel_check) and cancel_check():
            raise RuntimeError("レンダリングがキャンセルされました")
        render_alias = str(getattr(note, "_cvvc_render_alias", "") or "")
        vcv_resolver = getattr(self, "vcv_resolver", None)
        wav_path = ""
        resolved_oto_entry = None
        note_num = 60

        prev_note = notes[i - 1] if i > 0 else None
        prev_lyric = getattr(prev_note, "lyric", None) if prev_note is not None else None
        gap_sec = float("inf")
        if prev_note is not None:
            try:
                prev_end = float(prev_note.start_time) + max(0.0, float(prev_note.duration))
                gap_sec = max(0.0, float(note.start_time) - prev_end)
            except (TypeError, ValueError, AttributeError):
                gap_sec = float("inf")
        note_tempo_raw = getattr(note, "_ust_tempo", None)
        try:
            note_tempo = float(note_tempo_raw) if note_tempo_raw is not None else tempo_bpm
        except (TypeError, ValueError):
            note_tempo = tempo_bpm
        if note_tempo <= 0.0:
            note_tempo = 120.0
        is_continuous = prev_note is not None and gap_sec <= 30.0 / note_tempo
        try:
            note_num = int(
                getattr(note, "note_number", getattr(note, "note_num", 60))
            )
        except (TypeError, ValueError):
            note_num = 60

        if render_alias:
            cvvc_entry = getattr(oto_parser, "_db", {}).get(render_alias) if oto_parser is not None else None
            if cvvc_entry is not None:
                wav_path = cvvc_entry.wav_path
                resolved_oto_entry = cvvc_entry
        elif vcv_resolver is not None:
            _alias, oto_entry = vcv_resolver.resolve_note(
                getattr(note, "lyric", ""),
                prev_lyric,
                is_continuous=is_continuous,
                note_num=note_num,
            )
            if oto_entry is not None:
                wav_path = oto_entry.wav_path
                resolved_oto_entry = oto_entry

        if not wav_path or not os.path.isfile(wav_path):
            # Retry the exact lyric against OTO before any phoneme-derived alias.
            direct_entry = None
            if oto_parser is not None:
                try:
                    direct_entry = oto_parser.resolve_alias(
                        str(getattr(note, "lyric", "") or ""),
                        None,
                        note_num=note_num,
                    )
                except (AttributeError, TypeError, ValueError):
                    direct_entry = None
            if direct_entry is None and oto_parser is not None:
                try:
                    direct_entry = getattr(oto_parser, "_db", {}).get(
                        str(getattr(note, "lyric", "") or "")
                    )
                except AttributeError:
                    direct_entry = None
            if direct_entry is not None:
                candidate_path = str(getattr(direct_entry, "wav_path", "") or "")
                if candidate_path and os.path.isfile(candidate_path):
                    wav_path = candidate_path
                    resolved_oto_entry = direct_entry

        if not wav_path or not os.path.isfile(wav_path):
            # A basename matching the lyric must win over a guessed CV phone alias.
            wav_path = _lookup_oto_map_fallback(
                getattr(self, "oto_map", {}),
                getattr(note, "lyric", None),
                None,
            )

        if (
            not render_alias
            and vcv_resolver is not None
            and (not wav_path or not os.path.isfile(wav_path))
        ):
            # Only retry one unambiguous consonant+vowel pair. The OTO resolver
            # still applies VCV context and prefix.map pitch-layer selection.
            for phone_alias in _phoneme_sequence_aliases(
                getattr(note, "phonemes", None)
            ):
                _alias, phone_entry = vcv_resolver.resolve_note(
                    phone_alias,
                    prev_lyric,
                    is_continuous=is_continuous,
                    note_num=note_num,
                )
                if phone_entry is None:
                    continue
                candidate_path = str(getattr(phone_entry, "wav_path", "") or "")
                if candidate_path and os.path.isfile(candidate_path):
                    wav_path = candidate_path
                    resolved_oto_entry = phone_entry
                    break

        if not wav_path or not os.path.isfile(wav_path):
            # Legacy map fallback also accepts a safe two-phone alias.
            wav_path = _lookup_oto_map_fallback(
                getattr(self, "oto_map", {}),
                None,
                getattr(note, "phonemes", None),
            )

        if wav_path:
            wav_path = os.path.abspath(os.path.normpath(str(wav_path)))

        if not wav_path or not os.path.isfile(wav_path):
            phoneme_preview = getattr(note, "phonemes", None)
            if isinstance(phoneme_preview, (list, tuple)):
                phoneme_preview = list(phoneme_preview[:8])
            note_number = getattr(note, "note_number", getattr(note, "note_num", None))
            unresolved_note_diagnostics.append(
                f"#{i + 1} lyric={getattr(note, 'lyric', None)!r} "
                f"note_number={note_number!r} phonemes={phoneme_preview!r}"
            )
            wav_path = ""

        # 未解決時にライブラリ先頭の別音素を使うと、誤発音になるため、
        # 解決不能なノートは空のままにする。ただし後段で「全ノート無音」を
        # 正常終了扱いにしない。

        # C++ 側の pitch_length は「固定128点の表示解像度」ではなく、
        # 実際のノート長を表す 5ms フレーム数。
        # 以前は常に128を渡していたため、短いノートも長いノートも
        # すべて約635msとしてレンダリングされ、曲全体の時間軸が崩れていた。
        res = _note_duration_frames(note)
        p_curve = self._get_sampled_curve(parameters["Pitch"], note, res, is_pitch=True).astype(np.float64)
        g_curve = self._get_sampled_curve(parameters["Gender"], note, res).astype(np.float64)
        t_curve = self._get_sampled_curve(parameters["Tension"], note, res).astype(np.float64)
        b_curve = self._get_sampled_curve(parameters["Breath"], note, res).astype(np.float64)
        flag_gender, flag_tension, flag_breath, flag_pitch_cents = parse_ust_flag_overrides(
            str(getattr(note, "_ust_flags", "") or "")
        )
        if flag_gender is not None:
            g_curve.fill(flag_gender)
        if flag_tension is not None:
            t_curve.fill(flag_tension)
        if flag_breath is not None:
            b_curve.fill(flag_breath)
        if flag_pitch_cents:
            p_curve *= np.power(2.0, flag_pitch_cents / 1200.0)

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

        # The v2 UST/VCV path has already resolved PreUtterance/VoiceOverlap
        # in align_vocal_timing(). Extract timing metadata BEFORE building
        # portamento_offsets so PreUtterance lead_frames can pad negative time.
        try:
            start_time_sec = float(getattr(note, "start_time", 0.0))
        except (TypeError, ValueError):
            start_time_sec = 0.0

        try:
            preutterance_ms = float(getattr(note, "pre_utterance", 0.0))
        except (TypeError, ValueError):
            preutterance_ms = 0.0
        try:
            overlap_ms = float(getattr(note, "overlap", 0.0))
        except (TypeError, ValueError):
            overlap_ms = 0.0

        c_pre_ms = max(0.0, preutterance_ms) if np.isfinite(preutterance_ms) else -1.0
        resolved_oto_pre = (
            max(0.0, float(getattr(resolved_oto_entry, "preutterance", 0.0) or 0.0))
            if resolved_oto_entry is not None else 0.0
        )
        effective_pre_ms = c_pre_ms if c_pre_ms >= 0.0 else resolved_oto_pre

        render_kind = str(getattr(note, "_cvvc_render_kind", "") or "")
        next_note = None
        if render_kind != "vc":
            try:
                cur_end_sec = start_time_sec + max(0.0, float(getattr(note, "duration", 0.0) or 0.0))
            except (TypeError, ValueError):
                cur_end_sec = -1.0
            for j in range(i + 1, note_count):
                cand = notes[j]
                if str(getattr(cand, "_cvvc_render_kind", "") or "") == "vc":
                    continue
                try:
                    cand_start_sec = float(getattr(cand, "start_time", 0.0))
                except (TypeError, ValueError):
                    cand_start_sec = float("inf")
                if cur_end_sec >= 0.0 and abs(cand_start_sec - cur_end_sec) <= 0.015:
                    next_note = cand
                break
        time_offset_ms = (
            -float(getattr(note, "duration", 0.0) or 0.0) * 1000.0
            if render_kind == "vc" else 0.0
        )

        portamento_curve = build_portamento_curve(
            note,
            resolution=res,
            next_note=next_note,
            preutterance_ms=effective_pre_ms,
            time_offset_ms=time_offset_ms,
        )
        if portamento_curve is not None:
            portamento_arr = portamento_curve.astype(np.float64)
            portamento_len = int(len(portamento_arr))
        else:
            portamento_arr = None
            portamento_len = 0

        self._temp_refs.extend([p_curve, g_curve, t_curve, b_curve, vib_depth, vib_rate])
        if portamento_arr is not None:
            self._temp_refs.append(portamento_arr)

        # ctypes.c_char_p の None は C++ 側の nullptr になる。
        # b"" は「NUL終端の空文字列へのポインタ」であり nullptr ではない。
        c_notes_array[i].wav_path = wav_path.encode("utf-8") if wav_path else None
        if wav_path:
            resolved_voice_count += 1
        c_notes_array[i].pitch_curve = p_curve.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].gender_curve = g_curve.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].tension_curve = t_curve.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].breath_curve = b_curve.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].vibrato_depth_curve = vib_depth.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].vibrato_rate_curve = vib_rate.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
        c_notes_array[i].pitch_length = res
        c_notes_array[i].vibrato_curve_length = res
        c_notes_array[i].intensity = float(np.clip(getattr(note, "_ust_intensity", 100.0), 0.0, 200.0))
        c_notes_array[i].modulation = float(np.clip(getattr(note, "_ust_modulation", 0.0), 0.0, 100.0))

        c_notes_array[i].start_time_ms = (
            start_time_sec * 1000.0
            if np.isfinite(start_time_sec) and start_time_sec >= 0.0
            else -1.0
        )

        c_notes_array[i].preutterance_ms = c_pre_ms
        c_notes_array[i].overlap_ms = (
            max(0.0, overlap_ms)
            if np.isfinite(overlap_ms)
            else -1.0
        )

        # NoteEvent準備は全体の8→20%に割り当て、ネイティブ側の
        # 2→100%を20→100%へ連続的に接続する。これでDesktop UIの
        # 進捗が90%→2%のように逆戻りしない。
        note_progress = 8 + int(((i + 1) / max(note_count, 1)) * 12.0)
        report_progress(note_progress)

        if portamento_arr is not None:
            c_notes_array[i].portamento_offsets = portamento_arr.ctypes.data_as(ctypes.POINTER(ctypes.c_double))
            c_notes_array[i].portamento_length = portamento_len
        else:
            c_notes_array[i].portamento_offsets = None
            c_notes_array[i].portamento_length = 0

    if callable(cancel_check) and cancel_check():
        raise RuntimeError("レンダリングがキャンセルされました")

    # ノートが存在するのに音源が1件も解決できない場合、C++ は仕様上
    # 全ノートを無音として正常終了できてしまう。これは「書き出し成功」
    # ではなく音源解決失敗なので、ここで明示的に止める。
    if unresolved_note_diagnostics:
        logger.warning(
            "音源WAVを解決できないノートが %d 件あります: %s",
            len(unresolved_note_diagnostics),
            "; ".join(unresolved_note_diagnostics[:5]),
        )

    if note_count > 0 and resolved_voice_count == 0:
        examples = "; ".join(unresolved_note_diagnostics[:5])
        detail = f" 未解決ノート: {examples}" if examples else ""
        voice_root = str(getattr(self, "voice_lib_path", "") or "(未設定)")
        oto_db = getattr(oto_parser, "_db", {}) if oto_parser is not None else {}
        oto_alias_count = len(oto_db) if isinstance(oto_db, dict) else 0
        prefix_map_count = len(getattr(oto_parser, "_prefix_map", []) or [])
        oto_ini_count = len(getattr(self, "_loaded_oto_ini_paths", []) or [])
        diagnostic = (
            f" 音源パス={voice_root!r} / oto.ini={oto_ini_count}件"
            f" / OTO alias={oto_alias_count}件 / prefix.map={prefix_map_count}行。"
        )
        raise RuntimeError(
            "レンダリング対象の音源WAVを1件も解決できませんでした。"
            " 音源フォルダ・oto.ini・prefix.mapを確認してください。"
            + diagnostic
            + detail
        )

    native_output = os.path.abspath(file_path).encode("utf-8")
    execute_cancelable = getattr(self.lib, "execute_render_cancelable", None)

    if callable(execute_cancelable):
        ProgressCallback = ctypes.CFUNCTYPE(None, ctypes.c_int)
        CancelCheckCallback = ctypes.CFUNCTYPE(ctypes.c_int)

        # ctypes callbackはネイティブ呼び出しが終わるまで参照を保持する。
        progress_cb_ref = ProgressCallback(
            lambda native_pct: report_progress(20 + int(
                max(0, min(100, int(native_pct))) * 0.8
            ))
        )
        cancel_cb_ref = CancelCheckCallback(
            lambda: 1 if callable(cancel_check) and cancel_check() else 0
        )

        try:
            execute_cancelable(
                c_notes_array,
                note_count,
                native_output,
                mode_flag,
                progress_cb_ref,
                cancel_cb_ref,
            )
        finally:
            self._temp_refs = []
    else:
        # 古いDLLとの互換性。cancelable APIが無い場合でもレンダー自体は
        # 継続できるが、ネイティブ内部の段階進捗は受け取れない。
        report_progress(20)
        try:
            self.lib.execute_render(
                c_notes_array,
                note_count,
                native_output,
                mode_flag,
            )
        finally:
            self._temp_refs = []

    _log_native_render_worker_count(self.lib)
    native_error = _read_native_render_error(self.lib)
    if native_error:
        raise RuntimeError(f"ネイティブ合成中にエラーが発生しました: {native_error}")

    if callable(cancel_check) and cancel_check():
        raise RuntimeError("レンダリングがキャンセルされました")

    if not os.path.exists(output_path_abs):
        raise RuntimeError("レンダリング結果の WAV が生成されませんでした。")

    expected_duration_sec = _expected_render_duration_sec(notes)
    _validate_rendered_wav(output_path_abs, expected_duration_sec)

    report_progress(100)
    return output_path_abs



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
    """Write a validated render to a sibling temp file before replacing output."""
    import threading
    from modules.audio.atomic_export import render_wav_atomically

    # Native rendering owns mutable FFI buffers, OTO state and timeline data.
    # Serialize every v2 render transaction even when a caller bypasses the
    # desktop playback preparation lock.
    lock = getattr(self, "_render_lock", None)
    if lock is None:
        lock = self.__dict__.setdefault("_render_lock", threading.RLock())

    with _NATIVE_ENGINE_LOCK, lock:
        return render_wav_atomically(
            file_path,
            lambda temporary_path: _render_to_wav_v2(
                self,
                notes,
                parameters,
                temporary_path,
                mode_flag=mode_flag,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                **kwargs,
            ),
        )


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
            # Keep Python's active voice-root fields and the process-wide OTO
            # replacement in the same critical section as native rendering.
            with _NATIVE_ENGINE_LOCK:
                self.voice_lib_path = voice_dir
                self._ust_voice_dir = voice_dir
                _refresh_voice_library_v2_unlocked(self)
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


def ensure_desktop_export_ready(engine: Any) -> None:
    """Prepare the VO-SE desktop renderer regardless of the GUI entry point.

    Some legacy launchers construct MainWindow before loading VO_SE_Engine and
    do not run patch_main_window after injection. Keep that from silently
    falling back to the older, incompatible export path.
    """
    from modules.audio.vo_se_engine import VO_SE_Engine

    if not isinstance(engine, VO_SE_Engine):
        raise TypeError(
            "デスクトップWAV書き出しには VO_SE_Engine が必要です。"
            f" 現在のエンジン: {type(engine).__name__}"
        )

    if bool(getattr(engine, "_vose_desktop_export_ready", False)):
        if not callable(getattr(engine, "export_to_wav_v2", None)):
            raise RuntimeError("WAV書き出しAPIの初期化状態が不正です。")
        return

    apply_patch(type(engine))

    refresh = getattr(engine, "refresh_voice_library_v2", None)
    exporter = getattr(engine, "export_to_wav_v2", None)
    if not callable(refresh) or not callable(exporter):
        raise RuntimeError("VO-SEデスクトップWAV書き出しAPIを初期化できません。")

    # Resolve oto.ini / prefix.map only after the compatibility methods have
    # been installed on the actual engine instance.
    refresh()
    setattr(engine, "_vose_desktop_export_ready", True)
