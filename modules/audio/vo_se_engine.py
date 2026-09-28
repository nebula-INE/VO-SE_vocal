import ctypes
from typing import Optional 
import os
import platform
import numpy as np
import tempfile
import shutil
from typing import List, Dict, Any, Callable, cast
try:
    import sounddevice as sd
except Exception:
    sd = None
try:
    import soundfile as sf
except Exception:
    sf = None
try:
    import chardet
except Exception:
    chardet = None


# ==========================================================================
# 1. C言語互換構造体（パラメーターを1つも漏らさずC++へ）
# ==========================================================================
from modules.ffi.vose_types import CNoteEvent, COtoEntry
# 🚀 【新規追加】C++側の 8バイトアライメント（24バイト固定長）に完全準拠した構造体定義
class CVoseFrame(ctypes.Structure):
    _pack_ = 8
    _fields_ = [
        ("time", ctypes.c_double),
        ("phoneme", ctypes.c_char * 8),
        ("weight", ctypes.c_double),
    ]


# ==========================================================================
# 2. メインエンジンクラス（削りなし・全機能統合版）
# ==========================================================================
class VO_SE_Engine:
    def __init__(self, voice_lib_dir="voices"):
        self.sample_rate = 44100
        self.lib = self._load_core_library()
        self._temp_refs = []  # C++実行中のメモリ保護用
        self.is_playing = False
        self.stream = None
        self.current_out_data = None  # 現在再生中の全波形データ
        
        # パス解決（開発環境とビルド後の両方に対応）
        base_dir = os.path.dirname(os.path.abspath(__file__))
        self.voice_lib_path = os.path.abspath(os.path.join(base_dir, "..", voice_lib_dir))
        
        # 🚀 【フェーズ3：音素解析・原音設定・C++転送ブリッジの完全統合】
        # refresh_voice_library() が走る前に、受け皿となるパーサー類を確実に実体化
        from modules.data.text_analyzer import TextAnalyzer
        from modules.data.oto_parser import OtoParser
        from modules.bridge.pipeline_bridge import PipelineBridge

        self.text_analyzer = TextAnalyzer()
        self.oto_parser = OtoParser()
        self.pipeline_bridge = PipelineBridge(self.lib)
        
        self.oto_map = {}
        self.refresh_voice_library()

        try:
            from modules.gui.aural_engine import AuralAIEngine
            self.aural_ai = AuralAIEngine()
        except Exception:
            self.aural_ai = None

    def get_audio_devices(self):
        """接続されているオーディオ入出力デバイスのリストを返す"""
        if sd is None:
            return []
        devices = sd.query_devices()
        output_devices = [d['name'] for d in devices if d['max_output_channels'] > 0]
        return output_devices

    def set_output_device(self, device_name):
        """指定されたデバイスを出力先に設定する"""
        if sd is None:
            raise RuntimeError("sounddevice is not available")
        sd.default.device = [None, device_name]  # [入力, 出力]
        print(f"🔈 Output set to: {device_name}")

    def setup_audio_output(self, device_name=None):
        """
        オーディオデバイスを設定する。
        """
        try:
            if sd is None:
                print("Audio backend is unavailable: sounddevice not installed.")
                return
            if device_name:
                sd.default.device[1] = device_name  # 出力デバイスを指定
            device_info = sd.query_devices(sd.default.device[1])
            print(f"✔︎ Audio device set: {device_info['name']}")
        except Exception as e:
            print(f"Device error: {e}")

    def set_voice_library(self, path: str) -> None:
        """音源フォルダを動的に切り替え、oto.ini/VCV 解決状態を再構築する。"""
        self.voice_lib_path = os.path.abspath(path)

        # MainWindow 起動時に vo_se_engine_patch が適用済みなら、
        # 通常スキャンではなく VCV 対応版を必ず使う。
        # これを base refresh_voice_library() のままにすると、音源切替後も
        # 古い OtoParser/VcvResolver を参照し続ける。
        refresh_v2 = getattr(self, "refresh_voice_library_v2", None)
        if callable(refresh_v2):
            # refresh_voice_library_v2() が OTO をネイティブへ同期する。
            # ここで再送すると音源切替時に同じDBを二重登録することになる。
            refresh_v2()
        else:
            self.refresh_voice_library()
            self.set_oto_data(getattr(self.oto_parser, "_db", {}))

    def set_oto_data(self, oto_data) -> None:
        """oto.ini のメタデータをPython/ネイティブ両方へ同期する。"""
        self.oto_data = oto_data

        set_oto = getattr(self.lib, "set_oto_data", None)
        if not callable(set_oto):
            return

        if isinstance(oto_data, dict):
            entries = list(oto_data.values())
        elif isinstance(oto_data, (list, tuple)):
            entries = list(oto_data)
        else:
            entries = []

        c_entries = (COtoEntry * len(entries))()
        for i, entry in enumerate(entries):
            alias = str(getattr(entry, "alias", "") or "")
            wav_path = str(getattr(entry, "wav_path", "") or "")
            if not alias or not wav_path:
                continue

            def fixed_utf8(value: str, limit: int) -> bytes:
                raw = value.encode("utf-8")
                if len(raw) < limit:
                    return raw
                raw = raw[: limit - 1]
                while raw:
                    try:
                        raw.decode("utf-8")
                        return raw
                    except UnicodeDecodeError:
                        raw = raw[:-1]
                return b""

            c_entries[i].filename = None
            c_entries[i].cutoff = float(getattr(entry, "right_blank", 0.0))
            c_entries[i].alias = fixed_utf8(alias, 64)
            c_entries[i].wav_path = fixed_utf8(os.path.abspath(wav_path), 512)
            c_entries[i].offset = float(getattr(entry, "left_blank", 0.0))
            c_entries[i].consonant = float(getattr(entry, "fixed_range", 0.0))
            c_entries[i].blank = float(getattr(entry, "right_blank", 0.0))
            c_entries[i].preutterance = float(getattr(entry, "preutterance", 0.0))
            c_entries[i].overlap = float(getattr(entry, "overlap", 0.0))

        if entries:
            set_oto(c_entries, len(entries))
        else:
            set_oto(None, 0)

    def prepare_cache(self, notes: list) -> None:
        """再生前に波形の先行キャッシュ（現状はスルーでOK。将来的に最適化）"""
        # スタブ：重い処理を事前に走らせたい場合はここに実装
        pass

    def export_to_wav_v2(
        self,
        notes,
        params,
        file_path,
        mode_flag: int = 0,
        progress_callback: Optional[Callable[[int], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
        **kwargs,
    ) -> Optional[str]:
        """旧 export_to_wav を v2 互換シグネチャで呼び出すラッパー。"""
        result = self.export_to_wav(
            notes,
            params,
            file_path,
            mode_flag=mode_flag,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        if result is None:
            return None
        return os.path.abspath(result)
        
    def set_tempo(self, tempo: float) -> None:
        """テンポをエンジン内部に保持（現状は何もしないが、将来のDSP用）"""
        self._tempo = float(tempo)

    def _load_core_library(self):
        """VO-SE Coreを共通FFIローダー経由で読み込む。"""
        from modules.ffi.vose_api import load_engine

        base_dir = os.path.dirname(__file__)
        search_dirs = [
            base_dir,
            os.path.join(base_dir, "bin"),
            os.path.join(os.getcwd(), "bin"),
            os.getcwd(),
        ]
        lib = load_engine(search_dirs=search_dirs)
        if lib is not None:
            print("○ Engine Core Connected via unified FFI loader")
        return lib

    @staticmethod
    def _duration_sec_to_frames(duration_sec: float) -> int:
        """秒 → C++ 側の pitch_length (5ms フレーム数) に変換する。

        C++ 側の note_samples_safe(p) は以下の式で出力サンプル数を決める:
            note_samples = (p - 1) * kFramePeriod / 1000.0 * kFs + 1
        ここで kFramePeriod = 5.0 [ms], kFs = 44100 [Hz] なので
            note_samples = (p - 1) * 220.5 + 1
        よって duration_sec を再現するには:
            p = round(duration_sec * 44100 / 220.5) + 1
              = round(duration_sec * 200.0) + 1
        """
        frames = int(round(duration_sec * 200.0)) + 1
        return max(2, frames)

    # --- 高度な音源スキャン ---
    def refresh_voice_library(self):
        """voicesフォルダを再帰的にスキャン。UTAU音源の階層構造に対応"""
        if not os.path.exists(self.voice_lib_path):
            os.makedirs(self.voice_lib_path, exist_ok=True)
            return

        print(f"[VO_SE_Engine] refresh_voice_library: scanning {self.voice_lib_path}")

        self.oto_map = {}
        collision_count = 0

        for root, _, files in os.walk(self.voice_lib_path):
            files_lower = [f.lower() for f in files]

            # UTAUの複数音域（マルチピッチ）音源では Lyric= 側が
            # "サブフォルダ名\エイリアス"（例: "do-dai\あー_D4"）という形式で
            # 音域フォルダを指定してくる。ルート直下ではない場合はこのプレフィックスも
            # 登録しないと絶対に一致しない（→無音スキップ／代打雑音の原因だった）。
            rel_dir = os.path.relpath(root, self.voice_lib_path)
            subdir_prefix = "" if rel_dir in (".", "") else rel_dir.replace(os.sep, "\\") + "\\"

            oto_aliases: Dict[str, str] = {}  # alias -> filename（このフォルダのoto.iniがあれば埋まる）
            if "oto.ini" in files_lower:
                target_ini = files[files_lower.index("oto.ini")]
                ini_path = os.path.join(root, target_ini)
                self.oto_parser.load_oto_file(ini_path)
                # oto_parser がエイリアス一覧を引ける場合はそれを使う
                get_aliases = getattr(self.oto_parser, "get_aliases_for_dir", None)
                if callable(get_aliases):
                    oto_aliases = cast(
                        Dict[str, str],
                        get_aliases(ini_path) or {},
                    )

            for file in files:
                if not file.lower().endswith(".wav"):
                    continue
                full_path = os.path.abspath(os.path.join(root, file))
                filename_key = os.path.splitext(file)[0]

                # oto.ini にエイリアス定義があればそちらを優先キーにする
                keys_to_register = set()
                for alias, wav_file in oto_aliases.items():
                    if os.path.splitext(wav_file)[0] == filename_key or wav_file == file:
                        keys_to_register.add(alias)
                if not keys_to_register:
                    keys_to_register.add(filename_key)

                # サブフォルダ内のファイルは "サブフォルダ\エイリアス" 形式でも登録する
                if subdir_prefix:
                    for key in list(keys_to_register):
                        keys_to_register.add(subdir_prefix + key)

                for key in keys_to_register:
                    existing = self.oto_map.get(key)
                    if existing and existing != full_path:
                        collision_count += 1
                        print(f"[VO_SE_Engine][WARN] oto_map衝突: '{key}' "
                              f"{os.path.relpath(existing, self.voice_lib_path)} -> "
                              f"{os.path.relpath(full_path, self.voice_lib_path)} で上書き")
                    self.oto_map[key] = full_path

        if collision_count:
            print(f"[VO_SE_Engine][WARN] refresh_voice_library: {collision_count}件のキー衝突を検出。"
                  f" voice_lib_path='{self.voice_lib_path}' に複数音源が混在している可能性があります。")
        
    # --- エンコーディング自動判別 ---
    def read_text_safely(self, file_path):
        """USTやoto.iniの文字化けを防ぐ"""
        try:
            with open(file_path, 'rb') as f:
                raw = f.read()
                if chardet is None:
                    return raw.decode("cp932", errors='ignore')
                det = chardet.detect(raw)
                enc = det['encoding'] if det['confidence'] > 0.7 else 'cp932'
                safe_enc = enc if isinstance(enc, str) else "cp932"
                return raw.decode(safe_enc, errors='ignore')
        except Exception:
            return ""

    # --- 核心機能：多重パラメーター・レンダリング ---
    def export_to_wav(
        self,
        notes: List[Any],
        parameters: Dict[str, Any],
        file_path: str,
        mode_flag: int = 0,
        progress_callback: Optional[Callable[[int], None]] = None,
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> Optional[str]:
        """Render the complete song in one C++ call using absolute note timing.

        The old implementation rendered independent chunks and concatenated their
        WAVs. That made start_time/preutterance/overlap impossible to preserve at
        chunk boundaries. The C++ renderer now owns the complete timeline, so the
        Python bridge only serializes NoteEvent data and delegates the final WAV
        write to the core.
        """
        if not self.lib:
            raise RuntimeError("Engine Core library missing!")

        if sf is None:
            print("[VO_SE_Engine] soundfile not available, cannot write WAV.")
            return None

        total = len(notes)
        if total == 0:
            return None

        if cancel_check and cancel_check():
            print("[VO_SE_Engine] Render cancelled by user.")
            return None

        c_notes_array = (CNoteEvent * total)()
        keep_alive: List[Any] = []
        wav_refs: List[bytes] = []
        self._temp_refs = keep_alive

        try:
            for i, note in enumerate(notes):
                res = self._duration_sec_to_frames(
                    float(getattr(note, "duration", 0.5))
                )

                p_curve = self._get_sampled_curve(
                    parameters.get("Pitch", []), note, res, is_pitch=True
                ).astype(np.float64)
                g_curve = self._get_sampled_curve(
                    parameters.get("Gender", []), note, res
                ).astype(np.float64)
                t_curve = self._get_sampled_curve(
                    parameters.get("Tension", []), note, res
                ).astype(np.float64)
                b_curve = self._get_sampled_curve(
                    parameters.get("Breath", []), note, res
                ).astype(np.float64)
                vibrato_depth_curve = np.zeros(res, dtype=np.float64)
                vibrato_rate_curve = np.zeros(res, dtype=np.float64)

                keep_alive.extend([
                    p_curve, g_curve, t_curve, b_curve,
                    vibrato_depth_curve, vibrato_rate_curve,
                ])

                lyric = getattr(note, "lyrics", None) or getattr(note, "lyric", "")
                phonemes = getattr(note, "phonemes", None)
                wav_path = self.oto_map.get(lyric) or (
                    self.oto_map.get(phonemes) if phonemes else None
                )

                if wav_path:
                    wav_ref = str(wav_path).encode("utf-8")
                    wav_refs.append(wav_ref)
                    c_notes_array[i].wav_path = wav_ref
                else:
                    print(
                        f"[VO_SE_Engine][INFO] 未解決の歌詞を無音として保持: "
                        f"lyric='{lyric}' phonemes='{phonemes}' "
                        f"duration={float(getattr(note, 'duration', 0.0)):.3f}s "
                        f"frames={res}"
                    )

                c_notes_array[i].pitch_curve = p_curve.ctypes.data_as(
                    ctypes.POINTER(ctypes.c_double)
                )
                c_notes_array[i].gender_curve = g_curve.ctypes.data_as(
                    ctypes.POINTER(ctypes.c_double)
                )
                c_notes_array[i].tension_curve = t_curve.ctypes.data_as(
                    ctypes.POINTER(ctypes.c_double)
                )
                c_notes_array[i].breath_curve = b_curve.ctypes.data_as(
                    ctypes.POINTER(ctypes.c_double)
                )
                c_notes_array[i].vibrato_depth_curve = vibrato_depth_curve.ctypes.data_as(
                    ctypes.POINTER(ctypes.c_double)
                )
                c_notes_array[i].vibrato_rate_curve = vibrato_rate_curve.ctypes.data_as(
                    ctypes.POINTER(ctypes.c_double)
                )
                c_notes_array[i].pitch_length = res
                c_notes_array[i].vibrato_curve_length = res
                c_notes_array[i].intensity = float(
                    np.clip(getattr(note, "_ust_intensity", 100.0), 0.0, 200.0)
                )
                c_notes_array[i].modulation = float(
                    np.clip(getattr(note, "_ust_modulation", 0.0), 0.0, 100.0)
                )

                # Absolute timeline. A missing start_time keeps legacy behavior.
                raw_start = getattr(note, "start_time", None)
                try:
                    start_ms = float(raw_start) * 1000.0 if raw_start is not None else -1.0
                except (TypeError, ValueError):
                    start_ms = -1.0
                c_notes_array[i].start_time_ms = start_ms if np.isfinite(start_ms) and start_ms >= 0.0 else -1.0

                # Only an explicit UST override is serialized here. Otherwise C++
                # resolves the value from oto.ini, preserving the explicit-0 rule.
                pre_explicit = bool(getattr(note, "_ust_preutterance_explicit", False))
                ov_explicit = bool(getattr(note, "_ust_overlap_explicit", False))
                c_notes_array[i].preutterance_ms = (
                    float(getattr(note, "pre_utterance", 0.0))
                    if pre_explicit and getattr(note, "pre_utterance", None) is not None
                    else -1.0
                )
                c_notes_array[i].overlap_ms = (
                    float(getattr(note, "overlap", 0.0))
                    if ov_explicit and getattr(note, "overlap", None) is not None
                    else -1.0
                )

            if progress_callback:
                progress_callback(5)

            if cancel_check and cancel_check():
                print("[VO_SE_Engine] Render cancelled by user.")
                return None

            self.lib.execute_render(
                c_notes_array,
                total,
                os.path.abspath(file_path).encode("utf-8"),
                mode_flag,
            )

            if not os.path.exists(file_path):
                print("[VO_SE_Engine] Render core did not create the output WAV.")
                return None

            if progress_callback:
                progress_callback(100)

            print(f"[VO_SE_Engine] Render complete: {file_path}")
            return file_path

        except Exception as e:
            print(f"[VO_SE_Engine] Render error: {e}")
            return None
        finally:
            self._temp_refs = []
    def _get_sampled_curve(self, events, note, res, is_pitch=False):
        """ノート区間におけるパラメータカーブを res 点でサンプリングする。

        events が空のときは「無変更」を意味する中立値を返す:
          - is_pitch=True : 0.0 (半音偏差。ノート基準ピッチそのものは
                            呼び出し側で note.note_number から復元する)
          - is_pitch=False: 0.5 (Gender/Tension の中立値。Breath は別途 0.0 を明示)
        """
        curve = np.zeros(res, dtype=np.float32)
        default_val = 0.0 if is_pitch else 0.5
        if not events:
            return curve + default_val

        start_time = float(getattr(note, "start_time", 0.0))
        duration = float(getattr(note, "duration", 0.0))
        times = np.linspace(start_time, start_time + duration, res)

        event_times = [float(p.time) for p in events]
        event_values = [float(p.value) for p in events]

        curve = np.interp(times, event_times, event_values).astype(np.float32)

        if is_pitch:
            # curve は「ノート基準ピッチからの半音偏差」として扱う。
            # ノート基準ピッチ (note_number) を加算してから Hz に変換する。
            curve = curve + float(getattr(note, "note_number", 60))
            curve = 440.0 * (2.0 ** ((curve - 69.0) / 12.0))
            if self.aural_ai is not None:
                note_id = id(note)
                curve = self.aural_ai.get_baked_pitch(note_id, curve)

        return curve
            


    def get_current_rms(self):
        """再生中の『本物の波形』から現在の音量を計算して返す"""
        if not self.is_playing or self.current_out_data is None:
            return 0.0

        try:
            get_playback_time = getattr(self, "get_playback_time", None)
            raw_playback = get_playback_time() if callable(get_playback_time) else 0.0
            playback_time = float(raw_playback) if isinstance(raw_playback, (int, float)) else 0.0
            curr_sample = int(playback_time * 44100)
            chunk = self.current_out_data[curr_sample : curr_sample + 256]
            if len(chunk) == 0:
                return 0.0
            
            rms = np.sqrt(np.mean(chunk**2))
            return min(rms * 5.0, 1.0)
        except Exception:
            return 0.0
    
    # --- 再生制御 ---
    def play(self, filepath):
        if sd is None or sf is None:
            print("Audio playback is unavailable: sounddevice/soundfile not installed.")
            return
        if filepath and os.path.exists(filepath):
            data, fs = sf.read(filepath)
            sd.play(data, fs)

    def stop(self):
        if sd is None:
            return
        sd.stop()
