//vose_core.cpp
//合成メインエンジン

#include <vector>
#include <string>
#include <map>
#include <unordered_map>
#include <list>
#include <algorithm>
#include <cmath>
#include <sys/stat.h>
#include <fstream>
#include <iomanip>    
#include <random>
#include <sstream>
#include <cstring>
#include <cstdint>
#include <cstdio>
#include <cerrno>
#include <future>
#include <thread>
#include <mutex>
#include <condition_variable>
#include <memory>
#include <atomic>
#include <chrono>
#include <cctype>
#include <functional>

#if defined(__EMSCRIPTEN__)
struct VoseSharedMutex {
    void lock() {}
    void unlock() {}
    bool try_lock() { return true; }
    void lock_shared() {}
    void unlock_shared() {}
    bool try_lock_shared() { return true; }
};
template <typename T>
struct VoseSharedLock {
    explicit VoseSharedLock(T&) {}
};
#else
#include <shared_mutex>
using VoseSharedMutex = std::shared_mutex;
template <typename T>
using VoseSharedLock = std::shared_lock<T>;
#endif

// --- clamp polyfill (for C++14/macOS libc++) ---
// [修正] <algorithm> を読み込んだ「後」に判定する。
// __cpp_lib_clamp は <algorithm> が C++17 以降でのみ定義するフィーチャーテストマクロなので、
// これより前に判定すると常に「未定義」扱いになり、std::clamp が使える環境でも
// 独自 clamp() を定義してしまい、コード中で std::clamp と bare clamp が混在する
// 原因になっていた（名前解決の予期せぬ挙動の温床）。
// ここで一本化し、以降は常に無名前空間の clamp(...) を使う。
#if defined(__cpp_lib_clamp)
using std::clamp;
#else
template <typename T>
constexpr const T& clamp(const T& v, const T& lo, const T& hi) {
    return (v < lo) ? lo : (hi < v) ? hi : v;
}
#endif

// 先に型定義を完了させ、ONNXセッション側での未定義エラーを防ぐ
using VoseMutex = std::mutex;
using VoseUniqueLock = std::unique_lock<std::mutex>;

// --- Windows (MSVC) と POSIX (macOS/Linux) のクロスプラットフォーム吸収マクロ ---
#if defined(_WIN32) || defined(_WIN64)
#  define WIN32_LEAN_AND_MEAN  // windows.hによる余計なヘッダ巻き込みを抑制
#  ifndef NOMINMAX
#    define NOMINMAX           // std::min/std::maxとの衝突を防ぐ(このファイルで多用しているため必須)
#  endif
#  include <windows.h>         // MultiByteToWideChar (UTF-8→UTF-16変換) 用
#  include <io.h>
#  include <process.h>
#  include <direct.h>     // Windowsの_mkdir用
#  define access _access
#  define F_OK 0
#  define mkdir(path, mode) _mkdir(path) // 2引数版をWindows用に1引数ラップ
#  define unlink _unlink // save_cache() の破損.tmpファイル削除で使用
#else
#  include <unistd.h>
#endif

// _USE_MATH_DEFINES の再定義警告(C4005)および未定義対策
#ifndef _USE_MATH_DEFINES
#define _USE_MATH_DEFINES
#endif
#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

// BigVGAN ONNX Runtime
#ifdef VOSE_PRO
#include <onnxruntime_cxx_api.h>
static std::unique_ptr<Ort::Session> g_bigvgan_session;
static VoseSharedMutex               g_bigvgan_mutex; // 前方で定義済みのため安全
#endif

#include "vose_core.h"
#include "voice_data.h"
// ...existing code...

#include "world/synthesis.h"
#include "world/cheaptrick.h"
#include "world/d4c.h"
#include "world/harvest.h"
#include "world/stonemask.h"
#include "world/audioio.h"
#include "world/constantnumbers.h"

// fs::path などは std::string で代用


static std::vector<VoseFrame> g_vocal_timeline;
static VoseMutex g_timeline_mutex;

// ============================================================
// FNV-1a ハッシュ
// ============================================================

static uint64_t fnv1a_hash(const std::string& str) {
    uint64_t hash = 0xcbf29ce484222325ULL;
    for (char c : str) {
        hash ^= static_cast<uint64_t>(c);
        hash *= 0x100000001b3ULL;
    }
    return hash;
}

static std::string generate_cache_hash(const std::string& wav_path) {
    struct stat st;
    if (stat(wav_path.c_str(), &st) == 0) {
        // ファイルが存在する場合: パス + 更新時刻 + サイズでハッシュ
        auto last_time = static_cast<long long>(st.st_mtime);
        auto file_size = static_cast<unsigned long long>(st.st_size);
        std::string seed = wav_path + std::to_string(last_time) + std::to_string(file_size);
        uint64_t h = fnv1a_hash(seed);
        std::stringstream ss;
        ss << std::hex << std::setw(16) << std::setfill('0') << h;
        return ss.str();
    } else {
        // エンベッドボイス（ファイルシステム上に存在しない）:
        uint64_t h = fnv1a_hash(wav_path);
        std::stringstream ss;
        ss << "emb_" << std::hex << std::setw(16) << std::setfill('0') << h;
        return ss.str();
    }
}

// ============================================================
// oto.ini DB
// ============================================================

std::map<std::string, OtoEntry> g_oto_db;
VoseMutex g_oto_db_mutex;

extern "C" void set_oto_data(const OtoEntry* entries, int count) {
    VoseUniqueLock lock(g_oto_db_mutex);
    g_oto_db.clear();
    if (!entries || count <= 0) return;
    for (int i = 0; i < count; ++i)
        g_oto_db[entries[i].alias] = entries[i];
}

// ============================================================
// データ構造
// ============================================================

struct EmbeddedVoice {
    std::string         path;
    std::vector<double> waveform;
    int                 fs;
};

// ============================================================
// VoiceDbStore — LRU エビクション付き音源波形キャッシュ
//
// 問題: 従来の g_voice_db (std::map) には上限がなく、長時間セッションで
//   多数の音源ファイル/エンベッドボイスを読み込むと、波形データ
//   (EmbeddedVoice::waveform) がメモリ上に無制限に蓄積し続けていた。
//   clear_engine_cache() を明示的に呼ばない限り解放されない。
//
// 解決: g_analysis_cache と同じ LRU パターンを適用し、
//   最大エントリ数 kMaxEntries を超えたら最も使われていない音源を追い出す。
// ============================================================
class VoiceDbStore {
    using Key   = std::string;
    using Value = std::shared_ptr<const EmbeddedVoice>;

    // 音源1件あたりのサイズは波形長に依存し様々だが、通常の音源集
    // （数百〜数千音素）を想定した上限。必要ならビルド時に調整可能。
    static constexpr size_t kMaxEntries = 4096;

    mutable VoseSharedMutex mtx;
    std::list<std::pair<Key, Value>> lru_list;
    std::unordered_map<Key, std::list<std::pair<Key, Value>>::iterator> index;

public:
    Value get(const Key& key) {
        std::unique_lock<VoseSharedMutex> lock(mtx); // splice は変更操作のため排他ロック
        auto it = index.find(key);
        if (it == index.end()) return nullptr;
        lru_list.splice(lru_list.begin(), lru_list, it->second);
        return it->second->second;
    }

    void put(const Key& key, const Value& val) {
        std::unique_lock<VoseSharedMutex> lock(mtx);
        auto it = index.find(key);
        if (it != index.end()) {
            lru_list.erase(it->second);
            index.erase(it);
        }
        lru_list.push_front({key, val});
        index[key] = lru_list.begin();

        while (index.size() > kMaxEntries) {
            auto last = std::prev(lru_list.end());
            index.erase(last->first);
            lru_list.pop_back();
        }
    }

    void erase(const Key& key) {
        std::unique_lock<VoseSharedMutex> lock(mtx);
        auto it = index.find(key);
        if (it == index.end()) return;
        lru_list.erase(it->second);
        index.erase(it);
    }

    void clear() {
        std::unique_lock<VoseSharedMutex> lock(mtx);
        lru_list.clear();
        index.clear();
    }
};

static VoiceDbStore g_voice_db;

struct AnalysisCache {
    std::vector<double> f0;
    std::vector<double> time;
    int                 length    = 0;
    std::vector<double> flat_spec;
    std::vector<double> flat_ap;
    int                 spec_bins = 0;
};

// ============================================================
// AnalysisCacheStore — LRU エビクション付きメモリキャッシュ
//
// 問題: g_analysis_cache が無制限に増える。
//   100音素 × (harvest_len=2000) × (spec_bins=513) × 2配列 × 8byte
//   ≈ 1音素あたり約16MB → 100音素で1.6GB
//
// 解決: 最大エントリ数を kMaxCacheEntries に制限し、
//   LRU（Least Recently Used）で古いエントリを追い出す。
//   アクセス順を std::list で管理し、O(1) エビクションを実現する。
// ============================================================
// VO-SE専用の型定義（既存の定義に合わせて調整してください）
struct AnalysisCache; 

static constexpr size_t kMaxCacheEntries = 1024; // キャッシュ上限を拡張

class CacheStore {
    using Key   = std::string;
    using Value = std::shared_ptr<const AnalysisCache>;

private:
    mutable VoseSharedMutex mtx;
    std::list<std::pair<Key, Value>> lru_list;
    std::unordered_map<Key, std::list<std::pair<Key, Value>>::iterator> index;

public:
    // 読み取り（共有ロック＋MRU移動）
    Value get(const Key& key) {
        std::unique_lock<VoseSharedMutex> lock(mtx);  // splice は変更操作のため排他ロック
        auto it = index.find(key);
        if (it == index.end()) return nullptr;
        lru_list.splice(lru_list.begin(), lru_list, it->second);
        return it->second->second;
    }

    // 書き込み（排他ロック）
    void put(const Key& key, const Value& val) {
        std::unique_lock<VoseSharedMutex> lock(mtx);
        auto it = index.find(key);
        if (it != index.end()) {
            lru_list.erase(it->second);
            index.erase(it);
        }
        lru_list.push_front({key, val});
        index[key] = lru_list.begin();

        while (index.size() > kMaxCacheEntries) {
            auto last = std::prev(lru_list.end());
            index.erase(last->first);
            lru_list.pop_back();
        }
    }

    void erase(const Key& key) {
        std::unique_lock<VoseSharedMutex> lock(mtx);
        auto it = index.find(key);
        if (it == index.end()) return;
        lru_list.erase(it->second);
        index.erase(it);
    }

    size_t size() const {
        VoseSharedLock<VoseSharedMutex> lock(mtx);
        return index.size();
    }

    void clear() {
        std::unique_lock<VoseSharedMutex> lock(mtx);
        lru_list.clear();
        index.clear();
    }
};

static CacheStore g_analysis_cache;
static std::mutex g_analysis_cache_mutex;

// ============================================================
// KeyedLockRegistry — 同一キーに対する重複処理を防ぐための
// per-key ミューテックス。
//
// 問題: get_or_analyze() は「メモリキャッシュ確認 → ディスクキャッシュ
//   確認 → 新規解析(Harvest/CheapTrick/D4C) → 保存」という一連の流れを
//   一切ロックせずに実行していた。同一音源を使う複数ノートが並列
//   ワーカーへ振り分けられた場合、以下が起こり得る:
//     1) 同じ音源の重い解析処理が複数スレッドで無駄に重複実行される
//     2) save_cache() が同じ一時ファイル(cache_path + ".tmp")へ
//        複数スレッドから同時書き込みし、rename() が競合してキャッシュ
//        ファイルが破損する可能性がある
//
// 解決: キーごとに個別の mutex を割り当て、get_or_analyze() の
//   「チェック→解析→保存」区間をキー単位で直列化する。異なるキー同士は
//   並列のまま進められるため、g_analysis_cache_mutex による全体直列化
//   よりも並列性を落とさずに済む。
// ============================================================
class KeyedLockRegistry {
    std::mutex map_mutex;
    std::unordered_map<std::string, std::shared_ptr<std::mutex>> locks;

public:
    std::shared_ptr<std::mutex> get_lock(const std::string& key) {
        std::lock_guard<std::mutex> lg(map_mutex);
        auto it = locks.find(key);
        if (it != locks.end()) return it->second;
        auto m = std::make_shared<std::mutex>();
        locks.emplace(key, m);
        return m;
    }
};

static KeyedLockRegistry g_analysis_lock_registry;

// ============================================================
// NoteState / NotePrepass
// ============================================================

enum class NoteState : uint8_t { INVALID, NO_VOICE, RENDERABLE };

struct NotePrepass {
    NoteState                            state        = NoteState::INVALID;
    int64_t                              note_samples = 0;
    std::shared_ptr<const EmbeddedVoice> ev;
    std::shared_ptr<const EmbeddedVoice> prev_ev;
    // raw pointer ではなく値コピー。
    // g_oto_db は set_oto_data() で再構築されうるため、
    // ポインタを長命なオブジェクトに保持すると UB になる。
    OtoEntry                             oto          = {};
    bool                                 has_oto      = false;

