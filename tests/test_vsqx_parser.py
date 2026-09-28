import pytest
from modules.data.vsqx_parser import parse_vsqx

@pytest.mark.smoke
def test_parse_vsqx_with_namespace_part_offset_and_tempo_change(tmp_path):
    path = tmp_path / "song.vsqx"
    path.write_text("""<?xml version="1.0"?>
<vsq3 xmlns="http://www.yamaha.co.jp/vocaloid/schema/vsq3/">
  <masterTrack>
    <resolution>480</resolution>
    <tempo><posTick>0</posTick><bpm>12000</bpm></tempo>
    <tempo><posTick>480</posTick><bpm>6000</bpm></tempo>
  </masterTrack>
  <vsTrack><name>Test Song</name></vsTrack>
  <vsPart><musicalPart><posTick>240</posTick>
    <note><posTick>0</posTick><durTick>480</durTick><noteNum>60</noteNum><lyric>あ</lyric></note>
    <note><posTick>480</posTick><durTick>480</durTick><noteNum>62</noteNum><lyric>い</lyric></note>
  </musicalPart></vsPart>
</vsq3>""", encoding="utf-8")
    project = parse_vsqx(str(path))
    assert project.project_name == "Test Song"
    assert project.tempo == pytest.approx(120.0)
    assert len(project.notes) == 2
    assert project.notes[0].start_time == pytest.approx(0.25)
    assert project.notes[0].duration == pytest.approx(0.5)
    assert project.notes[1].start_time == pytest.approx(1.25)
    assert project.notes[1].duration == pytest.approx(1.0)
