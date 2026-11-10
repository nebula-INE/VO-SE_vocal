import pytest

mido = pytest.importorskip("mido")

from modules.data.midi_manager import load_midi_file, save_midi_file


def test_midi_import_applies_global_tempo_changes_and_keeps_overlaps(tmp_path):
    midi_path = tmp_path / "tempo-overlap.mid"
    tempo_track = mido.MidiTrack()
    tempo_track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    tempo_track.append(mido.MetaMessage("set_tempo", tempo=1_000_000, time=480))
    tempo_track.append(mido.MetaMessage("end_of_track", time=0))

    note_track = mido.MidiTrack()
    note_track.append(mido.Message("note_on", note=60, velocity=80, time=0))
    note_track.append(mido.Message("note_on", note=60, velocity=100, time=240))
    note_track.append(mido.Message("note_off", note=60, velocity=0, time=240))
    note_track.append(mido.Message("note_off", note=60, velocity=0, time=240))
    note_track.append(mido.MetaMessage("end_of_track", time=0))

    midi_file = mido.MidiFile(type=1, ticks_per_beat=480)
    midi_file.tracks.extend([tempo_track, note_track])
    midi_file.save(midi_path)

    notes = load_midi_file(str(midi_path))

    assert notes is not None
    assert len(notes) == 2
    assert [note["start_time"] for note in notes] == pytest.approx([0.0, 0.25])
    assert [note["duration"] for note in notes] == pytest.approx([0.5, 0.75])
    assert [note["velocity"] for note in notes] == [80, 100]


def test_midi_export_writes_track_names_tempo_and_notes(tmp_path):
    midi_path = tmp_path / "roundtrip.mid"
    result = save_midi_file(
        str(midi_path),
        [
            {
                "name": "Lead",
                "notes": [
                    {
                        "start_time": 0.0,
                        "duration": 0.5,
                        "note_number": 60,
                        "velocity": 96,
                        "lyrics": "あ",
                    },
                    {
                        "start_time": 0.5,
                        "duration": 0.25,
                        "note_number": 64,
                        "velocity": 88,
                        "lyrics": "い",
                    },
                ],
            },
            {"name": "Harmony", "notes": []},
        ],
        tempo=120.0,
    )

    assert result == str(midi_path)
    loaded = mido.MidiFile(midi_path)
    assert len(loaded.tracks) == 2
    assert any(msg.type == "track_name" and msg.name == "Lead" for msg in loaded.tracks[0])
    assert any(msg.type == "set_tempo" and msg.tempo == 500_000 for msg in loaded.tracks[0])
    assert len([msg for msg in loaded.tracks[0] if msg.type == "note_on"]) == 2


def test_midi_export_rejects_invalid_tempo(tmp_path):
    with pytest.raises(ValueError, match="テンポ"):
        save_midi_file(
            str(tmp_path / "invalid.mid"),
            [{"name": "Lead", "notes": []}],
            tempo=0.0,
        )
