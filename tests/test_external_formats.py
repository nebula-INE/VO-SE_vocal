import json
import xml.etree.ElementTree as ET

from modules.data.external_formats import (
    SVP_TICK_SCALE,
    export_svp_json,
    export_vsqx_xml,
    parse_svp_json,
)
from modules.data.data_models import NoteEvent


def test_svp_parser_converts_tick_units_and_tempo():
    project = {
        "time": {"tempo": [{"position": 0, "bpm": 132.5}]},
        "tracks": [{
            "name": "Lead",
            "mainGroup": {"notes": [
                {"onset": 480 * SVP_TICK_SCALE, "duration": 240 * SVP_TICK_SCALE,
                 "lyrics": "あ", "pitch": 64},
            ]},
        }],
    }
    parsed = parse_svp_json(json.dumps(project, ensure_ascii=False))
    note = parsed["notes"][0]
    assert parsed["tempo"] == 132.5
    assert parsed["project_name"] == "Lead"
    assert getattr(note, "_svp_tick") == 480
    assert round(note.start_time, 4) == round(480 * 60 / (132.5 * 480), 4)
    assert note.note_number == 64


def test_svp_parser_accepts_nested_group_tracks_and_rejects_invalid_root():
    project = {"tracks": [{"name": "Group", "groups": [{"notes": [
        {"onset": 480, "duration": 120, "lyrics": "い", "pitch": 60}
    ]}]}]}
    assert len(parse_svp_json(json.dumps(project))["notes"]) == 1
    try:
        parse_svp_json("[]")
    except ValueError as exc:
        assert "root" in str(exc)
    else:
        raise AssertionError("invalid root should be rejected")


def test_svp_export_preserves_tempo_pitch_and_lyric():
    notes = [NoteEvent(note_number=67, start_time=0.5, duration=0.25, lyric="ね")]
    payload = json.loads(export_svp_json("Song", 120, notes))
    exported = payload["tracks"][0]["mainGroup"]["notes"][0]
    assert exported["onset"] == 480 * SVP_TICK_SCALE
    assert exported["duration"] == 240 * SVP_TICK_SCALE
    assert exported["pitch"] == 67
    assert exported["lyrics"] == "ね"
    assert payload["time"]["tempo"][0]["bpm"] == 120


def test_vsqx_export_is_well_formed_and_contains_timing_and_pitch():
    notes = [NoteEvent(note_number=72, start_time=0.0, duration=0.5, lyric="あ")]
    xml = export_vsqx_xml("Song & Test", 120, notes)
    root = ET.fromstring(xml)
    ns = {"v": "http://www.yamaha.co.jp/vocaloid/schema/vsq4/"}
    note = root.find(".//v:note", ns)
    assert note is not None
    assert note.findtext("v:noteNum", namespaces=ns) == "72"
    assert note.findtext("v:lyric", namespaces=ns) == "あ"
    assert root.findtext(".//v:masterTrack/v:tempo/v:bpm", namespaces=ns) == "12000"

def test_svp_file_uses_async_project_loader_contract(tmp_path):
    from modules.gui.mixins.project_io_mixin import _parse_external_project_file

    path = tmp_path / "desktop-parity.svp"
    path.write_text(json.dumps({
        "time": {"tempo": [{"position": 0, "bpm": 120}]},
        "tracks": [{"mainGroup": {"notes": [
            {"onset": 0, "duration": 480, "pitch": 60, "lyrics": "あ"}
        ]}}],
    }, ensure_ascii=False), encoding="utf-8")
    payload = _parse_external_project_file(str(path))
    assert payload["format"] == "svp"
    assert payload["project"]["notes"][0].lyric == "あ"


def test_exported_vsqx_can_be_read_by_desktop_parser(tmp_path):
    from modules.data.vsqx_parser import parse_vsqx

    notes = [NoteEvent(note_number=69, start_time=0.0, duration=0.5, lyric="あ")]
    path = tmp_path / "roundtrip.vsqx"
    path.write_text(export_vsqx_xml("Roundtrip", 120, notes), encoding="utf-8")
    project = parse_vsqx(str(path))
    assert project.tempo == 120
    assert len(project.notes) == 1
    assert project.notes[0].note_number == 69
    assert project.notes[0].lyric == "あ"
