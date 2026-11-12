import numpy as np
import pytest

from modules.audio.master_effects import (
    MasterEffectsProcessor,
    normalize_effect_chain,
)


def _effect(effect_type, params=None, *, enabled=True, effect_id="fx"):
    return {
        "id": effect_id,
        "type": effect_type,
        "enabled": enabled,
        "params": params or {},
    }


def test_empty_or_disabled_chain_is_an_exact_noop():
    audio = np.linspace(-0.8, 0.8, 256, dtype=np.float32).reshape(128, 2)
    empty = MasterEffectsProcessor(44100)
    disabled = MasterEffectsProcessor(
        44100,
        [_effect("compressor", {"threshold_db": -12.0}, enabled=False)],
    )

    assert empty.process(audio) is audio
    assert disabled.process(audio) is audio
    assert np.array_equal(audio, np.linspace(-0.8, 0.8, 256, dtype=np.float32).reshape(128, 2))


def test_effect_chain_validation_rejects_non_finite_and_out_of_range_values():
    with pytest.raises(ValueError, match="範囲"):
        normalize_effect_chain([
            _effect("compressor", {"ratio": 25.0}),
        ])
    with pytest.raises(ValueError, match="無効な数値"):
        normalize_effect_chain([
            _effect("eq", {"gain_db": float("nan")}),
        ])
    with pytest.raises(ValueError, match="未対応"):
        normalize_effect_chain([_effect("limiter")])
    with pytest.raises(ValueError, match="重複"):
        normalize_effect_chain([
            _effect("eq", effect_id="duplicate"),
            _effect("reverb", effect_id="duplicate"),
        ])


def test_compressor_reduces_levels_above_the_threshold():
    sample_rate = 1000
    audio = np.full((2000, 2), 0.8, dtype=np.float32)
    processor = MasterEffectsProcessor(
        sample_rate,
        [_effect("compressor", {
            "threshold_db": -12.0,
            "ratio": 6.0,
            "attack_ms": 2.0,
            "release_ms": 80.0,
            "makeup_gain_db": 0.0,
        })],
    )

    result = processor.process(audio)
    assert result.shape == audio.shape
    assert float(np.mean(np.abs(result[-200:]))) < 0.7 * float(np.mean(np.abs(audio[-200:])))


def test_eq_boosts_its_center_frequency_and_keeps_state_across_blocks():
    sample_rate = 8000
    time = np.arange(sample_rate, dtype=np.float64) / sample_rate
    mono = (0.2 * np.sin(2.0 * np.pi * 1000.0 * time)).astype(np.float32)
    audio = np.column_stack([mono, mono])

    chain = [_effect("eq", {
        "frequency_hz": 1000.0,
        "gain_db": 6.0,
        "q": 1.0,
    })]
    whole = MasterEffectsProcessor(sample_rate, chain).process(audio)

    split_processor = MasterEffectsProcessor(sample_rate, chain)
    first = split_processor.process(audio[:333])
    second = split_processor.process(audio[333:1777])
    third = split_processor.process(audio[1777:])
    split = np.concatenate([first, second, third], axis=0)

    assert np.allclose(split, whole, atol=1e-6)
    assert float(np.sqrt(np.mean(whole[-4000:] ** 2))) > 1.5 * float(
        np.sqrt(np.mean(audio[-4000:] ** 2))
    )


def test_reverb_emits_tail_after_input_and_processes_continuously():
    sample_rate = 8000
    impulse = np.zeros((128, 2), dtype=np.float32)
    impulse[0] = (0.8, 0.8)
    silence = np.zeros((1024, 2), dtype=np.float32)
    chain = [_effect("reverb", {
        "room_size": 0.55,
        "damping": 0.4,
        "wet": 0.5,
        "dry": 0.5,
        "width": 0.8,
    })]

    whole_processor = MasterEffectsProcessor(sample_rate, chain)
    whole = np.concatenate([
        whole_processor.process(impulse),
        whole_processor.process(silence),
    ])
    split_processor = MasterEffectsProcessor(sample_rate, chain)
    split = np.concatenate([
        split_processor.process(impulse[:53]),
        split_processor.process(impulse[53:]),
        split_processor.process(silence),
    ])

    assert np.allclose(split, whole, atol=1e-7)
    assert whole_processor.tail_frames > 0
    assert float(np.max(np.abs(whole[128:]))) > 1e-5


def test_reset_clears_reverb_delay_history():
    processor = MasterEffectsProcessor(
        8000,
        [_effect("reverb", {"wet": 0.5, "dry": 0.5})],
    )
    impulse = np.zeros((128, 2), dtype=np.float32)
    impulse[0] = 1.0
    processor.process(impulse)
    processor.reset()

    silence = processor.process(np.zeros((512, 2), dtype=np.float32))
    assert np.allclose(silence, 0.0)



def test_live_parameter_update_preserves_matching_reverb_history():
    sample_rate = 8000
    chain = [_effect("reverb", {
        "room_size": 0.45,
        "damping": 0.5,
        "wet": 1.0,
        "dry": 0.0,
        "width": 1.0,
    }, effect_id="room")]
    processor = MasterEffectsProcessor(sample_rate, chain)
    impulse = np.zeros((128, 2), dtype=np.float32)
    impulse[0] = 1.0
    processor.process(impulse[:128])

    updated_chain = [_effect("reverb", {
        "room_size": 0.7,
        "damping": 0.35,
        "wet": 1.0,
        "dry": 0.0,
        "width": 1.0,
    }, effect_id="room")]
    processor.set_chain(updated_chain, preserve_state=True)
    tail = processor.process(np.zeros((128, 2), dtype=np.float32))

    reset_processor = MasterEffectsProcessor(sample_rate, updated_chain)
    reset_tail = reset_processor.process(np.zeros((128, 2), dtype=np.float32))
    assert float(np.max(np.abs(tail))) > 1e-5
    assert np.allclose(reset_tail, 0.0)


def test_serial_reverbs_add_their_tail_windows():
    one = MasterEffectsProcessor(
        8000,
        [_effect("reverb", {"room_size": 0.8, "wet": 0.5})],
    )
    two = MasterEffectsProcessor(
        8000,
        [
            _effect("reverb", {"room_size": 0.8, "wet": 0.5}, effect_id="first"),
            _effect("reverb", {"room_size": 0.8, "wet": 0.5}, effect_id="second"),
        ],
    )
    assert two.tail_frames == 2 * one.tail_frames