    NotePrepass() = default;
    NotePrepass(NoteState s, int64_t ns,
                std::shared_ptr<const EmbeddedVoice> e,
                std::shared_ptr<const EmbeddedVoice> pe = nullptr,
                const OtoEntry* o = nullptr)
        : state(s), note_samples(ns), ev(std::move(e)),
          prev_ev(std::move(pe))
    {
        if (o) { oto = *o; has_oto = true; }
    }
};

// ============================================================
// SynthesisScratchPad
// ============================================================


struct SynthesisScratchPad {
    // 平坦化（1次元化）された動的バッファ
    std::vector<double>  flat_spec, flat_ap, spec_tmp;
    std::vector<double*> spec_ptrs, ap_ptrs;
    
    std::vector<double>  f0, time_axis;

    std::vector<double>  flat_spec_prev, flat_ap_prev;
    std::vector<double*> spec_ptrs_prev, ap_ptrs_prev;
    
    std::vector<double>  f0_prev, time_axis_prev;

    std::vector<double>  flat_mod_ap;
    std::vector<double*> mod_ap_ptrs;

    // 実際にバッファとして確保されている「最大サイズ」
    int reserved_f0 = 0;
    int reserved_bins = 0;

    // 【重要】今回の呼び出しにおける「実際の有効なストライド（列数）」を保持
    int current_bins = 0;

    /**
     * @brief スペクトログラム行列のメモリを安全に確保・更新する
     * @param f0_length 要求された時間フレーム数 (行数)
     * @param spec_bins 要求された周波数ビン数 (列数)
     */
    void ensure_spec(int f0_length, int spec_bins) {
        // 今回の有効な列数を記録（データアクセスの安全弁）
        current_bins = spec_bins;

        // 行数または列数が、過去に確保した最大サイズを超えている場合のみリサイズ
        const bool needs_resize = (f0_length > reserved_f0 || spec_bins > reserved_bins);
        
        if (needs_resize) {
            // キャパシティを最大値に更新
            reserved_f0   = std::max(f0_length,  reserved_f0);
            reserved_bins = std::max(spec_bins,  reserved_bins);
            const size_t total = static_cast<size_t>(reserved_f0) * reserved_bins;

            // 1次元バッファの一括リサイズ
            flat_spec     .resize(total); 
            flat_ap       .resize(total);
            spec_tmp      .resize(reserved_bins);
            flat_spec_prev.resize(total); 
            flat_ap_prev  .resize(total);
            flat_mod_ap   .resize(total);

            // ポインタ配列（行ポインタ）の領域確保
            spec_ptrs     .resize(reserved_f0); 
            ap_ptrs       .resize(reserved_f0);
            spec_ptrs_prev.resize(reserved_f0); 
            ap_ptrs_prev  .resize(reserved_f0);
            mod_ap_ptrs   .resize(reserved_f0);
        }

        // 【バグの根本治療】
        // needs_resize の成否に関わらず、呼び出しごとにポインタを毎回再構築する。
        // これにより、reserved_bins (現在のメモリの物理ストライド) に基づく正しい先頭アドレスが
        // 常にすべての行（0 〜 f0_length-1）に保証される。
        for (int i = 0; i < f0_length; ++i) {
            const size_t off = static_cast<size_t>(i) * reserved_bins;
            spec_ptrs     [i] = &flat_spec     [off];
            ap_ptrs       [i] = &flat_ap       [off];
            spec_ptrs_prev[i] = &flat_spec_prev[off];
            ap_ptrs_prev  [i] = &flat_ap_prev  [off];
            mod_ap_ptrs   [i] = &flat_mod_ap   [off];
        }
    }

    /**
     * @brief 2次元配列風に安全にアクセスするためのユーティリティ（デバッグ・安全用）
     * 外部で `spec_ptrs[i][j]` と書く代わりに `at_spec(i, j)` を使うことで、
     * 万が一ストライドが狂っても数値を破壊させない防壁となる。
     */
    inline double& at_spec(int frame, int bin) {
        return flat_spec[static_cast<size_t>(frame) * reserved_bins + bin];
    }

    void ensure_f0(int n) {
        if (n > static_cast<int>(f0.size())) {
            f0.resize(n); 
            time_axis.resize(n);
        }
    }

    void ensure_f0_prev(int n) {
        if (n > static_cast<int>(f0_prev.size())) {
            f0_prev.resize(n); 
            time_axis_prev.resize(n);
        }
    }
};

// スレッドローカルなインスタンス宣言
thread_local SynthesisScratchPad tl_scratch;

// ============================================================
// 定数
// ============================================================

static constexpr int    kFs               = 44100;
static constexpr double kFramePeriod      = 5.0;   // ms
static constexpr double kInv32768         = 1.0 / 32768.0;
static constexpr int    kCrossfadeSamples = static_cast<int>(kFs * 0.030);
static constexpr int    kMaxPitchLength   = 120000;
static constexpr int    kTransitionFrames = static_cast<int>(60.0 / kFramePeriod);

static int64_t note_samples_safe(int p) {
    return (static_cast<int64_t>(p) - 1) * kFramePeriod / 1000.0 * kFs + 1;
}

// ============================================================
// parse_pitch_tag_hz
//
// UTAU系音源のファイル名/エイリアスにはしばしば "あー_D4" "い_F#4_2" のように
// 録音時のピッチタグが付与されている。これはCheapTrick/Harvestの解析結果
// (フレーム平均)よりもはるかに信頼できる「そのサンプルの本来のピッチ」情報
// なので、apply_gender_shift の基準F0(base_f0)にはこちらを優先して使う。
// 見つからなければ 0.0 を返し、呼び出し側は従来の解析平均にフォールバックする。
// ============================================================
static double parse_pitch_tag_hz(const std::string& path)
{
    // ファイル名部分だけを見る（ディレクトリ名にたまたま数字が含まれる誤検出を避ける）
    std::string name = path;
    const size_t slash = name.find_last_of("/\\");
    if (slash != std::string::npos) name = name.substr(slash + 1);

    // 例: "あー_D4", "い_F#4_2", "do-dai_C#5.wav" のような末尾寄りのパターンを探す。
    // [A-Ga-g][#b]?[0-9] という並びを後ろから探し、直後に数字が続く場合は
    // それも含めて「オクターブ番号」として解釈する（例: "D10" のような2桁対応）。
    for (size_t i = 0; i + 1 < name.size(); ++i) {
        char c = name[i];
        char upper = static_cast<char>(std::toupper(static_cast<unsigned char>(c)));
        if (upper < 'A' || upper > 'G') continue;

        size_t j = i + 1;
        int accidental = 0; // 0=natural, 1=sharp, -1=flat
        if (j < name.size() && (name[j] == '#')) { accidental = 1; ++j; }
        else if (j < name.size() && (name[j] == 'b')) { accidental = -1; ++j; }

        if (j >= name.size() || !std::isdigit(static_cast<unsigned char>(name[j]))) continue;

        size_t k = j;
        while (k < name.size() && std::isdigit(static_cast<unsigned char>(name[k]))) ++k;
        int octave = std::atoi(name.substr(j, k - j).c_str());

        static const int kSemitoneFromC[7] = {9, 11, 0, 2, 4, 5, 7}; // A,B,C,D,E,F,G
        const int letterIdx = upper - 'A';
        const int semitone = kSemitoneFromC[letterIdx] + accidental;
        const int midi = (octave + 1) * 12 + semitone; // MIDI: C4=60 (オクターブ表記はC4基準)

        if (midi < 0 || midi > 127) continue; // 明らかに誤検出（無関係な英数字列）なら無視

        return 440.0 * std::pow(2.0, (midi - 69) / 12.0);
    }
    return 0.0;
}

// ============================================================
// find_voice_ref
// ============================================================

std::shared_ptr<const EmbeddedVoice> find_voice_ref(const char* key)
{
    {
        auto cached = g_voice_db.get(key ? key : "");
        if (cached) return cached;
    }
    
    // Fallback: load from disk (MEMFS)
    if (key) {
        int audio_len = GetAudioLength(key);
        if (audio_len > 0) {
            auto ev = std::make_shared<EmbeddedVoice>();
            ev->path = key;
            int nbit = 0;
            ev->waveform.resize(audio_len);
            wavread(key, &ev->fs, &nbit, ev->waveform.data());
            
            g_voice_db.put(key, ev);
            return ev;
        }
    }
    return nullptr;
}

// ============================================================
// ディスクキャッシュ
// ============================================================

static std::string get_cache_dir() {
    std::string p = "cache";
    struct stat st;
    if (stat(p.c_str(), &st) != 0) {
        mkdir(p.c_str(), 0755);
    }
    return p;
}

static void save_cache(const std::string& cache_path, const AnalysisCache& cache)
{
    // 書き途中でクラッシュしても破損キャッシュが残らないよう
    // 一時ファイルに書いてからアトミックにリネームする
    std::string tmp_path = cache_path + ".tmp";

    FILE* fp = fopen(tmp_path.c_str(), "wb");
    if (!fp) {
        fprintf(stderr, "[Cache] Failed to open temp cache file for writing: %s (errno=%d)\n",
                tmp_path.c_str(), errno);
        return;
    }

    bool ok = true;
    VoseCacheHeader header;
    header.magic     = 0x45534F56;
    header.length    = cache.length;
    header.spec_bins = cache.spec_bins;

    ok &= (fwrite(&header,             sizeof(header),  1,            fp) == 1);
    ok &= (fwrite(cache.f0.data(),     sizeof(double),  cache.length, fp) == static_cast<size_t>(cache.length));
    ok &= (fwrite(cache.time.data(),   sizeof(double),  cache.length, fp) == static_cast<size_t>(cache.length));
    const size_t sc = static_cast<size_t>(cache.length) * cache.spec_bins;
    ok &= (fwrite(cache.flat_spec.data(), sizeof(double), sc, fp) == sc);
    ok &= (fwrite(cache.flat_ap.data(),   sizeof(double), sc, fp) == sc);
    fclose(fp);

    if (ok) {
        // [修正] 以前は std::error_code を宣言していたが、ここで使っている
        // rename() は C標準ライブラリ版（<cstdio>）であり std::error_code は
        // 一切渡されておらず死んだ変数だった。加えて rename() の戻り値も
        // 確認しておらず、失敗（例: 権限不足やディスクフル）しても気づかず
        // 中途半端な .tmp ファイルが残り続けていた。
        // ここで戻り値を確認し、失敗時はログを出した上で .tmp を掃除する。
        if (std::rename(tmp_path.c_str(), cache_path.c_str()) != 0) {
            fprintf(stderr, "[Cache] Failed to rename %s -> %s (errno=%d)\n",
                    tmp_path.c_str(), cache_path.c_str(), errno);
            unlink(tmp_path.c_str());
        }
    } else {
        fprintf(stderr, "[Cache] Failed to write cache data to %s\n", tmp_path.c_str());
        unlink(tmp_path.c_str());  // 書き込み失敗なら一時ファイルを削除
    }
}

static std::shared_ptr<AnalysisCache> load_cache(const std::string& path,
                                                 int expected_spec_bins = 0)
{
    struct stat st;
    if (stat(path.c_str(), &st) != 0) return nullptr;

    std::ifstream ifs(path, std::ios::binary);
    if (!ifs) return nullptr;

    VoseCacheHeader header{};
    if (!ifs.read(reinterpret_cast<char*>(&header), sizeof(header))) return nullptr;

    // マジック検証
    if (header.magic != 0x45534F56) return nullptr;

    // サニティチェック: 長さ・spec_bins が異常値ならキャッシュ破棄（OOM防止）
    if (header.length <= 0 || header.length > 1'000'000) return nullptr;
    if (header.spec_bins <= 0 || header.spec_bins > 65536) return nullptr;

    // spec_bins 互換チェック:
    // fft_size が変わると spec_bins が変わる。異なるサイズのキャッシュを
    // 読み込むと配列の境界外アクセスが起きるため、不一致なら再解析させる。
    if (expected_spec_bins > 0 && header.spec_bins != expected_spec_bins) {
        return nullptr;
    }

    auto cache = std::make_shared<AnalysisCache>();
    cache->length    = header.length;
    cache->spec_bins = header.spec_bins;
    
    // メモリ確保
    cache->f0  .resize(cache->length);
    cache->time.resize(cache->length);
    const size_t sc = static_cast<size_t>(cache->length) * cache->spec_bins;
    cache->flat_spec.resize(sc);
    cache->flat_ap  .resize(sc);

    // 各 read の成否を検証するラムダ
    auto read_check = [&](void* dst, size_t bytes) -> bool {
        return static_cast<bool>(
            ifs.read(reinterpret_cast<char*>(dst), static_cast<std::streamsize>(bytes)));
    };

    // 【最適化】sizeof(double) をコンテナの要素型（sizeof(cache->...[0])）に書き換え、
    // 将来的に float 等に型変更してもバグが出ないよう安全性を担保。
    if (!read_check(cache->f0.data(),        sizeof(cache->f0[0]) * cache->length))  return nullptr;
    if (!read_check(cache->time.data(),      sizeof(cache->time[0]) * cache->length)) return nullptr;
    if (!read_check(cache->flat_spec.data(), sizeof(cache->flat_spec[0]) * sc))      return nullptr;
    if (!read_check(cache->flat_ap.data(),   sizeof(cache->flat_ap[0]) * sc))        return nullptr;

    // ストリームが正確に末尾に達しているか確認（余剰バイトがある = ファイル破損とみなす）
    if (ifs.peek() != std::ifstream::traits_type::eof()) return nullptr;

    return cache;
}
// ============================================================
// build_analysis_cache
// ============================================================

