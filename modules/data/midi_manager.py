# pyright: reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
# midi_manager.py


import mido
import mido.backends.rtmidi
import time
from typing import List, Optional, Dict, Any, cast
try:
    from PySide6.QtCore import Signal, QObject
except ImportError:
    from modules.utils.pyside_stub import Signal, QObject

class _FallbackNoteEvent:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def to_dict(self):
        return self.__dict__

try:
    import modules.data.data_models as _data_models
    NoteEventClass: Any = getattr(_data_models, "NoteEvent", _FallbackNoteEvent)
except Exception:
    NoteEventClass = _FallbackNoteEvent

class MidiSignals(QObject):
    """MIDIイベントをGUIやエンジンに橋渡しするシグナル"""
    # note, velocity, status ('on'/'off')
    midi_event_signal = Signal(int, int, str)
    # note, velocity, status, timestamp
    midi_event_record_signal = Signal(int, int, str, float)

# シングルトンとしてエクスポート
midi_signals = MidiSignals()

def _extract_lyric(message: Any) -> str:
    """MIDIメッセージから歌詞候補を抽出。見つからない場合は母音デフォルト。"""
    for attr in ("text", "lyric", "lyrics"):
        value = getattr(message, attr, None)
        if isinstance(value, str):
            candidate = value.strip()
            if candidate:
                return candidate
    return "a"

def load_midi_file(filepath: str) -> Optional[List[Dict[str, Any]]]:
    """Load MIDI notes using the file-wide tempo map and preserve overlapping same-pitch notes."""
    try:
        try:
            mid = mido.MidiFile(filepath, charset="utf-8")
        except UnicodeDecodeError:
            try:
                mid = mido.MidiFile(filepath, charset="cp932")
            except UnicodeDecodeError:
                mid = mido.MidiFile(filepath, charset="latin1")
        ticks_per_beat = int(mid.ticks_per_beat)
        if ticks_per_beat <= 0:
            raise ValueError("MIDI ticks_per_beat must be positive")

        # Merge absolute-tick events from every track before converting time.
        # Tempo changes are global in MIDI files and must affect every note track.
        events = []
        track_names: Dict[int, str] = {}
        for track_index, track in enumerate(mid.tracks):
            absolute_tick = 0
            for event_index, message in enumerate(track):
                absolute_tick += int(getattr(message, "time", 0))
                events.append((absolute_tick, track_index, event_index, message))
        events.sort(key=lambda item: (item[0], item[1], item[2]))

        notes: List[Dict[str, Any]] = []
        active_notes: Dict[tuple[int, int], List[tuple[float, int, int, str]]] = {}
        pending_lyrics: Dict[tuple[int, int], List[str]] = {}
        previous_tick = 0
        current_seconds = 0.0
        current_tempo = 500000

        for absolute_tick, event_track_index, _event_index, message in events:
            delta_ticks = absolute_tick - previous_tick
            if delta_ticks < 0:
                raise ValueError("MIDI events are not in chronological order")
            current_seconds += (
                delta_ticks * current_tempo / (1_000_000.0 * ticks_per_beat)
            )
            previous_tick = absolute_tick

            if message.is_meta:
                if message.type == "set_tempo":
                    current_tempo = int(getattr(message, "tempo", 500000))
                    if current_tempo <= 0:
                        raise ValueError("MIDI contains an invalid tempo")
                elif message.type == "track_name":
                    track_names[event_track_index] = str(getattr(message, "name", ""))
                elif message.type == "lyrics":
                    lyric_key = (event_track_index, absolute_tick)
                    pending_lyrics.setdefault(lyric_key, []).append(
                        str(getattr(message, "text", "") or "")
                    )
                continue

            message_type = str(message.type)
            note_number = int(getattr(message, "note", 0))
            velocity = int(getattr(message, "velocity", 0))
            channel = int(getattr(message, "channel", 0))
            key = (channel, note_number)

            if message_type == "note_on" and velocity > 0:
                lyric_key = (event_track_index, absolute_tick)
                lyric_queue = pending_lyrics.get(lyric_key, [])
                lyric = lyric_queue.pop(0) if lyric_queue else "a"
                if not lyric_queue:
                    pending_lyrics.pop(lyric_key, None)
                active_notes.setdefault(key, []).append(
                    (current_seconds, velocity, event_track_index, lyric)
                )
            elif message_type == "note_off" or (
                message_type == "note_on" and velocity == 0
            ):
                pending = active_notes.get(key)
                if not pending:
                    continue

                # FIFO pairing preserves both notes when the same pitch overlaps.
                start_seconds, start_velocity, start_track_index, start_lyric = pending.pop(0)
                if not pending:
                    active_notes.pop(key, None)
                duration = current_seconds - start_seconds
                if duration <= 0.0:
                    continue

                note = NoteEventClass(
                    note_number=note_number,
                    start_time=start_seconds,
                    duration=duration,
                    lyric=start_lyric,
                )
                note.velocity = start_velocity
                note_data = note.to_dict()
                note_data["velocity"] = start_velocity
                note_data["_midi_track_index"] = start_track_index
                note_data["_midi_track_name"] = track_names.get(
                    start_track_index, f"MIDI Track {start_track_index + 1}"
                )
                notes.append(note_data)

        notes.sort(key=lambda item: (
            int(item.get("_midi_track_index", 0)),
            float(item.get("start_time", 0.0)),
            int(item.get("note_number", 60)),
        ))
        return notes
    except Exception as e:
        print(f"MIDIファイルの読み込みに失敗しました: {e}")
        return None


