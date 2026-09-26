// VoseBridge.h
//
// vose_core.dll/.so/.dylib への橋渡し。
// 構造体レイアウトは include/vose_core.h（本物）と1バイトも違わないように
// 手で複製している。ヘッダ側が更新されたら必ずここも追従すること。
// 特に NoteEvent / VoseFrame は vose_core.h 側で
// #pragma pack(push, 8) ... #pragma pack(pop) に囲まれているため、
// ここでも同じ pack(8) 指定を忘れないこと（忘れるとフィールドオフセットが
// ズレて即クラッシュする）。
//
// VoseStreamConfig / VoseStreamNote は include/vose_streaming.h の実物を
// 反映済み（2026-07-05確定）。フィールド順が1つでも変わったら必ず追従すること。

#pragma once

#include <cstdint>
#include <juce_core/juce_core.h>

extern "C" {

// vose_core.h より: pack指定なし（デフォルトアライメント）
struct OtoEntry {
    const char* filename;
    double      cutoff;
    char        alias[64];
    char        wav_path[512];
    double      offset;
    double      consonant;
    double      blank;
    double      preutterance;
    double      overlap;
};

#pragma pack(push, 8)

struct VoseFrame {
    double time;
    char   phoneme[8];
    double weight;
};

struct NoteEvent {
    const char* wav_path;
    double*     pitch_curve;
    int         pitch_length;

    double*     gender_curve;
    double*     tension_curve;
    double*     breath_curve;

    double*     vibrato_depth_curve;
    double*     vibrato_rate_curve;
    int         vibrato_curve_length;

    double*     portamento_offsets;
    int         portamento_length;

    double      intensity;
    double      modulation;
};

#pragma pack(pop)

// ------------------------------------------------------------------
// ストリーミングAPI用構造体 (include/vose_streaming.h と1:1対応)
// フィールド順は実物のヘッダの順序を厳守すること。
// ------------------------------------------------------------------
using VoseChunkCallback = void (*) (const float* samples, int sample_count,
                                     double position_ms, void* user_data);

struct VoseStreamConfig {
    int    sample_rate;          // 出力サンプルレート (通常 44100)
    int    buffer_ms;             // 先行バッファ量 [ms] (推奨: 200〜500)
    int    mode_flag;             // 0=Free(16bit), 1=Pro(32bit)
    float  initial_tempo_bpm;    // 初期テンポ（後から変更可）
    VoseChunkCallback on_chunk_ready; // nullptrならpullモード
    void*  callback_user_data;
};

struct VoseStreamNote {
    const char*   wav_path;       // 音源キー (oto.ini alias)
    int           pitch_length;   // ピッチフレーム数
    const double* pitch_curve;    // [pitch_length]
    const double* gender_curve;   // [pitch_length] (null = 0.5)
    const double* tension_curve;  // [pitch_length] (null = 0.5)
    const double* breath_curve;   // [pitch_length] (null = 0.5)
    int64_t       note_id;        // 更新/差し替えに使用するID

    const double* portamento_offsets; // セント単位。nullptr可
    int           portamento_length;  // 0なら無効
};

using VoseStreamHandle = void*;

} // extern "C"

// ============================================================
// 関数ポインタ型（vose_core.h / vose_streaming.h の extern "C" ブロックと1:1対応）
// ============================================================
using Fn_load_embedded_resource    = void (*)(const char*, const int16_t*, int);
using Fn_load_embedded_resource_f32 = void (*)(const char*, const float*, int);
using Fn_execute_render         = void  (*)(NoteEvent*, int, const char*, int);
using Fn_set_vocal_timeline     = void  (*)(const VoseFrame*, int);
using Fn_get_engine_version     = float (*)();
using Fn_clear_engine_cache     = void  (*)();

using Fn_init_official_engine   = void (*)();
using Fn_set_oto_data           = void (*)(const OtoEntry*, int);
using Fn_set_bigvgan_model      = void (*)(const char*);

// --- ストリーミングAPI（src/vose_streaming_final.cpp）---
using Fn_streaming_render_create      = VoseStreamHandle (*)(const VoseStreamConfig*);
using Fn_streaming_render_push_note   = void   (*)(VoseStreamHandle, const VoseStreamNote*);
using Fn_streaming_render_pull        = int    (*)(VoseStreamHandle, float*, int);
using Fn_streaming_render_buffered_ms = double (*)(VoseStreamHandle);
using Fn_streaming_render_set_tempo   = void   (*)(VoseStreamHandle, float);
using Fn_streaming_render_destroy     = void   (*)(VoseStreamHandle);