static std::shared_ptr<const AnalysisCache>
build_analysis_cache(const EmbeddedVoice& ev, int fft_size, int spec_bins)
{
    auto cache = std::make_shared<AnalysisCache>();
    cache->spec_bins = spec_bins;

    HarvestOption opt;
    InitializeHarvestOption(&opt);
    opt.frame_period = kFramePeriod;
    opt.f0_floor     = 50.0;
    opt.f0_ceil      = 800.0;

    const int wav_len     = static_cast<int>(ev.waveform.size());
    const int harvest_len = GetSamplesForHarvest(ev.fs, wav_len, kFramePeriod);
    cache->f0.resize(harvest_len);
    cache->time.resize(harvest_len);
    cache->length = harvest_len;

    std::vector<double> raw_f0(harvest_len, 0.0);
    Harvest(ev.waveform.data(), wav_len, ev.fs, &opt,
            cache->time.data(), raw_f0.data());

    // StoneMask による瞬時周波数ベースの高精度 F0 精緻化
    StoneMask(ev.waveform.data(), wav_len, ev.fs,
              cache->time.data(), raw_f0.data(), harvest_len, cache->f0.data());

    // 孤立した1フレームの一時的なピッチドロップアウト（F0==0）を救済
    for (int i = 1; i + 1 < harvest_len; ++i) {
        if (cache->f0[i] == 0.0 && cache->f0[i - 1] > 0.0 && cache->f0[i + 1] > 0.0) {
            cache->f0[i] = 0.5 * (cache->f0[i - 1] + cache->f0[i + 1]);
        }
    }

    // CheapTrick と D4C は全区間で連続した F0 を必要とする。
    // 無声区間を有声区間のピッチで滑らかに補間・外挿した解析専用 F0 を生成することで、
    // スペクトル包絡の崩れや非周期性（ホワイトノイズ比率）の過大推定を防止する。
    std::vector<double> continuous_f0 = cache->f0;
    {
        std::vector<int> vi;
        vi.reserve(harvest_len);
        for (int i = 0; i < harvest_len; ++i) {
            if (continuous_f0[i] > 50.0 && continuous_f0[i] < 800.0) {
                vi.push_back(i);
            }
        }
        if (!vi.empty()) {
            for (int i = 0; i < vi.front(); ++i)
                continuous_f0[i] = continuous_f0[vi.front()];
            for (int i = vi.back() + 1; i < harvest_len; ++i)
                continuous_f0[i] = continuous_f0[vi.back()];
            for (size_t v = 0; v + 1 < vi.size(); ++v) {
                const int ia = vi[v], ib = vi[v + 1];
                if (ib - ia <= 1) continue;
                const double fa = continuous_f0[ia], fb = continuous_f0[ib];
                for (int i = ia + 1; i < ib; ++i) {
                    const double t = static_cast<double>(i - ia) / (ib - ia);
                    continuous_f0[i] = fa + t * (fb - fa);
                }
            }
        } else {
            std::fill(continuous_f0.begin(), continuous_f0.end(), 220.0);
        }
    }

    const size_t sc = static_cast<size_t>(harvest_len) * spec_bins;
    cache->flat_spec.resize(sc);
    cache->flat_ap  .resize(sc);

    std::vector<double*> sp(harvest_len), ap(harvest_len);
    for (int i = 0; i < harvest_len; ++i) {
        sp[i] = &cache->flat_spec[static_cast<size_t>(i)*spec_bins];
        ap[i] = &cache->flat_ap  [static_cast<size_t>(i)*spec_bins];
    }
    CheapTrickOption ct_opt;
    InitializeCheapTrickOption(ev.fs, &ct_opt);
    ct_opt.fft_size = fft_size;

    D4COption d4c_opt;
    InitializeD4COption(&d4c_opt);

    CheapTrick(ev.waveform.data(), wav_len, ev.fs,
               cache->time.data(), continuous_f0.data(), harvest_len, &ct_opt, sp.data());
    D4C(ev.waveform.data(), wav_len, ev.fs,
        cache->time.data(), continuous_f0.data(), harvest_len, fft_size, &d4c_opt, ap.data());

    // 平均ピッチおよびフォルマント計算の安定化のため、連続化されたF0を保持
    cache->f0 = std::move(continuous_f0);

    return cache;
}

// ============================================================
// get_or_analyze
// ============================================================

std::shared_ptr<const AnalysisCache>
get_or_analyze(std::shared_ptr<const EmbeddedVoice> ev_sp, int fft_size, int spec_bins)
{
    if (!ev_sp) return nullptr;
    const std::string& key = ev_sp->path;

    // 1. メモリキャッシュをチェック（ロック不要の高速パス。CacheStore内部でロック）
    {
        auto cached = g_analysis_cache.get(key);
        if (cached) return cached;
    }

    // 2. [修正] ここから先（ディスク読み込み〜新規解析〜保存）はキーごとに
    //    直列化する。以前はここが完全にロックフリーで、同一音源を使う
    //    複数ノートが並列ワーカーに割り当たると Harvest/CheapTrick/D4C の
    //    重複実行や、save_cache() の一時ファイル書き込み競合
    //    （ファイル破損の可能性）が起こり得た。per-keyロックで
    //    「他キーの処理は並列のまま、同一キーだけ直列化」する。
    auto key_lock = g_analysis_lock_registry.get_lock(key);
    std::lock_guard<std::mutex> lg(*key_lock);

    // ロック取得を待っている間に他スレッドが解析を完了させている
    // 可能性があるため、ロック取得後にもう一度メモリキャッシュを確認する。
    {
        auto cached = g_analysis_cache.get(key);
        if (cached) return cached;
    }

    // 3. ディスクキャッシュ読み込み（キーロック内、ネイティブ環境のみ）
#if !defined(__EMSCRIPTEN__)
    const std::string cache_file = get_cache_dir() + "/" + generate_cache_hash(key) + ".vsc";
    auto disk_cache = load_cache(cache_file, spec_bins);
    if (disk_cache) {
        g_analysis_cache.put(key, disk_cache);
        return disk_cache;
    }
#endif

    // 4. 新規解析（キーロック内。ただし他キーの解析はブロックしない）
    auto cache = build_analysis_cache(*ev_sp, fft_size, spec_bins);

    // 5. メモリキャッシュに書き込み
    g_analysis_cache.put(key, cache);
#if !defined(__EMSCRIPTEN__)
    save_cache(cache_file, *cache);  // ディスク保存（同一キーでは直列化済み）
#endif
    return cache;
}
// ============================================================
// UTAUタイムマッピング
// ============================================================

double get_source_ms(const EmbeddedVoice& ev) {
    return static_cast<double>(ev.waveform.size()) / ev.fs * 1000.0;
}

double map_time(double t_out_ms, const OtoEntry& oto,
                double source_wav_len_ms, double note_duration_ms)
{
    const double offset = std::max(0.0, oto.offset);
    double fixed        = std::max(0.0, oto.consonant);

    // 短いノート(例: 16分音符や速いテンポ)で fixed >= note_duration_ms になると
    // 母音まで到達できず子音だけで終わってしまう。
    // その場合は固定子音区間を短縮し、ノート後半の少なくとも50%は母音が鳴るようにする。
    if (note_duration_ms > 0.0 && fixed >= note_duration_ms) {
        fixed = std::max(5.0, note_duration_ms * 0.45);
    }

    double cutoff_pos;
    if (oto.cutoff < 0) {
        cutoff_pos = offset - oto.cutoff;
    } else if (oto.cutoff > 0) {
        cutoff_pos = source_wav_len_ms - oto.cutoff;
    } else {
        cutoff_pos = source_wav_len_ms;
    }
    cutoff_pos = std::min(cutoff_pos, source_wav_len_ms);
    if (cutoff_pos <= offset + fixed) {
        cutoff_pos = std::min(source_wav_len_ms, offset + fixed + 50.0);
    }

    const double source_stretch = std::max(0.0, cutoff_pos - (offset + fixed));
    const double output_stretch = std::max(1.0, note_duration_ms - fixed);

    double mapped_ms;
    if (t_out_ms < fixed) {
        mapped_ms = offset + t_out_ms;
    } else {
        const double ratio = (source_stretch > 0.0) ? (source_stretch / output_stretch) : 1.0;
        mapped_ms = (offset + fixed) + (t_out_ms - fixed) * ratio;
    }

    return clamp(mapped_ms, 0.0, std::max(0.0, source_wav_len_ms - 1.0));
}

// ============================================================
// copy_cache_to_scratch
// ============================================================

static void copy_cache_to_scratch_cur(const AnalysisCache& c)
{
    tl_scratch.ensure_spec(c.length, c.spec_bins);
    const size_t total = static_cast<size_t>(c.length) * c.spec_bins;
    std::copy(c.flat_spec.begin(), c.flat_spec.begin()+total, tl_scratch.flat_spec.begin());
    std::copy(c.flat_ap  .begin(), c.flat_ap  .begin()+total, tl_scratch.flat_ap  .begin());
    tl_scratch.ensure_f0(c.length);
    std::copy(c.f0  .begin(), c.f0  .begin()+c.length, tl_scratch.f0       .begin());
    std::copy(c.time.begin(), c.time.begin()+c.length, tl_scratch.time_axis.begin());
}

static void copy_cache_to_scratch_prev(const AnalysisCache& c)
{
    tl_scratch.ensure_spec(c.length, c.spec_bins);
    const size_t total = static_cast<size_t>(c.length) * c.spec_bins;
    std::copy(c.flat_spec.begin(), c.flat_spec.begin()+total, tl_scratch.flat_spec_prev.begin());
    std::copy(c.flat_ap  .begin(), c.flat_ap  .begin()+total, tl_scratch.flat_ap_prev  .begin());
    tl_scratch.ensure_f0_prev(c.length);
    std::copy(c.f0  .begin(), c.f0  .begin()+c.length, tl_scratch.f0_prev       .begin());
    std::copy(c.time.begin(), c.time.begin()+c.length, tl_scratch.time_axis_prev.begin());
}

// ============================================================
// resample_curve
// ============================================================

inline double resample_curve(const double* curve, int src_len,
                                     int dst_idx, int dst_len)
{
    if (!curve || src_len <= 0 || dst_len <= 0) return 0.0;
    if (dst_idx < 0) return curve[0];
    if (src_len == 1) return curve[0];
    const double t     = static_cast<double>(dst_idx) / std::max(dst_len-1, 1);
    const double src_f = t * (src_len-1);
    const int    j0    = static_cast<int>(src_f);
    const int    j1    = std::min(j0+1, src_len-1);
    return (1.0-(src_f-j0))*curve[j0] + (src_f-j0)*curve[j1];
}

// ============================================================
// apply_crossfade
//
// dst[offset..] に src を書き込む。先頭 xfade_len サンプルは
// dst と src を raised-cosine (0.5 * (1 - cos(pi*t))) でブレンドする。
// 子音の頭を絶対に削らず、src[0]から忠実に書き込む。
// ============================================================
static void apply_crossfade(std::vector<double>& dst, int64_t dst_size,
                             const std::vector<double>& src, int64_t src_size,
                             int64_t offset, int xfade_len)
{
    if (offset < 0 || offset >= dst_size || src.empty() || src_size <= 0) return;

    const int64_t write_len = std::min(src_size, dst_size - offset);
    if (write_len <= 0) return;

    const int safe_xfade = static_cast<int>(
        std::min<int64_t>(std::max(0, xfade_len), write_len));

    for (int s = 0; s < safe_xfade; ++s) {
        const double  t       = (safe_xfade > 1) ? (static_cast<double>(s) / safe_xfade) : 1.0;
        const double  fade_in = 0.5 * (1.0 - std::cos(M_PI * t));
        const int64_t di      = offset + s;
        dst[di] = dst[di] * (1.0 - fade_in) + src[s] * fade_in;
    }

    for (int64_t s = safe_xfade; s < write_len; ++s) {
        dst[offset + s] = src[s];
    }
}

// ============================================================
// apply_gender_shift  （フォルマント追従付き高音域補正）
//
// gender  ∈ [0.0, 1.0]  0.5=変更なし / <0.5=太い声 / >0.5=細い声
// f0_ratio: 現在フレームのF0 / 音源基準F0
//   高音域ほど > 1.0 → スペクトル包絡を引き伸ばしてフォルマントを追従させる
//   UTAUの標準resamplerは高音でスペクトルをそのまま使うため「こもる」。
//   ここでF0比に応じて引き伸ばすことで自然な声質を維持する。
// ============================================================