def save_midi_file(
    filepath: str,
    tracks: List[Dict[str, Any]],
    tempo: float = 120.0,
    ticks_per_beat: int = 480,
) -> str:
    """Write vocal tracks to a standard MIDI file without changing audio rendering."""
    import math

    tempo_bpm = float(tempo)
    if not math.isfinite(tempo_bpm) or tempo_bpm <= 0.0:
        raise ValueError("テンポが不正なためMIDIを書き出せません。")
    if ticks_per_beat <= 0:
        raise ValueError("ticks_per_beat must be positive")

    midi_file = mido.MidiFile(type=1, ticks_per_beat=int(ticks_per_beat), charset="utf-8")
    microseconds_per_beat = int(round(60_000_000.0 / tempo_bpm))

    for track_index, track_data in enumerate(tracks):
        name = str(track_data.get("name", f"Vocal {track_index + 1}"))
        notes = track_data.get("notes", [])
        if not isinstance(notes, list):
            raise ValueError(f"{name}: notes must be a list")

        midi_track = mido.MidiTrack()
        midi_file.tracks.append(midi_track)
        events = []
        if track_index == 0:
            events.append((0, -2, 0, mido.MetaMessage(
                "set_tempo", tempo=microseconds_per_beat, time=0
            )))
        events.append((0, -1, 0, mido.MetaMessage("track_name", name=name, time=0)))

        serial = 1
        for note_index, note in enumerate(notes):
            def value(key: str, default: Any) -> Any:
                if isinstance(note, dict):
                    return note.get(key, default)
                return getattr(note, key, default)

            start_seconds = float(value("start_time", 0.0))
            duration_seconds = float(value("duration", 0.5))
            note_number = int(value("note_number", 60))
            velocity = int(value("velocity", 100))
            lyric = str(value("lyrics", value("lyric", "")) or "")

            if (
                not math.isfinite(start_seconds)
                or not math.isfinite(duration_seconds)
                or start_seconds < 0.0
                or duration_seconds <= 0.0
                or not 0 <= note_number <= 127
            ):
                raise ValueError(
                    f"{name}: ノート {note_index + 1} の位置・長さ・音程が不正です。"
                )

            velocity = min(127, max(1, velocity))
            start_tick = int(round(start_seconds * tempo_bpm / 60.0 * ticks_per_beat))
            end_tick = max(
                start_tick + 1,
                int(round((start_seconds + duration_seconds) * tempo_bpm / 60.0 * ticks_per_beat)),
            )

            if lyric:
                events.append((
                    start_tick, 0, serial,
                    mido.MetaMessage("lyrics", text=lyric, time=0),
                ))
                serial += 1
            events.append((
                start_tick, 1, serial,
                mido.Message("note_on", channel=0, note=note_number, velocity=velocity, time=0),
            ))
            serial += 1
            events.append((
                end_tick, 0, serial,
                mido.Message("note_off", channel=0, note=note_number, velocity=0, time=0),
            ))
            serial += 1

        events.sort(key=lambda item: (item[0], item[1], item[2]))
        previous_tick = 0
        for absolute_tick, _priority, _serial, message in events:
            message.time = absolute_tick - previous_tick
            midi_track.append(message)
            previous_tick = absolute_tick
        midi_track.append(mido.MetaMessage("end_of_track", time=0))

    if not midi_file.tracks:
        raise ValueError("MIDIに書き出すボーカルトラックがありません。")
    midi_file.save(filepath)
    return filepath

