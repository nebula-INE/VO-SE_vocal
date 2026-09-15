// vose_streaming.cpp  (統合版)
// ============================================================
// VOSE Streaming Synthesis Engine
//
// このファイルは vose_core_4.cpp (パッチ済み) と同じプロジェクトに
// リンクする。vose_core_internal.h 経由で内部関数を共有する。
//
// ビルド例 (Linux/clang++):
//   clang++ -std=c++17 -O2 -fPIC -shared \
//     vose_core_4.cpp vose_streaming.cpp \
//     -Iworld -Lworld -lworld -lpthread \
//     -o libvose.so
//
// ビルド例 (Windows/MSVC):
//   cl /std:c++17 /O2 /LD vose_core_4.cpp vose_streaming.cpp
//      /I world /link world.lib /OUT:vose.dll
// ============================================================

#include "vose_streaming.h"
#include "vose_core_internal.h"
#include "world/cheaptrick.h"   // GetFFTSizeForCheapTrick

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <memory>
#include <mutex>

// --- clamp polyfill (for C++14/macOS libc++) ---
#ifndef HAVE_STD_CLAMP
template <typename T>
constexpr const T& clamp(const T& v, const T& lo, const T& hi) {
    return (v < lo) ? lo : (hi < v) ? hi : v;
}
#endif
#include <thread>
#include <vector>
#include <cmath>
#include <algorithm>

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// ============================================================
// lock-free Ring Buffer (Single-Producer / Single-Consumer)
//
// head_ (書き込み位置) は合成スレッドのみが進める。
// tail_ (読み出し位置) は pull 呼び出し側のみが進める。
// → ミューテックス不要。キャッシュライン競合を避けるため
//   head_ と tail_ は別々の atomic に分離している。
// ============================================================
template<typename T>
class RingBuffer {
public:
    explicit RingBuffer(size_t capacity)
        : mask_(next_pow2(capacity) - 1)
        , buf_(mask_ + 1)
        , head_(0), tail_(0) {}

    // 生産者 (合成スレッド): n 要素書き込む
    bool write(const T* src, size_t n) {
        const uint64_t h = head_.load(std::memory_order_relaxed);
        const uint64_t t = tail_.load(std::memory_order_acquire);
        if ((mask_ + 1) - static_cast<size_t>(h - t) < n) return false;
        for (size_t i = 0; i < n; ++i) buf_[(h + i) & mask_] = src[i];
        head_.store(h + n, std::memory_order_release);
        return true;
    }

    // 消費者 (pull / コールバック): 最大 n 要素読み出す
    size_t read(T* dst, size_t n) {
        const uint64_t t      = tail_.load(std::memory_order_relaxed);
        const uint64_t h      = head_.load(std::memory_order_acquire);
        const size_t   actual = std::min(n, static_cast<size_t>(h - t));
        for (size_t i = 0; i < actual; ++i) dst[i] = buf_[(t + i) & mask_];
        tail_.store(t + actual, std::memory_order_release);
        return actual;
    }

    size_t available() const {
        return static_cast<size_t>(
            head_.load(std::memory_order_acquire) -
            tail_.load(std::memory_order_relaxed));
    }

private:
    static size_t next_pow2(size_t v) {
        --v; for (size_t i=1; i<sizeof(size_t)*8; i<<=1) v|=v>>i; return ++v;
    }
    const size_t          mask_;
    std::vector<T>        buf_;
    std::atomic<uint64_t> head_, tail_;
};

// ============================================================
// QueuedNote / NoteQueue
// ============================================================
struct QueuedNote {
    int64_t             note_id      = 0;
    int                 pitch_length = 0;
    std::vector<double> pitch_curve, gender_curve, tension_curve, breath_curve;
    std::string         wav_path;
    std::vector<double> portamento_curve;
};