void apply_gender_shift(double* sr, int spec_bins, double gender,
                        double* tmp, double f0_ratio)
{
    if (!sr || !tmp || spec_bins <= 0) return;

    // gender が 0.5 (デフォルト) の場合は、原音の自然なフォルマントを 100% 活かすため
    // 不要なスペクトル変形を行わない。
    if (std::abs(gender - 0.5) < 1e-3) return;

    // gender ∈ [0.0, 1.0] (0.5=無変更, <0.5=太い声, >0.5=細い声)
    const double shift_ratio = std::exp((gender - 0.5) * 0.4 * std::log(2.0));
    if (std::abs(shift_ratio - 1.0) < 1e-4) return;

    constexpr double kFloor = 1e-12;
    for (int k = 0; k < spec_bins; ++k)
        tmp[k] = std::log(std::max(sr[k], kFloor));

    for (int k = 0; k < spec_bins; ++k) {
        const double src_k = static_cast<double>(k) / shift_ratio;
        const int    k0    = static_cast<int>(src_k);
        if (k0 >= spec_bins - 1) {
            // 最上位ビン外は自然に減衰させて高周波ノイズを防ぐ
            const double decay = std::exp(-0.05 * (src_k - (spec_bins - 1)));
            sr[k] = std::exp(tmp[spec_bins - 1]) * decay;
        } else {
            const double frac = src_k - k0;
            sr[k] = std::exp((1.0 - frac) * tmp[k0] + frac * tmp[k0 + 1]);
        }
    }
}

// ============================================================
// apply_tension_breath
// ============================================================

void apply_tension_breath(double* sr, double* ar, int spec_bins,
                                  double tension, double breath)
{
    if (!sr || !ar || spec_bins <= 1) return;
    const double inv = 1.0 / (spec_bins-1);
    for (int k = 0; k < spec_bins; ++k) {
        const double fw = static_cast<double>(k) * inv;
        if (std::abs(tension-0.5) > 1e-4) {
            const double weight     = 1.0/(1.0+std::exp(-8.0*(fw-0.35)));
            const double gain_db    = (tension-0.5)*12.0*weight;
            const double clipped_db = 6.0*std::tanh(gain_db/6.0);
            sr[k] *= std::pow(10.0, clipped_db/20.0);
        }
        if (std::abs(breath-0.5) > 1e-4) {
            const double bw     = std::pow(fw, 0.7);
            const double amount = (breath-0.5)*bw;
            ar[k] = amount >= 0.0
                ? ar[k] + amount*(1.0-ar[k])
                : ar[k] + amount*ar[k];
            ar[k] = clamp(ar[k], 0.0, 1.0);
        }
    }
}

// ============================================================
// blend_transition_spectra
// ============================================================

void blend_transition_spectra(
    double** spec_cur, double** ap_cur, int cur_len,
    double** spec_prev, double** ap_prev, int prev_len,
    int spec_bins, int transition_frames)
{
    if (!spec_cur || !spec_prev || !ap_cur || !ap_prev) return;
    if (spec_bins <= 0 || cur_len <= 0 || prev_len <= 0) return;

    // ★修正: 以前は transition_frames(固定60ms=12フレーム)を
    // min(cur_len, prev_len) でしかクランプしておらず、短いノート
    // （速いテンポの16分音符など、88ms=17〜18フレーム程度）では
    // ノート全体の60%以上が前のノートのスペクトル包絡で塗りつぶされて
    // いた。子音の立ち上がり（摩擦音の高域ノイズ等、その音素を特徴づける
    // 最も重要な部分）がここで失われ、全く別の音素に聞こえる原因になって
    // いた（例:「し」が「み」に聞こえる）。
    // ここでノート自身の長さの35%を上限にも加え、短いノートほど
    // ブレンド幅自体を狭めることで、ノート本来の音色を守る。
    const int max_by_note_ratio = std::max(1, static_cast<int>(cur_len * 0.35));
    const int blend = std::min({transition_frames, cur_len, prev_len, max_by_note_ratio});

    for (int j = 0; j < blend; ++j) {
        const double t      = static_cast<double>(j) / blend;
        const double w_prev = 0.5*(1.0-std::cos(M_PI*(1.0-t)));
        const double w_cur  = 1.0 - w_prev;
        const int    prev_j = prev_len - blend + j;
        constexpr double kFloor = 1e-12;
        double* sc = spec_cur [j];
        double* sp = spec_prev[std::max(0, prev_j)];
        double* ac = ap_cur   [j];
        double* ap = ap_prev  [std::max(0, prev_j)];
        for (int k = 0; k < spec_bins; ++k) {
            sc[k] = std::exp(w_cur *std::log(std::max(sc[k],kFloor))
                           + w_prev*std::log(std::max(sp[k],kFloor)));
            ac[k] = clamp(w_cur*ac[k] + w_prev*ap[k], 0.0, 1.0);
        }
    }
}

// ============================================================
// apply_vibrato
//
// ノート後半50%からビブラートを自然に立ち上げる。
// global_time_offset_sec: 曲先頭からの絶対時間 → ノートをまたいで位相連続
// depth_curve/rate_curve: ノートごとの制御カーブ（nullptr = デフォルト）
// ============================================================
void apply_vibrato(double* f0, int f0_length, double frame_period_ms,
                   double global_time_offset_sec,
                   const double* depth_curve,
                   const double* rate_curve,
                   int curve_length)
{
    if (!f0 || f0_length <= 0) return;
    // ユーザーが明示的にビブラートを指定していない場合は、ピッチを忠実に保つ
    if (!depth_curve || curve_length <= 0) return;

    const int vib_start = f0_length / 2;
    const int vib_len   = f0_length - vib_start;
    if (vib_len <= 0) return;

    constexpr double kVibDepthMax = 0.00868;  // 15cent
    constexpr double kVibFreqDef  = 6.0;
    const double     frame_sec    = frame_period_ms / 1000.0;

    for (int j = vib_start; j < f0_length; ++j) {
        const double fade_progress =
            static_cast<double>(j - vib_start) / std::max(vib_len - 1, 1);
        const double eased = std::min(fade_progress * 2.2, 1.0);
        const double fade_in = eased * eased * (3.0 - 2.0 * eased);

        if (f0[j] <= 0.0) continue;

        const double depth = depth_curve
            ? resample_curve(depth_curve, curve_length, j, f0_length)
            : 0.0;
        if (depth <= 0.0) continue;

        const double rate  = rate_curve
            ? std::max(1.0, resample_curve(rate_curve, curve_length, j, f0_length))
            : kVibFreqDef;

        const double t_global = global_time_offset_sec + static_cast<double>(j) * frame_sec;
        const double vib = std::sin(2.0 * M_PI * rate * t_global)
                           * kVibDepthMax * depth * f0[j] * fade_in;
        f0[j] = std::max(50.0, f0[j] + vib);
    }
}

// ============================================================
// [NEW ③] smooth_f0_gaussian
//
// F0配列の有声区間(f0 > 0.0)に対してガウシアンカーネルで平滑化する。
// 無声区間(0.0)はそのまま保持し、有声区間の立ち上がりでピッチが沈み込むのを防ぐ。
// ============================================================

void smooth_f0_gaussian(double* f0, int f0_length)
{
    if (!f0 || f0_length <= 0) return;

    static constexpr double kKernel[5] = {
        0.06136, 0.24477, 0.38774, 0.24477, 0.06136
    };
    static constexpr int kRadius = 2; // カーネル半径

    std::vector<double> tmp(f0, f0 + f0_length);
    for (int i = 0; i < f0_length; ++i) {
        if (f0[i] <= 0.0) continue; // 無声区間は0.0のまま維持

        double sum = 0.0;
        double weight_sum = 0.0;
        for (int k = -kRadius; k <= kRadius; ++k) {
            int idx = i + k;
            if (idx < 0) idx = 0;
            if (idx >= f0_length) idx = f0_length - 1;
            if (f0[idx] > 0.0) {
                const double w = kKernel[k + kRadius];
                sum += f0[idx] * w;
                weight_sum += w;
            }
        }
        if (weight_sum > 0.0) {
            tmp[i] = sum / weight_sum;
        }
    }
    std::copy(tmp.begin(), tmp.end(), f0);
}

// ピッチや振幅の濁りを防ぐため、人工的なゆらぎは適用しない
static void apply_f0_jitter(
    double* /*f0*/, int /*f0_length*/, double /*frame_period_ms*/,
    double /*global_time_offset_sec*/, uint32_t /*voice_seed*/)
{
}

static void apply_shimmer(
    std::vector<double>& /*note_buf*/, int /*fs*/,
    double /*global_time_offset_sec*/, uint32_t /*voice_seed*/)
{
}

static void VOSE_Synthesis(
    const double* f0, int f0_length,
    double** spectrogram, double** aperiodicity,
    int fft_size, double frame_period, int fs,
    int y_length, double* y)
{
    // WORLD 公式のピュア合成を実行 (ランダムノイズの混入や不自然なHPFは全廃)
    Synthesis(f0, f0_length, spectrogram, aperiodicity,
              fft_size, frame_period, fs, y_length, y);
}

// ============================================================
// apply_post_eq
//
// WORLD合成出力に対する Biquad IIR ポストEQフィルタ。
//
// 補正対象:
//   80Hz   -1.5dB  低域の不要な直流・超低周波ノイズをカット
//   380Hz  -2.0dB  箱鳴り感・こもり感を解消しヌケを向上
//   3kHz   -2.5dB  金属的・機械的な耳につくピークを緩和
//   6kHz   +1.5dB  ボーカルの輪郭と発音の明瞭度を補強
//
// ※ 9kHz / 14kHz のハイシェルフブーストは、息漏れや微小ノイズを
//    過剰に増幅し「謎の吐息音」を引き起こす原因となっていたため撤廃。
// ============================================================

static const double kPostEQ[4][5] = {
    //  b0               b1               b2               a1               a2
    {  0.9991702401, -1.9799444128,  0.9808921526, -1.9799332931,  0.9800735124 }, // 80Hz  -1.5dB low shelf (sub-bass mud reduction)
    {  0.9959199627, -1.9574523909,  0.9644048069, -1.9574523909,  0.9603247695 }, // 380Hz -2.0dB peaking (boxiness reduction)
    {  0.9885000000, -1.6255368854,  0.8000000000, -1.6255368854,  0.7885000000 }, // 3kHz  -1.0dB gentle peaking (natural vocal core)
    {  1.0000000000,  0.0000000000,  0.0000000000,  0.0000000000,  0.0000000000 }, // 6kHz  Bypassed / Flat (prevents boosting breath noise)
};

static void apply_post_eq(double* y, int y_length)
{
    if (!y || y_length <= 0) return;

    for (int b = 0; b < 4; ++b) {
        const double b0 = kPostEQ[b][0];
        const double b1 = kPostEQ[b][1];
        const double b2 = kPostEQ[b][2];
        const double a1 = kPostEQ[b][3];
        const double a2 = kPostEQ[b][4];

        double s1 = 0.0;
        double s2 = 0.0;
        for (int i = 0; i < y_length; ++i) {
            const double in  = y[i];
            const double out = b0 * in + s1;
            s1 = b1 * in - a1 * out + s2;
            s2 = b2 * in - a2 * out;
            y[i] = out;
        }
    }
}

// execute_render の並列合成ラムダを自由関数に昇格。
// vose_streaming.cpp の synth_loop() からも呼べる。
// ============================================================

struct SynthNoteParams {
    const NotePrepass& pp;
    NoteEvent&         n;
    int                fft_size;
    int                spec_bins;
    double             global_time_sec = 0.0;  // 曲先頭からのオフセット（ビブラート位相連続化）
};

static const OtoEntry kDefaultOto = {};

static bool is_unvoiced_phoneme_name(const std::string& str)
{
    static const char* kUnvoiced[] = {
        "か", "き", "く", "け", "こ",
        "カ", "キ", "ク", "ケ", "コ",
        "さ", "し", "す", "せ", "そ",
        "サ", "シ", "ス", "セ", "ソ",
        "た", "ち", "つ", "て", "と",
        "タ", "チ", "ツ", "テ", "ト",
        "は", "ひ", "ふ", "へ", "ほ",
        "ハ", "ヒ", "フ", "ヘ", "ホ",
        "ぱ", "ぴ", "ぷ", "ぺ", "ぽ",
        "パ", "ピ", "プ", "ペ", "ポ",
        "ka", "ki", "ku", "ke", "ko",
        "sa", "si", "su", "se", "so", "shi",
        "ta", "ti", "tu", "te", "to", "chi", "tsu",
        "ha", "hi", "hu", "he", "ho", "fu",
        "pa", "pi", "pu", "pe", "po"
    };
    for (const char* u : kUnvoiced) {
        if (str.find(u) != std::string::npos) return true;
    }
    return false;
}