class MidiInputManager:
    """MIDIキーボードなどの外部機器入力を管理（1行も省略なし）"""
    def __init__(self, port_name: Optional[str] = None):
        self.port_name = port_name
        self.port: Any = None

    @staticmethod
    def get_available_ports() -> List[str]:
        """利用可能なポートを取得。getattrで動的にアクセス。"""
        get_names = getattr(mido, 'get_input_names', None)
        if get_names and callable(get_names):
            # 戻り値を文字列のリストとして明示
            return list(cast(List[str], get_names()))
        return []

    def start(self) -> None:
        if not self.port_name:
            ports = self.get_available_ports()
            if not ports:
                print("MIDIデバイスが見つかりません。")
                return
            self.port_name = ports[0]

        try:
            # ポートオープン（Pyrightエラー回避のため動的呼び出し）
            open_input = getattr(mido, 'open_input', None)
            if open_input and callable(open_input):
                self.port = open_input(self.port_name, callback=self.midi_callback)
                print(f"MIDI入力開始: {self.port_name}")
            else:
                print("mido.open_input が利用できません。")
        except Exception as e:
            print(f"MIDIポートのオープンに失敗: {e}")

    def stop(self) -> None:
        """Close the active MIDI port and release its reference only on success."""
        port = self.port
        if port is None:
            return

        close_func = getattr(port, "close", None)
        if not callable(close_func):
            raise RuntimeError("MIDI input port does not expose a close method")

        try:
            close_func()
        except Exception as exc:
            # Keep the reference so shutdown or a later user action can retry.
            print(f"MIDIポートの切断エラー: {exc}")
            raise RuntimeError(f"MIDI input port could not be closed: {exc}") from exc

        self.port = None
        print("MIDIポートを閉じました。")

    def midi_callback(self, message: Any) -> None:
        """外部MIDIメッセージ受信時の処理（型安全版・省略なし）"""
        timestamp = time.time()
        
        # messageオブジェクトから属性を安全に取得し、型を確定させる
        m_type = str(getattr(message, 'type', 'unknown'))
        m_note = cast(int, getattr(message, 'note', 0))
        m_velocity = cast(int, getattr(message, 'velocity', 0))
        
        if m_type == 'note_on' and m_velocity > 0:
            midi_signals.midi_event_signal.emit(m_note, m_velocity, 'on')
            midi_signals.midi_event_record_signal.emit(m_note, m_velocity, 'on', timestamp)
        elif m_type == 'note_off' or (m_type == 'note_on' and m_velocity == 0):
            midi_signals.midi_event_signal.emit(m_note, m_velocity, 'off')
            midi_signals.midi_event_record_signal.emit(m_note, m_velocity, 'off', timestamp)