// ② NoteQueue::push メソッド内で portamento をコピー（既存の push 実装に追記）
void push(const VoseStreamNote& n) {
    QueuedNote qn;
    qn.note_id      = n.note_id;
    qn.pitch_length = n.pitch_length;
    qn.wav_path     = n.wav_path ? n.wav_path : "";

    auto fill = [&](const double* src, std::vector<double>& dst, double def) {
        dst.resize(n.pitch_length);
        if (src) std::copy(src, src + n.pitch_length, dst.begin());
        else     std::fill(dst.begin(), dst.end(), def);
    };
    fill(n.pitch_curve,   qn.pitch_curve,   440.0);
    fill(n.gender_curve,  qn.gender_curve,  0.5);
    fill(n.tension_curve, qn.tension_curve, 0.5);
    fill(n.breath_curve,  qn.breath_curve,  0.0);

    // ★ ポルタメントカーブのコピー（データがあれば）
    if (n.portamento_offsets && n.portamento_length > 0) {
        qn.portamento_curve.resize(n.portamento_length);
        std::copy(n.portamento_offsets, n.portamento_offsets + n.portamento_length,
                  qn.portamento_curve.begin());
    } else {
        qn.portamento_curve.clear();
    }

        std::unique_lock<std::mutex> lk(mu_);
        for (auto it = q_.begin(); it != q_.end(); ++it) {
            if (it->note_id == n.note_id) { q_.erase(it, q_.end()); break; }
        }
        q_.push_back(std::move(qn));
        cv_.notify_one();
    }

    bool pop(QueuedNote& out, const std::atomic<bool>& cancelled) {
        std::unique_lock<std::mutex> lk(mu_);
        cv_.wait(lk, [&]{ return !q_.empty() || cancelled.load(); });
        if (cancelled.load()) return false;
        out = std::move(q_.front());
        q_.pop_front();
        return true;
    }

    void cancel() { std::unique_lock<std::mutex> lk(mu_); cv_.notify_all(); }

private:
    std::mutex              mu_;
    std::condition_variable cv_;
    std::deque<QueuedNote>  q_;
};

// ============================================================
// StreamingSynthesizer
// ============================================================
class StreamingSynthesizer {
public:
    explicit StreamingSynthesizer(const VoseStreamConfig& cfg)
        : cfg_(cfg)
        , ring_(static_cast<size_t>(cfg.sample_rate) * (cfg.buffer_ms + 2000) / 1000 * 2)
        , cancelled_(false)
        , position_ms_(0.0)
        , tempo_bpm_(cfg.initial_tempo_bpm > 0.0f ? cfg.initial_tempo_bpm : 120.0f)
    {
        worker_ = std::thread([this]{ synth_loop(); });
    }

    ~StreamingSynthesizer() {
        cancelled_.store(true);
        note_queue_.cancel();
        if (worker_.joinable()) worker_.join();
    }