// ★追加: max_ap の帯域境界 (2200/4500/8000Hz) が階段状(ステップ関数)になっていると、
// その周波数ビンで ar[k] が強制的に折れ曲がり、非周期成分スペクトルに急激な段差が
// 生じる。この段差付きスペクトルを逆FFTすると各ピッチパルスの合間に高周波のリンギング
// ("トゲトゲしたノイズ")が発生する。境界の前後 kTransHz 幅を smoothstep で
// クロスフェードし、段差を無くすことでリンギングを解消する。
static double smooth_band_value(double freq, const double* bfreqs, const double* bvals, int n_bounds)
{
    constexpr double kTransHz = 400.0;
    auto smoothstep = [](double x) {
        x = clamp(x, 0.0, 1.0);
        return x * x * (3.0 - 2.0 * x);
    };
    for (int i = 0; i < n_bounds; ++i) {
        const double b = bfreqs[i];
        if (freq < b - kTransHz) return bvals[i];
        if (freq < b + kTransHz) {
            const double t = smoothstep((freq - (b - kTransHz)) / (2.0 * kTransHz));
            return bvals[i] + (bvals[i + 1] - bvals[i]) * t;
        }
    }
    return bvals[n_bounds];
}

void synthesize_note_impl(const SynthNoteParams& p, std::vector<double>& note_buf)
{
    const NotePrepass& pp    = p.pp;
    NoteEvent&         n     = p.n;
    const int   fft_size     = p.fft_size;
    const int   spec_bins    = p.spec_bins;

    if (pp.state != NoteState::RENDERABLE) return;

    // [防御的チェック] ASanで確認された null-pointer-dereference 対策。
    // パス1(NotePrepass構築)の時点では pp.ev は非null・有効だったはずだが、
    // このノートの合成が実行される時点で null になっているケースが実際に
    // 観測された(原因はまだ特定できていないメモリ破壊)。根本原因が
    // 特定できるまでの間、このノート1つだけを例外として扱い(このノートは
    // 無音として諦める)、曲全体のレンダリングが巻き込まれて中断される事態
    // を防ぐ。
    if (!pp.ev) {
        char buf[256];
        snprintf(buf, sizeof(buf),
                 "pp.ev is null despite RENDERABLE state: wav_path=%s "
                 "note_samples=%lld pitch_length=%d",
                 n.wav_path ? n.wav_path : "(null)",
                 static_cast<long long>(pp.note_samples), n.pitch_length);
        throw std::runtime_error(buf);
    }

    const int64_t note_samples  = pp.note_samples;
    const int     output_frames = std::max(1, p.n.pitch_length);
    const double  note_ms       = static_cast<double>(output_frames) * kFramePeriod;
    const double  src_ms        = get_source_ms(*pp.ev);
    const OtoEntry& current_oto = pp.has_oto ? pp.oto : kDefaultOto;

    // [デバッグ] どの段階で例外が発生しているか特定するため、
    // VOSE_Synthesis より手前の各段階を個別に try/catch で囲む。
    decltype(get_or_analyze(pp.ev, fft_size, spec_bins)) cache_cur;
    try {
        cache_cur = get_or_analyze(pp.ev, fft_size, spec_bins);
    } catch (const std::exception& e) {
        char buf[256];
        snprintf(buf, sizeof(buf),
                 "get_or_analyze(cur) failed: fft_size=%d spec_bins=%d "
                 "src_ms=%.2f pitch_length=%d : %s",
                 fft_size, spec_bins, src_ms, n.pitch_length, e.what());
        throw std::runtime_error(buf);
    }

    // フォルマント追従用: 音源の基準F0を求める。
    // ★修正: 以前は解析(CheapTrick/Harvest)の有声フレーム単純平均のみを
    // 使っており、子音部や語尾の不安定なピッチも均等に混ざるため基準自体が
    // ブレやすかった。ファイル名に "あー_D4" のようなピッチタグが付いている
    // 場合はそちらの方がはるかに信頼できるので優先し、見つからない場合のみ
    // 従来の解析平均にフォールバックする。
    double base_f0 = parse_pitch_tag_hz(pp.ev->path);
    if (base_f0 <= 0.0) {
        int voiced = 0;
        for (int j = 0; j < cache_cur->length; ++j) {
            if (cache_cur->f0[j] > 50.0) { base_f0 += cache_cur->f0[j]; ++voiced; }
        }
        base_f0 = (voiced > 0) ? base_f0 / voiced : 220.0;
    }

    try {
        tl_scratch.ensure_f0(output_frames);
        tl_scratch.ensure_spec(output_frames, spec_bins);
    } catch (const std::exception& e) {
        char buf[256];
        snprintf(buf, sizeof(buf),
                 "ensure_f0/ensure_spec failed: output_frames=%d spec_bins=%d "
                 "note_samples=%lld note_ms=%.2f : %s",
                 output_frames, spec_bins, static_cast<long long>(note_samples),
                 note_ms, e.what());
        throw std::runtime_error(buf);
    }

    // apply_post_eq の高域シェルフ減衰量を決めるための、ノート全体の
    // 代表的なフォルマント補正量(f0_ratio平均)を集計する
    double f0_ratio_sum = 0.0;

    // ----------------------------------------------------------------
    // ステップ1: cur スペクトルを DSP 込みで書き込む
    // (blend_transition_spectra より先に実行する必要がある)
    // ----------------------------------------------------------------
    try {
    for (int j = 0; j < output_frames; ++j) {
        const double t_out_ms = j * kFramePeriod;
        const double t_src_ms = map_time(t_out_ms, current_oto, src_ms, note_ms);
        const int src_frame   = clamp(
            static_cast<int>(t_src_ms / kFramePeriod), 0, cache_cur->length - 1);

        double* sr = tl_scratch.spec_ptrs[j];
        double* ar = tl_scratch.ap_ptrs[j];
        std::copy_n(&cache_cur->flat_spec[static_cast<size_t>(src_frame) * spec_bins],
                    spec_bins, sr);
        std::copy_n(&cache_cur->flat_ap[static_cast<size_t>(src_frame) * spec_bins],
                    spec_bins, ar);

        // ---- 1. ベースF0を計算 ----
        double base_f0_val = n.pitch_curve
            ? resample_curve(n.pitch_curve, n.pitch_length, j, output_frames)
            : 440.0;

        // ---- 2. ★★★ ポルタメントオフセットを適用（セント → Hz） ★★★ ----
        if (n.portamento_offsets && n.portamento_length > 0 && j < n.portamento_length) {
            double cents = resample_curve(n.portamento_offsets, n.portamento_length, j, output_frames);
            base_f0_val *= std::pow(2.0, cents / 1200.0);
        }

        // 歌唱合成では、各ノートは常に指定のピッチ(base_f0_val)で発音する。
        // 原音の無声判定などでF0を0.0に落とすと、WORLDが「100%ホワイトノイズ」を励起し、
        // 歌唱の途中に突然「突発的な吐息音・息漏れバースト」が発生してしまう。
        tl_scratch.f0[j] = base_f0_val;

        // ---- 3. その他のパラメータ ----
        const double gender  = n.gender_curve
            ? resample_curve(n.gender_curve,  n.pitch_length, j, output_frames) : 0.5;
        const double tension = n.tension_curve
            ? resample_curve(n.tension_curve, n.pitch_length, j, output_frames) : 0.5;
        // デフォルト息パラメータは 0.0 (純粋な有声調波・息ノイズなし)
        // 0.5 だと意図しないヒスノイズが乗るため、明示的な指定がない限り息漏れは0とする
        double breath  = n.breath_curve
            ? resample_curve(n.breath_curve,  n.pitch_length, j, output_frames) : 0.0;
        // ★修正: breath は本来 0.0〜1.0 (0.5=無変化) の規約だが、上流 (UI/UST/レガシー
        // パス) が 0〜100 スケールのままのカーブを渡してくるケースがあり、その場合
        // breath_allowance が桁違いに膨張して下の max_ap クランプが事実上無効化され、
        // 全ノートで高域ノイズ(「吐息みたいなノイズ」)が漏れる原因になっていた。
        // どんな上流の値が来ても安全な範囲に丸める。
        breath = clamp(breath, 0.0, 1.0);

        // ---- 4. フォルマント追従とテンション・ブレス ----
        const double f0_ratio = (base_f0 > 0.0) ? base_f0_val / base_f0 : 1.0;
        f0_ratio_sum += f0_ratio;
        apply_gender_shift(sr, spec_bins, gender, tl_scratch.spec_tmp.data(), f0_ratio);
        apply_tension_breath(sr, ar, spec_bins, tension, breath);

        // ---- 5. 非周期性(ar)の最適クランプ（謎のノイズ混じり吐息を完全に除去） ----
        // ユーザーが明示的に息パラメータ (breath > 0.5) を上げた場合のみ意図的な息漏れを許容
        // ★修正: 係数を 1.2 → 0.3 に縮小。breath=1.0 でも max_ap の底上げを +0.15 までに
        // 抑え、意図的な息漏れ設定時でも高域が「サー」と鳴りすぎないようにする。
        const double breath_allowance = (breath > 0.5) ? (breath - 0.5) * 0.3 : 0.0;
        const bool has_unvoiced = is_unvoiced_phoneme_name(pp.ev->path);
        const double fixed_ms = std::max(0.0, current_oto.consonant);
        const double unvoiced_attack_ms = has_unvoiced ? std::min(40.0, fixed_ms) : 0.0;
        const bool in_consonant_friction = (t_out_ms < unvoiced_attack_ms);

        for (int k = 0; k < spec_bins; ++k) {
            const double freq = static_cast<double>(k) * pp.ev->fs / fft_size;
            double max_ap;
            if (in_consonant_friction) {
                // 無声子音アタック (k, s, t, h, p など): 高域にのみ子音の摩擦・破裂成分を許容
                static const double bfreqs[2] = {2200.0, 4500.0};
                static const double bvals[3]  = {0.01, 0.25, 0.50};
                max_ap = smooth_band_value(freq, bfreqs, bvals, 2);
            } else {
                // 母音区間および有声音 (あ, い, う, え, お, ん, ま, な, ら, わ 等):
                // 非周期性（ar）を適切に抑制し、背後に乗る不快な「サー」というホワイトノイズ・ヒスノイズを一掃。
                // 人間の純粋な歌声の調波構造を優先し、高域の必要最小限の自然な空気感(1%〜10%)のみに制限する。
                static const double bfreqs[3] = {3000.0, 6000.0, 10000.0};
                static const double bvals[4]  = {0.01, 0.03, 0.06, 0.10};
                max_ap = smooth_band_value(freq, bfreqs, bvals, 3);
            }
            max_ap = std::min(1.0, max_ap + breath_allowance);
            if (ar[k] > max_ap) {
                ar[k] = max_ap;
            }
        }
    }
    } catch (const std::exception& e) {
        char buf[256];
        snprintf(buf, sizeof(buf),
                 "step1 loop failed: output_frames=%d pitch_length=%d "
                 "cache_cur->length=%d spec_bins=%d : %s",
                 output_frames, n.pitch_length, cache_cur->length, spec_bins, e.what());
        throw std::runtime_error(buf);
    }
    // ----------------------------------------------------------------
    // ステップ2: UTAU では音素接続は時間軸上のクロスフェードで行うため、
    // 子音のアタックを壊すスペクトル空間でのブレンドは行わない。
    // ----------------------------------------------------------------

    smooth_f0_gaussian(tl_scratch.f0.data(), output_frames);

    // ビブラートカーブが NoteEvent にあれば使用、なければデフォルト (depth=1.0, rate=6Hz)
    // NoteEvent 側に vibrato_depth_curve / vibrato_rate_curve / vibrato_curve_length
    // フィールドを追加した場合はそのまま渡せる。未定義なら nullptr で問題ない。
    const double* vib_depth = (n.vibrato_depth_curve && n.vibrato_curve_length > 0)
                              ? n.vibrato_depth_curve : nullptr;
    const double* vib_rate  = (n.vibrato_rate_curve  && n.vibrato_curve_length > 0)
                              ? n.vibrato_rate_curve  : nullptr;
    const int     vib_clen  = n.vibrato_curve_length > 0 ? n.vibrato_curve_length : 0;

    apply_vibrato(tl_scratch.f0.data(), output_frames, kFramePeriod,
                  p.global_time_sec, vib_depth, vib_rate, vib_clen);

    // voice_seed: 音源キー(エイリアス文字列)のハッシュ。ノートごとに
    // ジッター/シマーの位相をずらし、複数ノートが完全に同期して
    // 揺れる不自然さを避ける。
    const uint32_t voice_seed = static_cast<uint32_t>(
        std::hash<std::string>{}(pp.ev->path));

    // ★F0ジッターは必ず VOSE_Synthesis の前に適用する（後からf0配列を
    // いじっても、既に合成済みの波形には反映されないため）
    apply_f0_jitter(tl_scratch.f0.data(), output_frames, kFramePeriod,
                     p.global_time_sec, voice_seed);

    note_buf.assign(static_cast<size_t>(note_samples), 0.0);
    try {
        VOSE_Synthesis(tl_scratch.f0.data(), output_frames,
                       tl_scratch.spec_ptrs.data(), tl_scratch.ap_ptrs.data(),
                       fft_size, kFramePeriod, pp.ev->fs,
                       static_cast<int>(note_samples), note_buf.data());
    } catch (const std::exception& e) {
        char buf[256];
        snprintf(buf, sizeof(buf),
                 "VOSE_Synthesis failed: note_samples=%lld note_ms=%.2f "
                 "output_frames=%d fft_size=%d src_ms=%.2f fixed=%.2f : %s",
                 static_cast<long long>(note_samples), note_ms, output_frames,
                 fft_size, src_ms, current_oto.consonant, e.what());
        throw std::runtime_error(buf);
    }

    // ポストEQ: WORLD出力の金属的倍音・箱鳴り補正、ヌケの向上
    apply_post_eq(note_buf.data(), static_cast<int>(note_samples));

    // シマー(振幅ゆらぎ)は出力波形に対して適用する
    try {
        apply_shimmer(note_buf, pp.ev->fs, p.global_time_sec, voice_seed);
    } catch (const std::exception& e) {
        char buf[256];
        snprintf(buf, sizeof(buf), "apply_shimmer failed: note_samples=%lld : %s",
                 static_cast<long long>(note_samples), e.what());
        throw std::runtime_error(buf);
    }
}

