import json
import math
from pathlib import Path

import pytest

from modules.backend.audio_mixer import AudioMixer


FIXTURE = json.loads(
    (Path(__file__).resolve().parents[1] / "specs" / "track_mixer_cases.json").read_text(
        encoding="utf-8"
    )
)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda case: case["id"])
def test_desktop_track_pan_matches_shared_web_fixture(case):
    left, right = AudioMixer._pan_gains(case["pan"])
    assert math.isclose(left * case["volume"], case["left_gain"], abs_tol=1e-6)
    assert math.isclose(right * case["volume"], case["right_gain"], abs_tol=1e-6)


def test_shared_track_mixer_fixture_documents_same_gain_and_pan_ranges():
    assert all(0.0 <= case["volume"] <= 1.0 for case in FIXTURE["cases"])
    assert all(-1.0 <= case["pan"] <= 1.0 for case in FIXTURE["cases"])
    assert FIXTURE["effect_chain_schema"]["track_property"] == "effects"


def test_desktop_track_effect_presets_match_shared_fixture(qtbot):
    from modules.gui.effects_panel import EffectsPanel

    panel = EffectsPanel()
    qtbot.addWidget(panel)
    for preset_name, expected_chain in FIXTURE["presets"].items():
        preset_index = panel.preset_selector.findData(preset_name)
        assert preset_index >= 0
        panel.preset_selector.setCurrentIndex(preset_index)
        panel.on_apply_preset()
        assert panel.get_effect_chain() == expected_chain



def test_effect_schema_defaults_and_ranges_match_shared_fixture():
    from modules.audio.master_effects import EFFECT_DEFAULTS, EFFECT_RANGES

    for effect_type, defaults in FIXTURE["effect_defaults"].items():
        assert EFFECT_DEFAULTS[effect_type] == defaults
        for parameter, bounds in FIXTURE["effect_parameter_ranges"][effect_type].items():
            assert list(EFFECT_RANGES[effect_type][parameter]) == bounds
