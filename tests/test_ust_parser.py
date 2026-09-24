import unittest
import tempfile
import os
from modules.data.ust_parser import UstParser, UstConverter
import pytest

SAMPLE_UST = """
[#VERSION]
UST Version 1.2
[#SETTING]
Tempo=150.000
ProjectName=Test
[#0000]
Length=480
Lyric=か
NoteNum=60
Intensity=120
Flags=g-5B50
VBR=50,180,35,20,20,0,0
PBS=0;0
PBW=50,100
PBY=0,5
"""
@pytest.mark.smoke
class TestUstParser(unittest.TestCase):
    def test_parse_ust_basic(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ust_file = os.path.join(tmp_dir, "test.ust")
            with open(ust_file, "w", encoding="cp932") as f:
                f.write(SAMPLE_UST)

            parser = UstParser()
            project = parser.load(ust_file)

            self.assertEqual(project.tempo, 150.0)
            self.assertEqual(len(project.notes), 1)
            note = project.notes[0]
            self.assertEqual(note.lyric, "か")
            self.assertEqual(note.note_num, 60)
            self.assertEqual(note.intensity, 120.0)
            self.assertEqual(note.flags, "g-5B50")

    def test_parse_vibrato(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ust_file = os.path.join(tmp_dir, "test.ust")
            with open(ust_file, "w", encoding="cp932") as f:
                f.write(SAMPLE_UST)

            parser = UstParser()
            project = parser.load(ust_file)
            note = project.notes[0]

            self.assertIsNotNone(note.vibrato)
            # Pyright に Optional の可能性を無視させるため # type: ignore を付与
            self.assertEqual(note.vibrato.length, 50.0)   # type: ignore
            self.assertEqual(note.vibrato.cycle, 180.0)   # type: ignore
            self.assertEqual(note.vibrato.depth, 35.0)    # type: ignore

    def test_tempo_prefers_first_note_over_setting(self):
        """[#SETTING]=120 かつ [#0000] Tempo=170 のとき project.tempo は 170 になる"""
        ust = (
            "[#SETTING]\n"
            "Tempo=120.00\n"
            "ProjectName=Test\n"
            "\n"
            "[#0000]\n"
            "Length=480\n"
            "Lyric=か\n"
            "NoteNum=60\n"
            "Tempo=170.00\n"
            "\n"
            "[#0001]\n"
            "Length=480\n"
            "Lyric=か\n"
            "NoteNum=60\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            ust_file = os.path.join(tmp_dir, "test.ust")
            with open(ust_file, "w", encoding="cp932") as f:
                f.write(ust)

            parser = UstParser()
            project = parser.load(ust_file)

            self.assertEqual(project.tempo, 170.0)
            # 各ノートの長さも170BPM換算になっているはず
            note_dicts = UstConverter.to_note_dicts(project)
            self.assertAlmostEqual(note_dicts[0]["duration"], 60.0 / 170.0, places=4)

    def test_convert_to_note_events(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            ust_file = os.path.join(tmp_dir, "test.ust")
            with open(ust_file, "w", encoding="cp932") as f:
                f.write(SAMPLE_UST)

            parser = UstParser()
            project = parser.load(ust_file)
            dicts = UstConverter.to_note_dicts(project)

            self.assertEqual(len(dicts), 1)
            self.assertAlmostEqual(dicts[0]["duration"], 0.4, places=2)
            self.assertEqual(dicts[0]["_ust_flags"], "g-5B50")
            self.assertIn("_ust_vibrato", dicts[0])

if __name__ == "__main__":
    unittest.main()