// ============================================================
// レンダリング進捗・キャンセル用コールバック
//
// Python(ctypes)側からは CFUNCTYPE(None, c_int) / CFUNCTYPE(c_int) として
// 渡す想定。呼び出し規約はプラットフォームデフォルト（ctypes標準）。
//   ProgressCallback:   進捗率(0-100)を通知する。nullptr可（呼ばれない）。
//   CancelCheckCallback: 非0を返すとレンダリングを中断する。nullptr可。
// ============================================================
typedef void (*ProgressCallback)(int percent);
typedef int  (*CancelCheckCallback)();

// ============================================================
// extern "C" API
// ============================================================

extern "C" {

void init_official_engine() { register_all_embedded_voices(); }

DLLEXPORT void load_embedded_resource(const char* phoneme,
                                      const int16_t* raw_data, int sample_count)
{
    if (!phoneme || !raw_data || sample_count <= 0) return;

    auto ev = std::make_shared<EmbeddedVoice>();
    ev->fs = kFs;
    ev->waveform.resize(sample_count);
    for (int i = 0; i < sample_count; ++i)
        ev->waveform[i] = static_cast<double>(raw_data[i]) * kInv32768;

    VoseUniqueLock clock(g_analysis_cache_mutex);
    // パス文字列キーでキャッシュを無効化（再ロード時も確実にヒット）
    g_analysis_cache.erase(phoneme);
    ev->path = phoneme;
    g_voice_db.put(phoneme, std::move(ev));
}

// ============================================================
// execute_render  (並列合成版)
//
// 並列化の設計:
//   パス2を「合成フェーズ」と「書き込みフェーズ」に分離する。
//
//   [合成フェーズ・並列]
//     各ノートの note_buf を std::async で独立して合成する。
//     ノード間の依存関係（current_offset, full_song_buffer）には
//     一切触れないので安全に並列化できる。
//     tl_scratch は thread_local なのでスレッドごとに独立している。
//
//   [書き込みフェーズ・順次]
//     future.get() で合成完了を待ち、apply_crossfade でシングルスレッドで書き込む。
//     full_song_buffer への書き込みはここだけなのでデータ競合なし。
//
// スレッド数:
//   std::thread::hardware_concurrency() を上限とするが、
//   音源の解析（get_or_analyze）は g_analysis_cache_mutex を取るため
//   キャッシュミス時だけ直列化される。通常はキャッシュヒットするので問題なし。
// ============================================================
 

static void execute_render_impl(NoteEvent* notes, int note_count, const char* output_path,
                                 int mode_flag,
                                 ProgressCallback progress_cb,
                                 CancelCheckCallback cancel_cb)
{
    auto report_progress = [&](int pct) {
        if (progress_cb) progress_cb(pct);
    };
    auto is_cancelled = [&]() -> bool {
        return cancel_cb && cancel_cb() != 0;
    };

    if (!notes || note_count <= 0 || !output_path) return;
    if (is_cancelled()) return;

    // ================================================================
    // Pro版（Studio Master）の判定とパラメータ設定
    // ================================================================
    bool is_pro = (mode_flag == 1);
    
    // Pro版は 32bit float (または32bit PCM)、無料版は 16bit CD音質
    int out_bit_depth = is_pro ? 32 : 16;
    
    // ※将来的に96kHz出力を行う場合は、ここの out_fs を切り替えて、
    // 最後の wavwrite 前にリサンプリング処理を挟みます。
    int out_fs = kFs; 

    CheapTrickOption ct_opt;
    InitializeCheapTrickOption(kFs, &ct_opt);
    const int fft_size  = ct_opt.fft_size;
    const int spec_bins = fft_size / 2 + 1;

    // ----------------------------------------------------------------
    // パス1: NotePrepass 構築（変更なし）
    // ----------------------------------------------------------------
    std::vector<NotePrepass> prepass(note_count);
    int     max_harvest_len  = 0;
    int64_t total_samples    = 0;
    int     xfade_count      = 0;
    bool    prev_renderable  = false;
    std::shared_ptr<const EmbeddedVoice> last_ev;

    for (int i = 0; i < note_count; ++i) {
        const int pitch_len = notes[i].pitch_length;
        if (pitch_len <= 0 || pitch_len > kMaxPitchLength) {
            prepass[i]      = NotePrepass(NoteState::INVALID, 0, nullptr);
            prev_renderable = false;
            last_ev         = nullptr;
            continue;
        }

        const int64_t ns = note_samples_safe(pitch_len);
        if (!notes[i].wav_path) {
            prepass[i]      = NotePrepass(NoteState::NO_VOICE, ns, nullptr);
            prev_renderable = false;
            last_ev         = nullptr;
            total_samples  += ns;
            continue;
        }
        auto ev = find_voice_ref(notes[i].wav_path);

        // [修正] found_oto は以前 g_oto_db 内部要素への生ポインタを
        // ロック解放後まで持ち出していた。set_oto_data() は g_oto_db を
        // clear() して再構築するため、ロック解放後にこのポインタを
        // dereference する窓（NotePrepass構築時）で use-after-free の
        // 可能性があった。ロック内で値コピーを確定させ、ロック外では
        // ローカル変数 found_oto（実体）だけを参照するように変更。
        OtoEntry found_oto{};
        bool     has_found_oto = false;
        {
            VoseUniqueLock lock(g_oto_db_mutex);
            auto oto_it = g_oto_db.find(notes[i].wav_path);
            if (oto_it != g_oto_db.end()) {
                found_oto     = oto_it->second;   // 値コピー（ロック内で確定）
                has_found_oto = true;
            }
        }

        if (ev) {
            prepass[i] = NotePrepass(NoteState::RENDERABLE, ns, ev,
                                     prev_renderable ? last_ev : nullptr,
                                     has_found_oto ? &found_oto : nullptr);
            if (prev_renderable) ++xfade_count;
            prev_renderable = true;
            last_ev         = ev;
            const int wav_len     = static_cast<int>(ev->waveform.size());
            const int harvest_len = GetSamplesForHarvest(ev->fs, wav_len, kFramePeriod);
            if (harvest_len > max_harvest_len) max_harvest_len = harvest_len;
        } else {
            prepass[i]      = NotePrepass(NoteState::NO_VOICE, ns, nullptr);
            prev_renderable = false;
            last_ev         = nullptr;
        }
        total_samples += ns;
    }

    if (total_samples <= 0) return;

    report_progress(2);
    if (is_cancelled()) return;

    (void)max_harvest_len;
    std::vector<double> full_song_buffer(total_samples, 0.0);

    // ----------------------------------------------------------------
    // パス2-A: 各ノートの note_buf を並列合成
    //
    // [修正] 以前の実装はバッチ単位（max_threads件ずつ）で std::thread を
    //   毎回新規生成しては join していた。tl_scratch は thread_local だが、
    //   スレッド自体がバッチごとに使い捨てられるため、各ノートは常に
    //   「reserved_f0=0 / reserved_bins=0」の状態から scratch を確保し直す
    //   ことになり、SynthesisScratchPad::ensure_spec() が意図していた
    //   「一度確保したら使い回す」という最適化がほぼ機能していなかった。
    //
    //   ここでは hardware_concurrency 個のワーカースレッドを合成フェーズの
    //   開始時に1回だけ生成し、全ノートの処理が終わるまで生存させる
    //   「永続ワーカープール」方式に変更する。各ワーカーは共有カウンタ
    //   (next_task) から次に処理すべきノート番号を取り出して処理する。
    //   これにより同一スレッドが複数ノートを連続して処理するようになり、
    //   tl_scratch のバッファ再利用が実際に効くようになる。
    //
    //   full_song_bufferへは誰も書き込まず、note_bufs[idx] への書き込みは
    //   ノートごとに一意なので、これまで通りデータ競合はない。
    // ----------------------------------------------------------------
    const int max_threads = static_cast<int>(
        std::max(1u, std::thread::hardware_concurrency()));

    std::vector<std::vector<double>> note_bufs(note_count);

    // RENDERABLE なノートのインデックスだけ集める
    std::vector<int> renderable_indices;
    renderable_indices.reserve(note_count);
    for (int i = 0; i < note_count; ++i)
        if (prepass[i].state == NoteState::RENDERABLE)
            renderable_indices.push_back(i);

    // ノートごとのグローバル時間オフセット（ビブラート位相連続化用）
    std::vector<double> note_global_time(note_count, 0.0);
    {
        double acc_sec = 0.0;
        for (int i = 0; i < note_count; ++i) {
            note_global_time[i] = acc_sec;
            if (prepass[i].note_samples > 0)
                acc_sec += static_cast<double>(prepass[i].note_samples) / kFs;
        }
    }

    // 進捗・キャンセルについて:
    //   合成フェーズが全体の処理時間の大半を占めるため、
    //   完了済みノート数をポーリングして進捗を通知し、キャンセル要求も
    //   ここでチェックする。コールバックは常にこのメインスレッド側からのみ
    //   呼び出す（ワーカースレッドからは直接呼ばない）ことで、
    //   Python(ctypes) 側に渡されたコールバックがスレッドセーフでなくても
    //   安全に扱えるようにする。
    //   進捗レンジは 2%〜80% を合成フェーズに割り当てる
    //   （0-2%: 準備, 80-95%: 書き込み/後処理, 95-100%: wavwrite）。
    const int total_renderable = static_cast<int>(renderable_indices.size());
    bool cancelled_during_synth = false;

    bool failed_during_synth = false;

    if (total_renderable > 0) {
        std::atomic<int>  next_task{0};
        std::atomic<int>  completed{0};
        std::atomic<bool> cancel_flag{false};

        // [修正] 以前は synthesize_note_impl() が投げうる例外
        // （std::bad_alloc、WORLDライブラリ内部の例外等）をワーカー
        // スレッド内で一切捕捉していなかった。スレッド関数から例外が
        // 漏れると std::terminate() が呼ばれ、プロセス全体が即座に
        // 落ちてしまう。ここで try/catch し、失敗を worker_failed で
        // 記録した上で他のワーカーにも早期終了させ、メインスレッド側で
        // 安全にレンダリングを打ち切れるようにする。
        std::atomic<bool> worker_failed{false};
        std::string       worker_error_msg;
        std::mutex        worker_error_mutex;
        std::atomic<int>  failed_note_count{0};

        auto worker_fn = [&]() {
            for (;;) {
                if (cancel_flag.load(std::memory_order_relaxed)) return;
                if (worker_failed.load(std::memory_order_relaxed)) return;

                const int bi = next_task.fetch_add(1, std::memory_order_relaxed);
                if (bi >= total_renderable) return;

                const int idx = renderable_indices[bi];
                try {
                    SynthNoteParams p{ prepass[idx], notes[idx], fft_size, spec_bins,
                                       note_global_time[idx] };
                    synthesize_note_impl(p, note_bufs[idx]);
                } catch (const std::exception& e) {
                    // [修正] 以前はここで worker_failed を立てて曲全体の
                    // レンダリングを中断していたが、原因不明のノート単位の
                    // 例外(メモリ破壊の疑いあり、調査中)によって曲全体が
                    // 巻き込まれるのを避けるため、このノート1つを無音として
                    // 諦めて処理を続行するように変更した。曲全体の長さの
                    // 帳尻を合わせるため、無音の長さは prepass[idx].note_samples
                    // に正確に合わせる。失敗自体はログに残し、件数もカウントする。
                    char buf[256];
                    snprintf(buf, sizeof(buf),
                             "note idx=%d wav_path=%s pitch_length=%d : %s (無音でスキップ)",
                             idx,
                             notes[idx].wav_path ? notes[idx].wav_path : "(null)",
                             notes[idx].pitch_length,
                             e.what());
                    fprintf(stderr, "[Render] %s\n", buf);
                    failed_note_count.fetch_add(1, std::memory_order_relaxed);
                    note_bufs[idx].assign(
                        static_cast<size_t>(std::max<int64_t>(0, prepass[idx].note_samples)),
                        0.0);
                } catch (...) {
                    char buf[256];
                    snprintf(buf, sizeof(buf),
                             "note idx=%d wav_path=%s pitch_length=%d : unknown exception (無音でスキップ)",
                             idx,
                             notes[idx].wav_path ? notes[idx].wav_path : "(null)",
                             notes[idx].pitch_length);
                    fprintf(stderr, "[Render] %s\n", buf);
                    failed_note_count.fetch_add(1, std::memory_order_relaxed);
                    note_bufs[idx].assign(
                        static_cast<size_t>(std::max<int64_t>(0, prepass[idx].note_samples)),
                        0.0);
                }

                const int done = completed.fetch_add(1, std::memory_order_relaxed) + 1;
#if defined(__EMSCRIPTEN__) && !defined(__EMSCRIPTEN_PTHREADS__)
                if (is_cancelled()) {
                    cancel_flag.store(true, std::memory_order_relaxed);
                    cancelled_during_synth = true;
                    return;
                }
                const int pct = 2 + static_cast<int>(
                    (static_cast<double>(done) / total_renderable) * 78.0);
                report_progress(std::min(pct, 80));
                if (done == 1 || done % 5 == 0 || done == total_renderable) {
                    fprintf(stdout, "[Render] Note %d/%d completed (progress=%d%%)\n", done, total_renderable, pct);
                }
#endif
            }
        };

        const int worker_count = std::min(max_threads, total_renderable);

#if defined(__EMSCRIPTEN__) && !defined(__EMSCRIPTEN_PTHREADS__)
        worker_fn();
        if (is_cancelled()) {
            cancel_flag.store(true, std::memory_order_relaxed);
            cancelled_during_synth = true;
        }
#else
        std::vector<std::thread> workers;
        workers.reserve(worker_count);
        for (int i = 0; i < worker_count; ++i)
            workers.emplace_back(worker_fn);

        // メインスレッドは進捗通知とキャンセル監視に専念する。
        // worker_failed も終了条件に含めないと、例外発生時に completed が
        // total_renderable に到達しないままこのループが回り続けてしまう。
        while (completed.load(std::memory_order_relaxed) < total_renderable) {
            if (worker_failed.load(std::memory_order_relaxed)) {
                cancelled_during_synth = true; // 以降のパス2-Bをスキップさせる
                break;
            }
            if (is_cancelled()) {
                cancel_flag.store(true, std::memory_order_relaxed);
                cancelled_during_synth = true;
                break;
            }
            const int done = completed.load(std::memory_order_relaxed);
            const int pct  = 2 + static_cast<int>(
                (static_cast<double>(done) / total_renderable) * 78.0);
            report_progress(std::min(pct, 80));
            std::this_thread::sleep_for(std::chrono::milliseconds(30));
        }

        for (auto& t : workers) t.join();
#endif

        if (worker_failed.load(std::memory_order_relaxed)) {
            failed_during_synth = true;
            fprintf(stderr, "[Render] Worker thread failed: %s\n", worker_error_msg.c_str());
        } else {
            const int nfail = failed_note_count.load(std::memory_order_relaxed);
            if (nfail > 0) {
                fprintf(stderr,
                        "[Render] %d note(s) failed and were skipped as silence "
                        "(see individual [Render] logs above for detail)\n", nfail);
            }
            if (!cancelled_during_synth) {
                report_progress(80);
            }
        }
    }

    if (failed_during_synth) return; // 例外発生時はレンダリングを中断（wavwriteしない）
    if (cancelled_during_synth || is_cancelled()) return;

    // ----------------------------------------------------------------
    // パス2-B: 書き込みフェーズ
    // ----------------------------------------------------------------
    int64_t timeline_offset    = 0;
    bool    last_note_rendered = false;

    for (int idx = 0; idx < note_count; ++idx) {
        const NotePrepass& pp = prepass[idx];
        const int64_t note_samples = pp.note_samples;

        if (pp.state != NoteState::RENDERABLE) {
            last_note_rendered = false;
            timeline_offset += note_samples;
            continue;
        }

        // ノート境界での滑らかな接続
        if (last_note_rendered) {
            // 直前ノートと連続している場合:
            // 直前ノートの末尾と現在ノートの先頭を 2ms (88サンプル) でオーバーラップ・クロスフェード接続する。
            // 以前のような「直前ノートを0にフェードアウトしてから次ノートを0からフェードインする」処理だと
            // 境界で4msの完全な無音の谷間（振幅ディップ）が生じ、プチプチ・ガタガタというノイズの原因になっていた。
            // fade_out + fade_in = 1.0 の定ゲイン・クロスフェードにより、音圧の落ち込みやクリックのない
            // シームレスで滑らかなレガート接続を実現する。
            const int declick = static_cast<int>(std::min<int64_t>(88, note_samples / 8));
            for (int s = 0; s < declick; ++s) {
                const double t = (declick > 1) ? (static_cast<double>(s) / declick) : 0.5;
                const double fade_in  = 0.5 * (1.0 - std::cos(M_PI * t));
                const double fade_out = 0.5 * (1.0 + std::cos(M_PI * t));
                const int64_t di = timeline_offset - declick + s;
                if (di >= 0 && di < total_samples && s < note_samples) {
                    full_song_buffer[di] = full_song_buffer[di] * fade_out + note_bufs[idx][s] * fade_in;
                }
            }
            for (int64_t s = declick; s < note_samples; ++s) {
                const int64_t di = timeline_offset - declick + s;
                if (di >= 0 && di < total_samples) {
                    full_song_buffer[di] = note_bufs[idx][s];
                }
            }
            timeline_offset += (note_samples - declick);
        } else {
            // 休符明けの立ち上がり: 3msのデクリック・フェードイン
            const int fade_in_samples = static_cast<int>(std::min<int64_t>(132, note_samples / 4));
            for (int s = 0; s < fade_in_samples; ++s) {
                const double t = (fade_in_samples > 1) ? (static_cast<double>(s) / fade_in_samples) : 1.0;
                const double fade_in = 0.5 * (1.0 - std::cos(M_PI * t));
                const int64_t di = timeline_offset + s;
                if (di < total_samples && s < note_samples) {
                    full_song_buffer[di] = note_bufs[idx][s] * fade_in;
                }
            }
            for (int64_t s = fade_in_samples; s < note_samples; ++s) {
                const int64_t di = timeline_offset + s;
                if (di < total_samples) {
                    full_song_buffer[di] = note_bufs[idx][s];
                }
            }
            timeline_offset += note_samples;
        }

        // 次のノートがRENDERABLEでない、または曲末尾の場合: 5msのデクリック・フェードアウト
        const bool next_rendered = (idx + 1 < note_count && prepass[idx + 1].state == NoteState::RENDERABLE);
        if (!next_rendered) {
            const int fade_out_samples = static_cast<int>(std::min<int64_t>(220, note_samples / 4));
            for (int s = 0; s < fade_out_samples; ++s) {
                const double t = (fade_out_samples > 1) ? (static_cast<double>(s) / fade_out_samples) : 1.0;
                const double fade_out = 0.5 * (1.0 + std::cos(M_PI * t));
                const int64_t di = timeline_offset - fade_out_samples + s;
                if (di >= 0 && di < total_samples) {
                    full_song_buffer[di] *= fade_out;
                }
            }
        }

        last_note_rendered = true;
    }
    report_progress(85);
    if (is_cancelled()) return;

    // ----------------------------------------------------------------
    // BigVGAN ボコーダー処理
    //
    // WORLD合成の出力PCM（double[]）をメルスペクトログラムに変換し、
    // BigVGAN ONNX モデルで高品質PCMに再合成する。
    //
    // パイプライン:
    //   full_song_buffer (WORLD出力, double[])
    //     → pcm_float (float[], [-1,1] 正規化)
    //     → mel_filterbank (128bin, win=2048, hop=512, 44100Hz)
    //     → BigVGAN推論 (128フレームchunk, 25msオーバーラップ)
    //     → overlap-add → wavwrite
    //
    // ※ models/bigvgan/bigvgan_generator.onnx / bigvgan/config.json の
    //   実仕様に合わせたパラメータ（旧: 80bin/1024fft/256hopから変更）。
    //   入出力テンソル名も実モデルに合わせて "mel" / "audio"。
    //
    // BigVGANが無効なら従来通り WORLD出力をそのまま wavwrite する。
    // ----------------------------------------------------------------
{
        const double* src   = full_song_buffer.data();
        const int     n_src = static_cast<int>(total_samples);

#ifdef VOSE_PRO
        // [修正] set_bigvgan_model() によるセッション差し替え（reset/再生成）と
        // ここでの読み取り・Run() 呼び出しが無保護で競合していた（UAFの危険）。
        // 共有ロックを取ることで、差し替え中はここに進めないようにする。
        // ロックは Run() を含むブロック全体にかける（差し替えが完了するまで
        // 古いセッションの生存を保証する必要があるため）。
        std::shared_lock<VoseSharedMutex> bigvgan_read_lock(g_bigvgan_mutex);
        if (g_bigvgan_session && n_src > 0) {
            // ----------------------------------------------------------
            // ステップ1: double → float 正規化
            // ----------------------------------------------------------
            std::vector<float> pcm(n_src);
            for (int i = 0; i < n_src; ++i)
                pcm[i] = static_cast<float>(clamp(src[i], -1.0, 1.0));

            // ----------------------------------------------------------
            // ステップ2: メルスペクトログラム変換
            //
            // パラメータ (bigvgan/config.json の実値に一致させる):
            //   sample_rate = 44100
            //   n_fft       = 2048
            //   win_size    = 2048
            //   hop_size    = 512
            //   n_mels      = 128
            //   fmin        = 0 Hz
            //   fmax        = 22050 Hz (Nyquist, config.json では null)
            //
            // 振幅(magnitude)スペクトルを使用（torch.stft → sqrt(re²+im²)相当）。
            // パワースペクトル(re²+im²のみ)ではBigVGAN学習時の分布とズレるため誤り。
            // フレーム境界は reflect-padding（torch.stft(center=True)と同じ挙動）。
            // ----------------------------------------------------------
            constexpr int   kNFft    = 2048;
            constexpr int   kHop     = 512;
            constexpr int   kNMels   = 128;
            constexpr float kFMin    = 0.0f;
            constexpr float kFMax    = 22050.0f;
            constexpr float kSR      = 44100.0f;
            constexpr float kLogFloor = 1e-5f;  // log(mel) のフロア

            const int n_frames = (n_src + kHop - 1) / kHop;

            // Hann窓
            std::vector<float> window(kNFft);
            for (int i = 0; i < kNFft; ++i)
                window[i] = 0.5f * (1.0f - std::cos(2.0f * static_cast<float>(M_PI) * i / kNFft));

            // メルフィルタバンク行列を構築（n_mels × (n_fft/2+1)）
            // 正規化: 各フィルタをその帯域幅で割る slaney 正規化
            // （librosa.filters.mel のデフォルトと同じ。BigVGAN学習側と一致させる）
            const int spec_bins_fft = kNFft / 2 + 1;
            auto hz_to_mel = [](float hz) { return 2595.0f * std::log10(1.0f + hz / 700.0f); };
            auto mel_to_hz = [](float mel) { return 700.0f * (std::pow(10.0f, mel / 2595.0f) - 1.0f); };

            std::vector<std::vector<float>> mel_fb(kNMels, std::vector<float>(spec_bins_fft, 0.0f));
            {
                const float mel_min = hz_to_mel(kFMin);
                const float mel_max = hz_to_mel(kFMax);
                std::vector<float> mel_pts(kNMels + 2);
                for (int m = 0; m < kNMels + 2; ++m)
                    mel_pts[m] = mel_to_hz(mel_min + (mel_max - mel_min) * m / (kNMels + 1));

                for (int m = 0; m < kNMels; ++m) {
                    const float norm = 2.0f / (mel_pts[m+2] - mel_pts[m]); // 面積正規化
                    for (int k = 0; k < spec_bins_fft; ++k) {
                        const float hz = k * kSR / kNFft;
                        if (hz >= mel_pts[m] && hz <= mel_pts[m+1])
                            mel_fb[m][k] = norm * (hz - mel_pts[m]) / (mel_pts[m+1] - mel_pts[m]);
                        else if (hz > mel_pts[m+1] && hz <= mel_pts[m+2])
                            mel_fb[m][k] = norm * (mel_pts[m+2] - hz) / (mel_pts[m+2] - mel_pts[m+1]);
                    }
                }
            }

            // メルスペクトログラム [n_frames][n_mels]
            std::vector<std::vector<float>> mel_spec(n_frames, std::vector<float>(kNMels, kLogFloor));
            {
                std::vector<float> frame_buf(kNFft, 0.0f);
                std::vector<float> mag(spec_bins_fft);
                std::vector<float> re(kNFft), im(kNFft);

                // reflect-pad で参照するためのヘルパー（torch.stft(center=True, pad_mode="reflect")と同じ）
                auto reflect_sample = [&](int s) -> float {
                    if (n_src <= 1) return (n_src == 1) ? pcm[0] : 0.0f;
                    while (s < 0 || s >= n_src) {
                        if (s < 0)        s = -s;
                        if (s >= n_src)   s = 2 * (n_src - 1) - s;
                    }
                    return pcm[s];
                };

                for (int t = 0; t < n_frames; ++t) {
                    const int center = t * kHop;
                    for (int i = 0; i < kNFft; ++i) {
                        const int s = center - kNFft/2 + i;
                        frame_buf[i] = reflect_sample(s) * window[i];
                    }

                    // Cooley-Tukey 基数2 DIT FFT（正しい順序）
                    // ステップ1: ビット反転並べ替え（これを先にやる）
                    std::copy(frame_buf.begin(), frame_buf.end(), re.begin());
                    std::fill(im.begin(), im.end(), 0.0f);
                    {
                        int j = 0;
                        for (int i = 1; i < kNFft; ++i) {
                            int bit = kNFft >> 1;
                            for (; j & bit; bit >>= 1) j ^= bit;
                            j ^= bit;
                            if (i < j) { std::swap(re[i], re[j]); }
                        }
                    }
                    // ステップ2: バタフライ演算（並べ替え後に実行）
                    for (int step = 1; step < kNFft; step <<= 1) {
                        const float ang_base = -static_cast<float>(M_PI) / step;
                        for (int i = 0; i < kNFft; i += step * 2) {
                            for (int j = 0; j < step; ++j) {
                                const float ang = ang_base * j;
                                const float wr  = std::cos(ang), wi = std::sin(ang);
                                const float tr  = wr*re[i+j+step] - wi*im[i+j+step];
                                const float ti  = wr*im[i+j+step] + wi*re[i+j+step];
                                re[i+j+step] = re[i+j] - tr;
                                im[i+j+step] = im[i+j] - ti;
                                re[i+j]     += tr;
                                im[i+j]     += ti;
                            }
                        }
                    }

                    // 振幅（magnitude）スペクトル：sqrt(re²+im²)。パワーではない。
                    for (int k = 0; k < spec_bins_fft; ++k)
                        mag[k] = std::sqrt(re[k]*re[k] + im[k]*im[k] + 1e-9f);

                    for (int m = 0; m < kNMels; ++m) {
                        float val = 0.0f;
                        for (int k = 0; k < spec_bins_fft; ++k)
                            val += mel_fb[m][k] * mag[k];
                        mel_spec[t][m] = std::log(std::max(val, kLogFloor));
                    }
                }
            }

            // ----------------------------------------------------------
            // ステップ3: BigVGAN推論
            //
            // chunk_frames = 128  → 65536サンプル出力 (hop=512換算)
            // overlap_frames = 25ms @ hop=512 → ceil(25ms*44100/512) = 3フレーム
            // オーバーラップ部は raised-cosine でブレンド
            // ----------------------------------------------------------
            constexpr int kChunkFrames   = 128;
            constexpr int kOverlapFrames = 3;   // 25ms相当（実測で十分な連続性）
            constexpr int kChunkSamples  = kChunkFrames * kHop;  // 65536
            constexpr int kOverlapSamples = kOverlapFrames * kHop;

            std::vector<float> out_pcm(n_frames * kHop, 0.0f);

            Ort::MemoryInfo mem_info =
                Ort::MemoryInfo::CreateCpu(OrtArenaAllocator, OrtMemTypeDefault);

            // 実ONNXモデルの入出力テンソル名（"input_mel"/"output_audio"ではない）
            const char* input_name  = "mel";
            const char* output_name = "audio";

            std::vector<float> chunk_mel(kNMels * kChunkFrames);

            for (int t_start = 0; t_start < n_frames; t_start += kChunkFrames - kOverlapFrames) {
                if (is_cancelled()) return;

                // メルchunkを [1, 128, 128] に充填（足りない部分は末尾フレームで埋める）
                for (int t = 0; t < kChunkFrames; ++t) {
                    const int src_t = std::min(t_start + t, n_frames - 1);
                    for (int m = 0; m < kNMels; ++m)
                        chunk_mel[m * kChunkFrames + t] = mel_spec[src_t][m];
                }

                // ONNX推論
                std::array<int64_t, 3> input_shape  = {1, kNMels, kChunkFrames};
                std::array<int64_t, 3> output_shape = {1, 1, kChunkSamples};

                auto input_tensor = Ort::Value::CreateTensor<float>(
                    mem_info, chunk_mel.data(), chunk_mel.size(),
                    input_shape.data(), input_shape.size());

                auto outputs = g_bigvgan_session->Run(
                    Ort::RunOptions{nullptr},
                    &input_name, &input_tensor, 1,
                    &output_name, 1);

                const float* chunk_out = outputs[0].GetTensorData<float>();

                // オーバーラップアド（raised-cosine ブレンド）
                const int write_sample = t_start * kHop;
                for (int s = 0; s < kChunkSamples; ++s) {
                    const int out_s = write_sample + s;
                    if (out_s >= static_cast<int>(out_pcm.size())) break;

                    if (s < kOverlapSamples && t_start > 0) {
                        // オーバーラップ領域: 前chunkと raised-cosine ブレンド
                        const float fade_in = 0.5f * (1.0f -
                            std::cos(static_cast<float>(M_PI) * s / kOverlapSamples));
                        out_pcm[out_s] = out_pcm[out_s] * (1.0f - fade_in)
                                       + chunk_out[s]   * fade_in;
                    } else {
                        out_pcm[out_s] = chunk_out[s];
                    }
                }

                // 曲末に達したら終了
                if (t_start + kChunkFrames >= n_frames) break;
            }

            // ----------------------------------------------------------
            // ステップ4: BigVGAN出力を double[] に変換して wavwrite
            // ----------------------------------------------------------
            std::vector<double> bigvgan_out(n_src);
            for (int i = 0; i < n_src; ++i)
                bigvgan_out[i] = clamp(static_cast<double>(out_pcm[i]), -1.0, 1.0);

            report_progress(95);
            if (is_cancelled()) return;
            wavwrite(bigvgan_out.data(), n_src, out_fs, out_bit_depth, output_path);

        } else {
            // BigVGAN無効時（Pro版としてビルドされているがモデル未ロード時）: WORLD出力をそのまま書き出す
            report_progress(95);
            if (is_cancelled()) return;
            wavwrite(src, n_src, out_fs, out_bit_depth, output_path);
        }
#else
        // 無印版ビルド時: ONNX関連をすべて無視してWORLD出力をそのまま書き出す
        report_progress(95);
        if (is_cancelled()) return;
        wavwrite(src, n_src, out_fs, out_bit_depth, output_path);
#endif
    }

    report_progress(100);
}

// ============================================================
// execute_render / execute_render_cancelable
//   公開APIの2種類:
//     - execute_render: 従来通りのシグネチャ（後方互換用）。
//     - execute_render_cancelable: 進捗コールバックとキャンセルチェックを
//       追加した新API。GUI側（Python）はこちらを使うことでプログレスバーと
//       キャンセルボタンを実装できる。
// ============================================================

DLLEXPORT void execute_render(NoteEvent* notes, int note_count, const char* output_path, int mode_flag)
{
    execute_render_impl(notes, note_count, output_path, mode_flag, nullptr, nullptr);
}

DLLEXPORT void execute_render_cancelable(NoteEvent* notes, int note_count, const char* output_path,
                                          int mode_flag,
                                          ProgressCallback progress_cb,
                                          CancelCheckCallback cancel_cb)
{
    execute_render_impl(notes, note_count, output_path, mode_flag, progress_cb, cancel_cb);
}

// 🚀 【新規追加】PipelineBridgeから転送された構造体配列をC++のベクタにコピーする
DLLEXPORT void set_vocal_timeline(const VoseFrame* frames, int frame_count) {
    VoseUniqueLock lock(g_timeline_mutex);
    g_vocal_timeline.clear();
    
    if (frames != nullptr && frame_count > 0) {
        // O(N)の高速コピー。これでC++側のWORLDやVITS推論器は、
        // いつでも g_vocal_timeline にアクセスして1サンプル単位のウェイトや音素を取得できます。
        g_vocal_timeline.assign(frames, frames + frame_count);
    }
}

// エンジンのバージョン番号を返す
DLLEXPORT float get_engine_version(void)
{
    return 1.0f; // 実際のバージョン番号に置き換えてください
}

// 音声データベース／解析キャッシュをクリアする
DLLEXPORT void clear_engine_cache(void)
{
    g_voice_db.clear();
    {
        std::lock_guard<std::mutex> lock(g_analysis_cache_mutex);
        g_analysis_cache.clear();
    }
}

} // extern "C"