    void   push_note(const VoseStreamNote& n)  { note_queue_.push(n); }
    int    pull(float* out, int n)              { return static_cast<int>(ring_.read(out, n)); }
    double buffered_ms() const                 { return static_cast<double>(ring_.available()) / cfg_.sample_rate * 1000.0; }
    void   set_tempo(float bpm)                { tempo_bpm_.store(bpm); }

private:
    // ============================================================
    // synth_loop — 合成スレッド本体
    //
    //   synthesize_note_impl を呼ぶことで execute_render と
    //   完全に同一の合成パイプラインを使う。音質の差ゼロ。
    //
    //   バッファ制御:
    //     buffer_ms の 75% を超えたら 10ms 待機 (CPU 節約)
    //     buffer_ms の 75% を下回ったら即座に次ノートを合成開始
    //     → 再生カーソルに対して常に ~N ms 先行して PCM を供給
    // ============================================================
    void synth_loop() {
        CheapTrickOption ct_opt;
        InitializeCheapTrickOption(kFs_internal, &ct_opt);
        const int fft_size  = ct_opt.fft_size;
        const int spec_bins = fft_size / 2 + 1;

        std::shared_ptr<const EmbeddedVoice> prev_ev = nullptr;
        std::vector<double> note_buf;
        std::vector<float>  chunk;

        while (!cancelled_.load()) {
            // バッファが十分埋まっていたら待機
            while (!cancelled_.load() &&
                   buffered_ms() > static_cast<double>(cfg_.buffer_ms) * 0.75) {
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            }
            if (cancelled_.load()) break;

            // 次ノートを取得（ブロッキング）
            QueuedNote qn;
            if (!note_queue_.pop(qn, cancelled_)) break;


            const int pl = qn.pitch_length;
            if (pl <= 0) { prev_ev = nullptr; continue; }

            // ボイス解決
            auto ev = find_voice_ref(qn.wav_path.c_str());
            if (!ev) { prev_ev = nullptr; continue; }

            // NoteEvent を一時構築（カーブはスタック上のベクタを直接ポイント）
            NoteEvent tmp_n = {};
            tmp_n.wav_path      = qn.wav_path.c_str();
            tmp_n.pitch_length  = qn.pitch_length;
            tmp_n.pitch_curve   = qn.pitch_curve.data();
            tmp_n.gender_curve  = qn.gender_curve.data();
            tmp_n.tension_curve = qn.tension_curve.data();
            tmp_n.breath_curve  = qn.breath_curve.data();

            tmp_n.portamento_offsets = qn.portamento_curve.empty() ? nullptr : qn.portamento_curve.data();
            tmp_n.portamento_length  = static_cast<int>(qn.portamento_curve.size());


            // oto.ini エントリ取得（streaming でも正しくタイムマッピングする）
            const OtoEntry* found_oto = nullptr;
            {
                std::unique_lock<std::mutex> lk(g_oto_db_mutex);
                auto it = g_oto_db.find(qn.wav_path);
                if (it != g_oto_db.end()) found_oto = &it->second;
            }

            // note_samples (execute_render と同じ計算式)
            const int64_t note_samples =
                (static_cast<int64_t>(pl) - 1) *
                kFramePeriod_internal / 1000.0 * kFs_internal + 1;

            // NotePrepass 構築
            // prev_ev を渡すことで blend_transition_spectra が自動的に適用される
            NotePrepass pp(
                NoteState::RENDERABLE,
                note_samples,
                ev,
                prev_ev,    // クロスフェード用前ノートボイス
                found_oto   // oto.ini エントリ（タイムマッピングに使用）
            );

            // ===================================================
            // 合成 — execute_render と完全同一のパイプライン
            //   Harvest → CheapTrick → D4C → VOSE_Synthesis
            //   gender/tension/breath/vibrato/blend も全て適用
            // ===================================================
            SynthNoteParams params{ pp, tmp_n, fft_size, spec_bins };
            synthesize_note_impl(params, note_buf);

            // クロスフェードのフェードイン (先頭だけ前ノートとブレンド)
            const int xfade = (prev_ev != nullptr) ? kCrossfadeSamples_internal : 0;
            const int64_t out_len = static_cast<int64_t>(note_buf.size());

            chunk.resize(out_len);
            for (int64_t s = 0; s < out_len; ++s) {
                double v = note_buf[s];
                if (s < xfade) {
                    const double fi = 0.5 * (1.0 - std::cos(M_PI * s / xfade));
                    v *= fi;
                }
                chunk[s] = static_cast<float>(clamp(v, -1.0, 1.0));
            }

            // RingBuffer に書き込み（満杯なら待機してリトライ）
            size_t written = 0;
            while (written < static_cast<size_t>(out_len) && !cancelled_.load()) {
                const size_t remain = static_cast<size_t>(out_len) - written;
                if (ring_.write(chunk.data() + written, remain)) {
                    written += remain;
                    if (cfg_.on_chunk_ready) {
                        cfg_.on_chunk_ready(chunk.data(), static_cast<int>(out_len),
                                            position_ms_.load(), cfg_.callback_user_data);
                    }
                } else {
                    std::this_thread::sleep_for(std::chrono::milliseconds(5));
                }
            }

            // タイムスタンプ更新（クロスフェード分を差し引く）
            // std::atomic<double>はfetch_add未サポートのためload/setで加算
            double pos = position_ms_.load();
            pos += static_cast<double>(out_len - xfade) / kFs_internal * 1000.0;
            position_ms_.store(pos);

            prev_ev = ev;  // 次ノートのクロスフェード用
        }
    }

    VoseStreamConfig        cfg_;
    RingBuffer<float>       ring_;
    NoteQueue               note_queue_;
    std::thread             worker_;
    std::atomic<bool>       cancelled_;
    std::atomic<double>     position_ms_;
    std::atomic<float>      tempo_bpm_;
};

// ============================================================
// C API
// ============================================================
extern "C" {

DLLEXPORT VoseStreamHandle streaming_render_create(const VoseStreamConfig* cfg) {
    if (!cfg) return nullptr;
    return static_cast<VoseStreamHandle>(new StreamingSynthesizer(*cfg));
}

DLLEXPORT void streaming_render_push_note(VoseStreamHandle h, const VoseStreamNote* n) {
    if (h && n) static_cast<StreamingSynthesizer*>(h)->push_note(*n);
}

DLLEXPORT int streaming_render_pull(VoseStreamHandle h, float* buf, int max_samples) {
    if (!h || !buf || max_samples <= 0) return 0;
    return static_cast<StreamingSynthesizer*>(h)->pull(buf, max_samples);
}

DLLEXPORT double streaming_render_buffered_ms(VoseStreamHandle h) {
    return h ? static_cast<StreamingSynthesizer*>(h)->buffered_ms() : 0.0;
}

DLLEXPORT void streaming_render_set_tempo(VoseStreamHandle h, float bpm) {
    if (h && bpm > 0.0f) static_cast<StreamingSynthesizer*>(h)->set_tempo(bpm);
}

DLLEXPORT void streaming_render_destroy(VoseStreamHandle h) {
    delete static_cast<StreamingSynthesizer*>(h);
}

} // extern "C"
