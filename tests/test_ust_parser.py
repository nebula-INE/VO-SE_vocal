import unittest
import tempfile
import os
from modules.data.ust_parser import UstParser, UstConverter
import pytest
from dataclasses import replace

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

    def test_modulation_defaults_to_zero(self):
        """UST/OpenUtau の Modulation 省略時は 0（完全にフラット）を使う。"""
        ust = (
            "[#SETTING]\\nTempo=120\\n"
            "[#0000]\\nLength=480\\nLyric=か\\nNoteNum=60\\nIntensity=100\\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            ust_file = os.path.join(tmp_dir, "mod_default.ust")
            with open(ust_file, "w", encoding="cp932") as f:
                f.write(ust)
            project = UstParser().load(ust_file)
            self.assertEqual(project.notes[0].modulation, 0.0)
            self.assertEqual(UstConverter.to_note_dicts(project)[0]["_ust_modulation"], 0.0)

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

    def test_portamento_uses_ust_10cent_units(self):
        """PBS/PBY は 10cent 単位で、内部の semitone に正規化される。"""
        from modules.data.ust_parser import UstNote

        note = UstNote(
            index=0,
            length=480,
            lyric="か",
            note_num=60,
            tempo=60.0,
            pbs="0;5",
            pbw="100",
            pby="10",
        )
        curve = UstConverter.extract_portamento_curve(note, resolution=11)

        # 60 BPM / 480 ticks = 1000ms。resolution=11 なので100ms刻み。
        # PBS=5 => +0.5 semitone、PBY=10 => +1.0 semitone。
        self.assertAlmostEqual(curve[0], 0.5, places=6)
        self.assertAlmostEqual(curve[1], 1.0, places=6)

    def test_portamento_pbm_shapes(self):
        """PBM の S/linear/R/J が区間補間へ反映されること。"""
        from modules.data.ust_parser import UstNote

        from typing import Any

        base: dict[str, Any] = {
            "index": 0,
            "length": 480,
            "lyric": "か",
            "note_num": 60,
            "tempo": 60.0,
            "pbs": "0;0",
            "pbw": "500",
            "pby": "10",
        }

        linear = UstConverter.extract_portamento_curve(
            UstNote(**base, pbm="s"), resolution=5
        )
        smooth = UstConverter.extract_portamento_curve(
            UstNote(**base, pbm=""), resolution=5
        )
        r_shape = UstConverter.extract_portamento_curve(
            UstNote(**base, pbm="r"), resolution=5
        )
        j_shape = UstConverter.extract_portamento_curve(
            UstNote(**base, pbm="j"), resolution=5
        )

        self.assertAlmostEqual(linear[2], 1.0, places=6)
        self.assertAlmostEqual(smooth[2], 1.0, places=6)
        self.assertGreater(r_shape[1], linear[1])
        self.assertLess(j_shape[1], linear[1])
        # PBWの最終点以降はノート終端へ0 semitoneに戻る。
        self.assertGreater(linear[3], linear[4])
        self.assertAlmostEqual(r_shape[0], 0.0, places=6)
        self.assertAlmostEqual(j_shape[0], 0.0, places=6)
        # PBWの終端後は次の制御点(0 semitone)へ戻る。
        self.assertAlmostEqual(r_shape[-1], 0.0, places=6)
        self.assertAlmostEqual(j_shape[-1], 0.0, places=6)

    def test_project_flags_are_inherited_by_notes(self):
        """[#SETTING] Flags はノート側にFlagsが無い場合だけ継承される。"""
        ust = (
            "[#SETTING]\n"
            "Tempo=120.00\n"
            "Flags=g-5B50\n"
            "\n"
            "[#0000]\n"
            "Length=480\n"
            "Lyric=か\n"
            "NoteNum=60\n"
            "\n"
            "[#0001]\n"
            "Length=480\n"
            "Lyric=き\n"
            "NoteNum=62\n"
            "Flags=g10\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            ust_file = os.path.join(tmp_dir, "flags.ust")
            with open(ust_file, "w", encoding="cp932") as f:
                f.write(ust)
            dicts = UstConverter.to_note_dicts(UstParser().load(ust_file))
            self.assertEqual(dicts[0]["_ust_flags"], "g-5B50")
            self.assertEqual(dicts[1]["_ust_flags"], "g10")

    def test_explicit_empty_note_flags_override_project_flags(self):
        """ノート側の明示的なFlags=は[#SETTING] Flagsを継承しない。"""
        ust = (
            "[#SETTING]\nTempo=120\nFlags=g-5B50\n"
            "[#0000]\nLength=480\nLyric=か\nNoteNum=60\nFlags=\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            ust_file = os.path.join(tmp_dir, "flags_empty.ust")
            with open(ust_file, "w", encoding="cp932") as f:
                f.write(ust)
            dicts = UstConverter.to_note_dicts(UstParser().load(ust_file))
            self.assertEqual(dicts[0]["_ust_flags"], "")

    def test_voice_dir_resolves_relative_and_percent_voice(self):
        """UST VoiceDir の相対パスと %VOICE% を音源ライブラリへ正しく解決する。"""
        from modules.audio.vo_se_engine_patch import _load_ust_project

        with tempfile.TemporaryDirectory() as tmp_dir:
            voice_dir = os.path.join(tmp_dir, "voice")
            os.makedirs(voice_dir)
            with open(os.path.join(voice_dir, "oto.ini"), "w", encoding="cp932") as f:
                f.write("a.wav=あ,0,0,0,0,0\\n")

            ust_path = os.path.join(tmp_dir, "song.ust")
            ust = (
                "[#SETTING]\\nTempo=120\\nVoiceDir=%VOICE%\\n"
                "[#0000]\\nLength=480\\nLyric=あ\\nNoteNum=60\\n"
            )
            with open(ust_path, "w", encoding="cp932") as f:
                f.write(ust)

            class DummyEngine:
                def __init__(self):
                    self.voice_lib_path = voice_dir
                    self.oto_parser = None
                    self.oto_map = {}
                    self.vcv_resolver = None

            engine = DummyEngine()
            notes = _load_ust_project(engine, ust_path)
            self.assertEqual(engine.voice_lib_path, os.path.abspath(voice_dir))
            oto_parser = engine.oto_parser
            self.assertIsNotNone(oto_parser)
            assert oto_parser is not None
            self.assertTrue(oto_parser.get("あ"))
            self.assertEqual(len(notes), 1)

    def test_ust_flags_map_g_b_and_t(self):
        """g/B はパラメータ、t は10cent単位のピッチシフトとして扱う。"""
        from modules.audio.vo_se_engine_patch import parse_ust_flag_overrides

        gender, tension, breath, pitch_cents = parse_ust_flag_overrides("g-5B50t20")
        self.assertIsNotNone(gender)
        self.assertAlmostEqual(float(gender), 0.475)
        self.assertIsNone(tension)
        self.assertIsNotNone(breath)
        self.assertAlmostEqual(float(breath), 0.5)
        self.assertAlmostEqual(float(pitch_cents), 200.0)

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

    def test_ust_tempo_is_exported_in_note_dict(self):
        """[Step 2-A] 各ノートの tempo が _ust_tempo として辞書に含まれること"""
        ust = (
            "[#SETTING]\n"
            "Tempo=120.00\n"
            "ProjectName=Test\n"
            "\n"
            "[#0000]\n"
            "Length=480\n"
            "Lyric=か\n"
            "NoteNum=60\n"
            "Tempo=120.00\n"
            "\n"
            "[#0001]\n"
            "Length=480\n"
            "Lyric=き\n"
            "NoteNum=62\n"
            "Tempo=170.00\n"
            "\n"
            "[#0002]\n"
            "Length=480\n"
            "Lyric=く\n"
            "NoteNum=64\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            ust_file = os.path.join(tmp_dir, "test.ust")
            with open(ust_file, "w", encoding="cp932") as f:
                f.write(ust)

            project = UstParser().load(ust_file)
            dicts = UstConverter.to_note_dicts(project)

            self.assertEqual(len(dicts), 3)
            # ノート1: 明示的に Tempo=120.00
            self.assertEqual(dicts[0]["_ust_tempo"], 120.0)
            # ノート2: 明示的に Tempo=170.00
            self.assertEqual(dicts[1]["_ust_tempo"], 170.0)
            # ノート3: Tempo省略 → 直前の170を継承
            self.assertEqual(dicts[2]["_ust_tempo"], 170.0)

    def test_ust_tempo_affects_duration_per_note(self):
        """テンポ変化を含むUSTで、各ノートの duration が個別テンポで計算されること"""
        ust = (
            "[#SETTING]\n"
            "Tempo=120.00\n"
            "\n"
            "[#0000]\n"
            "Length=480\n"
            "Lyric=か\n"
            "NoteNum=60\n"
            "Tempo=120.00\n"
            "\n"
            "[#0001]\n"
            "Length=480\n"
            "Lyric=き\n"
            "NoteNum=62\n"
            "Tempo=170.00\n"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            ust_file = os.path.join(tmp_dir, "test.ust")
            with open(ust_file, "w", encoding="cp932") as f:
                f.write(ust)

            project = UstParser().load(ust_file)
            dicts = UstConverter.to_note_dicts(project)

            # 480 ticks / 480 ticks_per_beat = 1拍
            # 120 BPM: 1拍 = 0.5秒
            self.assertAlmostEqual(dicts[0]["duration"], 0.5, places=4)
            # 170 BPM: 1拍 = 60/170 ≒ 0.3529秒
            self.assertAlmostEqual(dicts[1]["duration"], 60.0 / 170.0, places=4)

            # start_time も正しく累積されること
            self.assertAlmostEqual(dicts[0]["start_time"], 0.0, places=4)
            self.assertAlmostEqual(dicts[1]["start_time"], 0.5, places=4)

    def test_note_event_preserves_ust_tempo(self):
        """[Step 2-B] NoteEvent.from_dict() / to_dict() が _ust_tempo を保持すること"""
        from modules.data.data_models import NoteEvent

        # 典型的な UST 辞書
        note_dict = {
            "note_number": 60,
            "start_time": 0.0,
            "duration": 0.5,
            "lyric": "か",
            "velocity": 100,
            "vibrato_depth": 0.0,
            "vibrato_rate": 5.5,
            "pre_utterance": None,
            "overlap": None,
            "_ust_flags": "g-5B50",
            "_ust_tempo": 170.0,
            "_ust_modulation": 0.0,
            "_ust_pbs": "",
            "_ust_pbw": "",
            "_ust_pby": "",
            "_ust_pbm": "",
            "_ust_intensity": 120.0,
        }

        note = NoteEvent.from_dict(note_dict)

        # _ust_* が動的属性として復元されていること
        self.assertEqual(getattr(note, "_ust_tempo", None), 170.0)
        self.assertEqual(getattr(note, "_ust_flags", None), "g-5B50")
        self.assertEqual(getattr(note, "_ust_intensity", None), 120.0)

        # to_dict() で _ust_* が再出力されること（往復）
        roundtrip = note.to_dict()
        self.assertEqual(roundtrip.get("_ust_tempo"), 170.0)
        self.assertEqual(roundtrip.get("_ust_flags"), "g-5B50")
        self.assertEqual(roundtrip.get("_ust_intensity"), 120.0)

        # 再構築しても同じ値が復元されること
        note2 = NoteEvent.from_dict(roundtrip)
        self.assertEqual(getattr(note2, "_ust_tempo", None), 170.0)

if __name__ == "__main__":
    unittest.main()
