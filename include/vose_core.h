#ifndef VOSE_CORE_H
#define VOSE_CORE_H

#ifdef _WIN32
    #define DLLEXPORT __declspec(dllexport)
#else
    #define DLLEXPORT __attribute__((visibility("default")))
#endif

#include <stdint.h>
#include <cstdint> 

// ディスクキャッシュの先頭に書き込むヘッダ情報
struct VoseCacheHeader {
    uint32_t magic;     // 'VOSE' (0x45534F56) かどうかを確認するマジックナンバー
    int length;         // フレーム数
    int spec_bins;      // 周波数ビン数
};

// --- GUI（Python）とやり取りするための構造体 ---
// 64bit/32bit環境でサイズが変わらないよう、アライメントを厳密に制御します

struct OtoEntry {
    const char* filename;
    double cutoff;
    char   alias[64];
    char   wav_path[512];
    double offset;       // ms: 左ブランク
    double consonant;    // ms: 子音固定
    double blank;        // ms: 右ブランク（負なら末尾からの距離）
    double preutterance; // ms: 先行発声
    double overlap;      // ms: オーバーラップ
};

#pragma pack(push, 8) 
// 🚀 【新規追加】5msフレーム単位の高精度歌唱タイムライン構造体
// 64bit境界 (double=8bytes) に完全に整列させ、最速のポインタアクセスを実現します
struct VoseFrame {
    double time;         // フレームの時間（秒）
    char phoneme[8];     // 音素名（最大7文字+NULL終端 / 例: "s", "a", "pau", "cl"）
    double weight;       // 子音と母音のクロスフェード・ウェイト（0.0〜1.0）
};

struct NoteEvent {
    const char* wav_path;      // 音源キー（音素名）
    double* pitch_curve;       // 周波数(Hz) ※WORLDに合わせdoubleへ
    int pitch_length;          // 配列の長さ
    
    // 追加パラメータ（精度維持のためdouble）
    double* gender_curve;
    double* tension_curve;
    double* breath_curve;

    // ビブラート制御カーブ
    double* vibrato_depth_curve;
    double* vibrato_rate_curve;
    int     vibrato_curve_length;

    // ★↓↓↓ ここから新規追加（必ず末尾に配置） ↓↓↓★
    double* portamento_offsets;   // 各フレームのピッチオフセット（セント単位）
    int     portamento_length;    // pitch_length と同じか、0なら無効
    // USTノート単位の表現パラメータ。intensity=0..200, modulation=0..100。
    double  intensity;
    double  modulation;
};
#pragma pack(pop)

struct OtoEntry; // 前方宣言

extern "C" {
    // 1. 音源をメモリにパッキングする（内蔵音源化の必須関数）
    DLLEXPORT void load_embedded_resource(const char* phoneme, const int16_t* raw_data, int sample_count);

    // Web Audio APIでデコードしたFloat32 PCMを量子化せずに登録する。
    // load_embedded_resource() は既存のデスクトップ呼び出しとの互換用に残す。
    DLLEXPORT void load_embedded_resource_f32(const char* phoneme, const float* raw_data, int sample_count);

    // 2. レンダリング実行関数
    DLLEXPORT void execute_render(NoteEvent* notes, int note_count, const char* output_path, int mode_flag);

    // 🚀 【新規追加】Python（PipelineBridge）からシリアライズされた連続フレームデータをC++メモリへ流し込む
    // このポインタを渡すだけのゼロコピー転送により、リアルタイム合成時でも一切の遅延が発生しません
    DLLEXPORT void set_vocal_timeline(const VoseFrame* frames, int frame_count);
    
    // 3. エンジン管理
    DLLEXPORT float get_engine_version(void);
    DLLEXPORT void clear_engine_cache(void);

    // 4. BigVGAN ボコーダー（Pro版のみ有効。無印版ビルドでは呼んでも無視される）
    //    onnx_path が nullptr または空文字なら BigVGAN を無効化し、WORLD直接出力に戻す。
    DLLEXPORT void set_bigvgan_model(const char* onnx_path);
}

#endif // VOSE_CORE_H
