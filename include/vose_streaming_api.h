// vose_streaming_api.h
// JUCE非依存のVO-SE streaming C API型定義。
// vose_core.dll/.so/.dylib とJUCEプラグインの両方から共有する。
#pragma once

#include <cstdint>

using VoseChunkCallback = void (*) (const float* samples, int sample_count,
                                     double position_ms, void* user_data);

struct VoseStreamConfig {
    int    sample_rate;
    int    buffer_ms;
    int    mode_flag;
    float  initial_tempo_bpm;
    VoseChunkCallback on_chunk_ready;
    void*  callback_user_data;
};

struct VoseStreamNote {
    const char*   wav_path;
    int           pitch_length;
    const double* pitch_curve;
    const double* gender_curve;
    const double* tension_curve;
    const double* breath_curve;
    int64_t       note_id;
    const double* portamento_offsets;
    int           portamento_length;
};

using VoseStreamHandle = void*;