// ============================================================
// BigVGAN セッション管理
// ============================================================
#ifdef VOSE_PRO
namespace {
    static Ort::Env            g_ort_env{ORT_LOGGING_LEVEL_WARNING, "vose_bigvgan"};
    static Ort::SessionOptions g_ort_opts;
}
#endif

#if defined(_WIN32) || defined(_WIN64)
// UTF-8(char*)をWindows API(Ort::Sessionのwchar_tコンストラクタ等)が要求する
// UTF-16(wstring)へ正しく変換する。
//
// [修正前のバグ] 旧実装は
//     std::wstring wpath(onnx_path, onnx_path + strlen(onnx_path));
// のように char を1バイトずつ wchar_t へ広げているだけだった。これは
// UTF-8→UTF-16変換ではなくLatin-1相当の単純拡張でしかないため、
// 日本語を含むパス(例: C:\Users\田中\models\bigvgan.onnx)を渡すと
// 文字化けし、ファイルが見つからずモデルロードに失敗していた。
//
// MB_ERR_INVALID_CHARS: 不正なUTF-8シーケンスがあった場合、黙って
// 文字を落とす/置換するのではなく変換自体を失敗(戻り値0)させる。
// ファイルパスの変換では「化けたパスで開こうとして原因不明のエラーになる」
// より「変換失敗を早期に検知する」方が診断しやすいため、あえて厳格にしている。
static std::wstring utf8_to_wstring(const char* utf8_str) {
    if (!utf8_str || utf8_str[0] == '\0') return std::wstring();

    const int required_len = MultiByteToWideChar(
        CP_UTF8, MB_ERR_INVALID_CHARS, utf8_str, -1, nullptr, 0);
    if (required_len <= 1) {
        // 0: 変換失敗(不正なUTF-8)。1: NUL終端のみ(=元が空文字列、既に上でreturn済のはずだが念のため)。
        return std::wstring();
    }

    // required_lenはNUL終端を含む長さなので、wstring自体の長さは-1する。
    std::wstring wide_str(static_cast<size_t>(required_len) - 1, L'\0');
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, utf8_str, -1, &wide_str[0], required_len);
    return wide_str;
}
#endif