// ============================================================
// VoseCoreLibrary
//
// vose_core 共有ライブラリの動的ロードとシンボル解決を1箇所に集約する。
// Python版 (_load_core_library) の探索ロジックと同じ考え方で、
// OSごとの拡張子違いを吸収する。
// ============================================================
class VoseCoreLibrary
{
public:
    bool load (const juce::File& pluginBinaryDir)
    {
       #if JUCE_WINDOWS
        const char* libName = "vose_core.dll";
       #elif JUCE_MAC
        const char* libName = "libvose_core.dylib";
       #else
        const char* libName = "libvose_core.so";
       #endif

        juce::Array<juce::File> candidates {
            pluginBinaryDir.getChildFile (libName),
            pluginBinaryDir.getChildFile ("bin").getChildFile (libName)
        };

        for (auto& f : candidates)
        {
            if (f.existsAsFile() && dll.open (f.getFullPathName()))
            {
                load_embedded_resource    = (Fn_load_embedded_resource)    dll.getFunction ("load_embedded_resource");
                load_embedded_resource_f32 = (Fn_load_embedded_resource_f32) dll.getFunction ("load_embedded_resource_f32");
                execute_render         = (Fn_execute_render)         dll.getFunction ("execute_render");
                set_vocal_timeline     = (Fn_set_vocal_timeline)     dll.getFunction ("set_vocal_timeline");
                get_engine_version     = (Fn_get_engine_version)     dll.getFunction ("get_engine_version");
                clear_engine_cache     = (Fn_clear_engine_cache)     dll.getFunction ("clear_engine_cache");

                init_official_engine   = (Fn_init_official_engine)   dll.getFunction ("init_official_engine");
                set_oto_data           = (Fn_set_oto_data)           dll.getFunction ("set_oto_data");
                set_bigvgan_model      = (Fn_set_bigvgan_model)      dll.getFunction ("set_bigvgan_model");

                streaming_render_create      = (Fn_streaming_render_create)      dll.getFunction ("streaming_render_create");
                streaming_render_push_note   = (Fn_streaming_render_push_note)   dll.getFunction ("streaming_render_push_note");
                streaming_render_pull        = (Fn_streaming_render_pull)        dll.getFunction ("streaming_render_pull");
                streaming_render_buffered_ms = (Fn_streaming_render_buffered_ms) dll.getFunction ("streaming_render_buffered_ms");
                streaming_render_set_tempo   = (Fn_streaming_render_set_tempo)   dll.getFunction ("streaming_render_set_tempo");
                streaming_render_destroy     = (Fn_streaming_render_destroy)     dll.getFunction ("streaming_render_destroy");

                loaded = (execute_render != nullptr);
                hasStreamingApi = (streaming_render_create != nullptr &&
                                   streaming_render_pull   != nullptr &&
                                   streaming_render_destroy != nullptr);

                if (loaded && init_official_engine != nullptr)
                    init_official_engine();

                if (loaded && get_engine_version != nullptr)
                    lastKnownVersion = get_engine_version();

                return loaded;
            }
        }
        return false;
    }

    bool isLoaded() const { return loaded; }
    bool supportsStreaming() const { return hasStreamingApi; }
    float getLastKnownVersion() const { return lastKnownVersion; }

    Fn_load_embedded_resource    load_embedded_resource    = nullptr;
    Fn_load_embedded_resource_f32 load_embedded_resource_f32 = nullptr;
    Fn_execute_render         execute_render         = nullptr;
    Fn_set_vocal_timeline     set_vocal_timeline     = nullptr;
    Fn_get_engine_version     get_engine_version     = nullptr;
    Fn_clear_engine_cache     clear_engine_cache     = nullptr;

    Fn_init_official_engine   init_official_engine   = nullptr;
    Fn_set_oto_data           set_oto_data           = nullptr;
    Fn_set_bigvgan_model      set_bigvgan_model      = nullptr;

    Fn_streaming_render_create      streaming_render_create      = nullptr;
    Fn_streaming_render_push_note   streaming_render_push_note   = nullptr;
    Fn_streaming_render_pull        streaming_render_pull        = nullptr;
    Fn_streaming_render_buffered_ms streaming_render_buffered_ms = nullptr;
    Fn_streaming_render_set_tempo   streaming_render_set_tempo   = nullptr;
    Fn_streaming_render_destroy     streaming_render_destroy     = nullptr;

private:
    juce::DynamicLibrary dll;
    bool  loaded = false;
    bool  hasStreamingApi = false;
    float lastKnownVersion = 0.0f;
};
