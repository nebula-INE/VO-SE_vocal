"""Offline project-wide WAV mixdown that shares the desktop mixer rules.

The live AudioMixer remains the source of truth for source decoding, resampling,
track activity, gain, pan, and clipping. This module only drives that mixer
without opening an audio device and writes the result in bounded blocks.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Iterable

try:
    import soundfile as sf
except Exception:  # pragma: no cover - exercised in dependency-free installs
    sf = None

from modules.audio.atomic_export import render_wav_atomically


def mixdown_tracks_to_wav(
    tracks: Iterable[Any],
    destination: str | os.PathLike[str],
    *,
    sample_rate: int = 44100,
    block_size: int = 65536,
    progress_callback: Callable[[int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> str:
    """Render all supplied project tracks to one stereo PCM-24 WAV.

    Audio is read and mixed in bounded blocks. The function deliberately reuses
    AudioMixer's private block renderer so offline export has exactly the same
    mute/solo, volume, equal-power pan, resampling, and clipping behavior as
    desktop playback. No sounddevice stream or audio device is opened.

    Invalid referenced files fail the export instead of silently producing a
    partial mix. The destination is replaced only after the complete WAV has
    been written and validated.
    """
    if sf is None:
        raise RuntimeError("プロジェクトのミックスダウンには soundfile が必要です。")

    try:
        output_rate = int(sample_rate)
        block_frames = int(block_size)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("サンプルレートとブロックサイズは正の整数が必要です。") from exc
    if output_rate <= 0 or block_frames <= 0:
        raise ValueError("サンプルレートとブロックサイズは正の整数が必要です。")

    track_list = list(tracks)
    if not track_list:
        raise RuntimeError("ミックスダウンするトラックがありません。")

    # Import locally so callers that only use other audio helpers do not
    # initialize optional output-device dependencies.
    from modules.backend.audio_mixer import AudioMixer

    mixer = AudioMixer(sample_rate=output_rate, block_size=block_frames)
    mixer.set_tracks(track_list)

    if mixer.last_load_errors:
        details = "\n".join(mixer.last_load_errors)
        raise RuntimeError(f"一部の音声トラックを読み込めませんでした。\n{details}")

    sources = dict(mixer._sources)
    if not sources or mixer._total_frames <= 0:
        raise RuntimeError("ミックスダウンできる音声データがありません。")

    target_path = os.path.realpath(os.path.abspath(os.fspath(destination)))
    for source in sources.values():
        source_path = os.path.realpath(os.path.abspath(source.path))
        if source_path == target_path:
            raise ValueError(
                "入力音声ファイルと同じ場所には書き出せません。別のWAVファイル名を指定してください。"
            )

    total_frames = int(mixer._total_frames)

    def _write_mix(temporary_path: str) -> str:
        readers: dict[int, Any] = {}
        failed_sources: set[int] = set()
        try:
            for track_id, source in sources.items():
                readers[track_id] = sf.SoundFile(source.path, mode="r")

            with sf.SoundFile(
                temporary_path,
                mode="w",
                samplerate=output_rate,
                channels=2,
                format="WAV",
                subtype="PCM_24",
            ) as writer:
                cursor = 0
                while cursor < total_frames:
                    if callable(cancel_check) and cancel_check():
                        raise RuntimeError("プロジェクトのミックスダウンがキャンセルされました。")

                    frames = min(block_frames, total_frames - cursor)
                    block = mixer._mix_block(
                        cursor,
                        frames,
                        sources=sources,
                        readers=readers,
                        failed_sources=failed_sources,
                    )
                    writer.write(block)
                    cursor += frames
                    if callable(progress_callback):
                        progress_callback(min(99, int(cursor * 100 / total_frames)))

            if failed_sources:
                errors = mixer.get_diagnostics().get("last_playback_errors", [])
                details = "\n".join(str(item) for item in errors)
                raise RuntimeError(
                    "ミックス中に音声トラックを読み込めませんでした。"
                    + (f"\n{details}" if details else "")
                )

            if callable(cancel_check) and cancel_check():
                raise RuntimeError("プロジェクトのミックスダウンがキャンセルされました。")

            info = sf.info(temporary_path)
            if int(info.samplerate) != output_rate or int(info.channels) != 2:
                raise RuntimeError("ミックスダウンWAVの形式検証に失敗しました。")
            if int(info.frames) != total_frames:
                raise RuntimeError(
                    f"ミックスダウンWAVの長さが一致しません: "
                    f"expected={total_frames}, actual={info.frames}"
                )
            if callable(progress_callback):
                progress_callback(100)
            return temporary_path
        finally:
            for reader in readers.values():
                try:
                    reader.close()
                except Exception:
                    pass

    return render_wav_atomically(destination, _write_mix)