// BigVGAN ONNXモデルのパスを設定する（init_official_engine から呼ぶ）
// path が nullptr または空なら BigVGAN を無効化する
extern "C" DLLEXPORT void set_bigvgan_model(const char* onnx_path) {
#ifdef VOSE_PRO
    // 排他ロック: モデル差し替え中は推論側（execute_render_impl）の
    // 共有ロック取得をブロックし、差し替え完了後の一貫した状態のみを読ませる。
    std::unique_lock<VoseSharedMutex> lk(g_bigvgan_mutex);
    if (!onnx_path || onnx_path[0] == '\0') {
        g_bigvgan_session.reset();
        return;
    }
    try {
        g_ort_opts.SetIntraOpNumThreads(
            static_cast<int>(std::max(1u, std::thread::hardware_concurrency())));
        g_ort_opts.SetGraphOptimizationLevel(ORT_ENABLE_ALL);
#ifdef _WIN32
        const std::wstring wpath = utf8_to_wstring(onnx_path);
        if (wpath.empty()) {
            // onnx_pathは上のチェックで非空と分かっているので、ここに来るのは
            // 不正なUTF-8シーケンスによる変換失敗のみ。
            fprintf(stderr, "[BigVGAN] Failed to convert path to UTF-16 (invalid UTF-8?): %s\n", onnx_path);
            g_bigvgan_session.reset();
            return;
        }
        g_bigvgan_session = std::make_unique<Ort::Session>(g_ort_env, wpath.c_str(), g_ort_opts);
#else
        g_bigvgan_session = std::make_unique<Ort::Session>(g_ort_env, onnx_path, g_ort_opts);
#endif
    } catch (const std::exception& e) {
        // モデルロード失敗時はWORLD直接出力にフォールバック。
        // Git LFSポインタ未取得（.onnx.data が実バイナリでない）等が典型的な原因。
        fprintf(stderr, "[BigVGAN] Failed to load model '%s': %s\n", onnx_path, e.what());
        g_bigvgan_session.reset();
    } catch (...) {
        fprintf(stderr, "[BigVGAN] Failed to load model '%s': unknown error\n", onnx_path);
        g_bigvgan_session.reset();
    }
#else
    (void)onnx_path; // 未使用変数警告の抑制
#endif
}
