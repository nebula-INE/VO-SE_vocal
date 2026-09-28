// vose_core_internal.h
// ============================================================
// vose_core_4.cpp の内部関数・型を vose_streaming.cpp に公開する
// 内部共有ヘッダ。プロジェクト外には配布しない。
// ============================================================
#pragma once
#include <vector>
#include <memory>
#include <string>
#include "vose_core.h"

// ============================================================
// 前方宣言 / 構造体
// ============================================================

struct EmbeddedVoice {
    std::string         path;
    std::vector<double> waveform;
    int                 fs;
};

struct AnalysisCache {
    std::vector<double> f0;
    std::vector<double> time;
    int                 length    = 0;
    std::vector<double> flat_spec;
    std::vector<double> flat_ap;
    int                 spec_bins = 0;
};

enum class NoteState : uint8_t { INVALID, NO_VOICE, RENDERABLE };

struct NotePrepass {
    NoteState                            state        = NoteState::INVALID;
    int64_t                              note_samples = 0;
    std::shared_ptr<const EmbeddedVoice> ev;
    std::shared_ptr<const EmbeddedVoice> prev_ev;
    OtoEntry                             oto          = {};
    bool                                 has_oto      = false;

    NotePrepass() = default;
    NotePrepass(NoteState s, int64_t ns,
                std::shared_ptr<const EmbeddedVoice> e,
                std::shared_ptr<const EmbeddedVoice> pe = nullptr,
                const OtoEntry* o = nullptr)
        : state(s), note_samples(ns), ev(std::move(e)), prev_ev(std::move(pe))
    { if (o) { oto = *o; has_oto = true; } }
};

struct SynthesisScratchPad {
    std::vector<double>  flat_spec, flat_ap, spec_tmp;
    std::vector<double*> spec_ptrs, ap_ptrs;
    std::vector<double>  f0, time_axis;

    std::vector<double>  flat_spec_prev, flat_ap_prev;
    std::vector<double*> spec_ptrs_prev, ap_ptrs_prev;
    std::vector<double>  f0_prev, time_axis_prev;

    std::vector<double>  flat_mod_ap;
    std::vector<double*> mod_ap_ptrs;

    int reserved_f0 = 0, reserved_bins = 0;

    void ensure_spec(int f0_length, int spec_bins);
    void ensure_f0(int n);
    void ensure_f0_prev(int n);
};

struct SynthNoteParams {
    const NotePrepass& pp;
    NoteEvent&         n;
    int                fft_size;
    int                spec_bins;
    double             global_time_sec = 0.0;
};

// ============================================================
// oto.ini DB への参照
// ============================================================
#include <map>
#include <mutex>
extern std::map<std::string, OtoEntry> g_oto_db;
extern std::mutex                      g_oto_db_mutex;

// ============================================================
// スレッドローカルスクラッチパッド (各スレッドで独立)
// ============================================================
extern thread_local SynthesisScratchPad tl_scratch;

// ============================================================
// 内部関数宣言
// ============================================================

// ボイスDB検索
std::shared_ptr<const EmbeddedVoice> find_voice_ref(const char* key);

// Thread-safe OTO metadata lookup by alias or WAV path.
bool find_oto_for_key(const char* key, OtoEntry& out);

// 解析キャッシュ (メモリ + ディスク 2段キャッシュ)
std::shared_ptr<const AnalysisCache>
get_or_analyze(std::shared_ptr<const EmbeddedVoice> ev_sp, int fft_size, int spec_bins);

// UTAUタイムマッピング
double get_source_ms(const EmbeddedVoice& ev);
double map_time(double t_out_ms, const OtoEntry& oto,
                double source_wav_len_ms, double note_duration_ms);

// カーブリサンプリング
double resample_curve(const double* curve, int src_len, int dst_idx, int dst_len);

// スペクトル DSP
void apply_gender_shift(double* sr, int spec_bins, double gender,
                        double* tmp, double f0_ratio = 1.0);
void apply_tension_breath(double* sr, double* ar, int spec_bins,
                          double tension, double breath);
void blend_transition_spectra(
    double** spec_cur, double** ap_cur, int cur_len,
    double** spec_prev, double** ap_prev, int prev_len,
    int spec_bins, int transition_frames);

// F0 DSP
void smooth_f0_gaussian(double* f0, int f0_length);
void apply_vibrato(double* f0, int f0_length, double frame_period_ms,
                   double global_time_offset_sec,
                   const double* depth_curve,
                   const double* rate_curve,
                   int curve_length);

// ノート合成 (execute_render / synth_loop 共通)
void synthesize_note_impl(const SynthNoteParams& p, std::vector<double>& note_buf);

// 定数
static constexpr int    kFs_internal          = 44100;
static constexpr double kFramePeriod_internal = 5.0;   // ms
static constexpr int    kCrossfadeSamples_internal =
    static_cast<int>(kFs_internal * 0.030);
static constexpr int    kTransitionFrames_internal =
    static_cast<int>(60.0 / kFramePeriod_internal);
