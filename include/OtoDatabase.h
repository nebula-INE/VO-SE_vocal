// OtoDatabase.h
//
// modules/data/oto_parser.py の C++ 移植。
// パース・alias解決ロジックは Python 版と1:1で対応させている
// (resolveAlias の VCV→CV→単独音→部分一致フォールバックの順序を含む)。
//
// 【重要な制約】 vose_core には「ディスク上のWAVを直接読む」経路が無い。
// find_voice_ref() は g_voice_db（load_embedded_resource でしか埋まらない）
// をキー検索するだけ。したがって:
//
//   1. oto.ini をパースして alias 一覧を得る (このファイルの役割)
//   2. alias が指す実WAVファイルを読み込み、int16 PCM に変換
//   3. load_embedded_resource(alias, pcm, count) でコア側メモリに事前登録
//   4. set_oto_data(entries) で offset/preutterance 等もコアへ転送
//
// をプラグイン起動時（または音源切り替え時）に一括して行う必要がある。
// loadVoiceDirectoryIntoCore() がこの4ステップをまとめて実行する。

#pragma once

#include <juce_audio_formats/juce_audio_formats.h>
#include <juce_core/juce_core.h>
#include "VoseBridge.h"
#include "TextEncoding.h"
#include <map>
#include <cstring>
#include <regex>
#include <algorithm>

struct OtoEntryCpp
{
    juce::String alias;        // エイリアス名 (例: "a い", "- い", "い")
    juce::String filename;     // 対応 WAV ファイル名
    juce::String voiceDir;     // oto.ini が置かれているフォルダの絶対パス

    double leftBlank    = 0.0; // ms
    double fixedRange   = 0.0; // ms
    double rightBlank   = 0.0; // ms（負値可）
    double preutterance = 0.0; // ms
    double overlap      = 0.0; // ms

    juce::File getWavFile() const
    {
        // oto.ini may use Windows backslashes even when the application runs
        // on macOS/Linux. Normalize the relative WAV path before resolving it.
        auto normalizedFilename = filename.replaceCharacter ('\\', '/');
        return juce::File (voiceDir).getChildFile (normalizedFilename);
    }
};

class OtoDatabase
{
public:
    // oto.ini を1ファイル読み込んでデータベースに追加。追加件数を返す。
    int loadOtoFile (const juce::File& iniFile)
    {
        if (! iniFile.existsAsFile())
            return 0;

        const auto voiceDir = iniFile.getParentDirectory().getFullPathName();
        const auto content  = readSafe (iniFile);
        int count = 0;

        for (auto line : juce::StringArray::fromLines (content))
        {
            line = line.trim();
            if (line.isEmpty() || ! line.contains ("="))
                continue;

            OtoEntryCpp entry;
            if (parseLine (line, voiceDir, entry))
            {
                // Multiple oto.ini files can legally contain the same alias
                // (for example multi-pitch subfolders). Do not let traversal
                // order silently replace an already indexed WAV.
                const inserted = db.emplace (entry.alias, entry);
                if (! inserted.second)
                {
                    juce::Logger::writeToLog (
                        "OtoDatabase: duplicate alias skipped: " + entry.alias
                        + " (" + entry.getWavFile().getFullPathName() + ")");
                }
                ++count;
            }
        }
        return count;
    }

    // フォルダ以下を再帰的に走査し、見つかった oto.ini を全部ロード
    int loadVoiceDir (const juce::File& voiceDir)
    {
        int total = 0;
        if (! voiceDir.isDirectory())
            return 0;

        auto otoFiles = voiceDir.findChildFiles (juce::File::findFiles, true, "oto.ini");
        // Make first-wins duplicate handling deterministic across filesystems.
        std::sort (
            otoFiles.begin(),
            otoFiles.end(),
            [] (const juce::File& a, const juce::File& b)
            {
                return a.getFullPathName() < b.getFullPathName();
            });

        for (const auto& f : otoFiles)
            total += loadOtoFile (f);

        return total;
    }

    const OtoEntryCpp* get (const juce::String& alias) const
    {
        auto it = db.find (alias);
        return it != db.end() ? &it->second : nullptr;
    }

    // VCV ("a い") → CV+無音 ("- い") → 単独音 ("い") → 部分一致 の順で解決。
    // prevVowel が空文字/nullなら VCV 候補はスキップ（Python版と同じ挙動）。
    const OtoEntryCpp* resolveAlias (const juce::String& lyric, const juce::String& prevVowel) const
    {
        // Python版と同じ候補順で解決する。音源によって
        // "a い" / "a_い" / "aい" のように区切り方が異なるため、
        // VCV候補を複数表記で確認する。
        if (lyric.isEmpty())
            return nullptr;

        // Rest markers must never resolve to an audio alias. Keep this
        // consistent with the Python and Web/WASM resolvers.
        const auto restLyric = lyric.trim().toLowerCase();
        const juce::StringArray restMarkers {
            "r", "r_", "r_0", "[r]", "息", "br", "pau", "sil",
            "吸", "吸気", "息吸い", "休", "休符", "・", "-", "ー", "~", "null"
        };
        if (restMarkers.contains (restLyric))
            return nullptr;

        // UST/音源側に付く VCV/CV プレフィックスとピッチサフィックスを
        // Python版と同じ考え方で正規化してから候補を生成する。
        auto cleanLyric = lyric.trim();

        // Normalize common pitch suffixes used by UST/voicebanks.
        // Keep this aligned with the Python/Web resolvers:
        //   あ_C4 / あC4 / あ_1 / あ↑
        {
            const auto text = cleanLyric.toStdString();
            std::smatch match;
            const std::regex pitchSuffix (R"((_?[A-Ga-g][#b]?[0-9]|_[0-9]|[↑↓強弱SP])$)");
            if (std::regex_search (text, match, pitchSuffix) && match.position() >= 0)
                cleanLyric = cleanLyric.substring (0, static_cast<int> (match.position())).trim();
        }

        // Normalize initial CV/VCV markers such as "-あ", "- あ", "_あ".
        while (cleanLyric.startsWithChar ('_'))
            cleanLyric = cleanLyric.substring (1).trimStart();
        if (cleanLyric.startsWithChar ('-'))
            cleanLyric = cleanLyric.substring (1).trimStart();

        if (prevVowel.isNotEmpty())
        {
            if (auto* e = get (prevVowel + " " + cleanLyric))
                return e;
            if (auto* e = get (prevVowel + "_" + cleanLyric))
                return e;
            if (auto* e = get (prevVowel + cleanLyric))
                return e;
        }

        // 語頭/CV候補も Python版と同じく複数表記を扱う。
        if (auto* e = get ("- " + cleanLyric))
            return e;
        if (auto* e = get ("_" + cleanLyric))
            return e;
        if (auto* e = get ("-" + cleanLyric))
            return e;

        if (auto* e = get (lyric))
            return e;
        if (cleanLyric != lyric)
            if (auto* e = get (cleanLyric))
                return e;

        // Python版と同じく、明示された prevVowel がある場合は
        // 別のVCV文脈への部分一致fallbackを禁止する。
        // ここまでで VCV -> CV/語頭 -> plain lyric の安全なfallbackは
        // 完了しているため、"u い" が "a い" などへ化けることを防ぐ。
        if (prevVowel.isNotEmpty())
            return nullptr;

        // 末尾一致の部分一致フォールバック（Python版の
        // match_pref(cleanLyric) 相当）。異なる歌詞を誤選択しないよう、
        // alias全体ではなく「区切り付き末尾一致」に限定する。
        const auto suffix = " " + cleanLyric;
        for (const auto& [alias, entry] : db)
            if (alias == lyric || alias == cleanLyric || alias.endsWith (suffix))
                return &entry;

        return nullptr;
    }

    juce::StringArray allAliases() const
    {
        juce::StringArray result;
        for (const auto& [alias, entry] : db)
            result.add (alias);
        return result;
    }

    bool hasVcv() const
    {
        for (const auto& [alias, entry] : db)
            if (alias.contains (" "))
                return true;
        return false;
    }

    void clear() { db.clear(); }
    int size() const { return (int) db.size(); }

    // --------------------------------------------------------------
    // vose_core への一括転送: WAV先読み込み(load_embedded_resource) +
    // oto情報転送(set_oto_data)。戻り値は正常に登録できたエントリ数。
    // --------------------------------------------------------------
    int pushAllToCore (VoseCoreLibrary& core) const
    {
        if (! core.isLoaded() || core.load_embedded_resource == nullptr)
            return 0;

        juce::AudioFormatManager fm;
        fm.registerBasicFormats();

        std::vector<OtoEntry> cEntries;
        cEntries.reserve (db.size());
        int loadedCount = 0;

        for (const auto& [alias, entry] : db)
        {
            // ---- ステップ1: 実WAVをint16 PCMへ変換して事前登録 ----
            std::unique_ptr<juce::AudioFormatReader> reader (
                fm.createReaderFor (entry.getWavFile()));

            if (reader != nullptr && reader->numChannels > 0 && reader->lengthInSamples > 0)
            {
                juce::AudioBuffer<float> floatBuf ((int) reader->numChannels,
                                                    (int) reader->lengthInSamples);
                reader->read (&floatBuf, 0, (int) reader->lengthInSamples, 0, true, true);

                // モノラルにダウンミックス（vose_coreはモノラル前提）
                std::vector<int16_t> pcm ((size_t) reader->lengthInSamples);
                const int numCh = (int) reader->numChannels;
                for (int i = 0; i < (int) reader->lengthInSamples; ++i)
                {
                    float sum = 0.0f;
                    for (int ch = 0; ch < numCh; ++ch)
                        sum += floatBuf.getSample (ch, i);
                    sum /= (float) numCh;
                    pcm[(size_t) i] = (int16_t) juce::jlimit (-32768.0f, 32767.0f, sum * 32768.0f);
                }

                core.load_embedded_resource (alias.toRawUTF8(), pcm.data(), (int) pcm.size());
                ++loadedCount;
            }
            else
            {
                juce::Logger::writeToLog ("OtoDatabase: WAV読み込み失敗 (" + alias + "): "
                                           + entry.getWavFile().getFullPathName());
                continue; // WAVが読めないエントリは oto_data にも入れない
            }

            // ---- ステップ2: OtoEntry (C ABI) へ変換してバッチ転送用に蓄積 ----
            OtoEntry c {};
            c.filename = nullptr; // vose_core側では未使用のためnullptrで安全
            c.cutoff   = entry.rightBlank; // execute_render の map_time が cutoff<0 を「末尾からの距離」として扱う
            copyTruncated (alias,          c.alias,    sizeof (c.alias));
            copyTruncated (entry.getWavFile().getFullPathName(), c.wav_path, sizeof (c.wav_path));
            c.offset       = entry.leftBlank;
            c.consonant    = entry.fixedRange;
            c.blank        = entry.rightBlank;
            c.preutterance = entry.preutterance;
            c.overlap      = entry.overlap;
            cEntries.push_back (c);
        }

        if (core.set_oto_data != nullptr && ! cEntries.empty())
            core.set_oto_data (cEntries.data(), (int) cEntries.size());

        return loadedCount;
    }

private:
    static void copyTruncated (const juce::String& src, char* dst, size_t dstSize)
    {
        const auto utf8 = src.toRawUTF8();
        std::memset (dst, 0, dstSize);
        std::strncpy (dst, utf8, dstSize - 1);
    }

    static bool parseLine (const juce::String& line, const juce::String& voiceDir, OtoEntryCpp& outEntry)
    {
        const int eq = line.indexOfChar ('=');
        if (eq < 0)
            return false;

        const auto filenamePart = line.substring (0, eq).trim();
        const auto paramsPart   = line.substring (eq + 1);
        auto parts = juce::StringArray::fromTokens (paramsPart, ",", "");
        for (auto& p : parts) p = p.trim();

        auto fAt = [&] (int idx, double fallback = 0.0) -> double
        {
            if (idx < 0 || idx >= parts.size() || parts[idx].isEmpty())
                return fallback;
            return parts[idx].getDoubleValue();
        };

        const auto alias = (parts.size() > 0 && parts[0].isNotEmpty())
                                ? parts[0]
                                : filenamePart.upToLastOccurrenceOf (".", false, false);

        outEntry.alias        = alias;
        outEntry.filename     = filenamePart;
        outEntry.voiceDir     = voiceDir;
        outEntry.leftBlank    = fAt (1);
        outEntry.fixedRange   = fAt (2);
        outEntry.rightBlank   = fAt (3);
        outEntry.preutterance = fAt (4);
        outEntry.overlap      = fAt (5);
        return true;
    }

    // Shift-JIS(cp932) / UTF-8(BOM) / UTF-8 / latin-1 の順で試す Python版 (_read_safe)
    // と同じ方針。実体は TextEncoding.h の decodeAutoEncoding に委譲する
    // (UTF-8として厳密に妥当か検証 → だめならOS標準APIでCP932変換 → 最終手段でLatin-1素通し)。
    static juce::String readSafe (const juce::File& file)
    {
        juce::MemoryBlock raw;
        if (! file.loadFileAsData (raw))
            return {};

        return vose_text::decodeAutoEncoding (raw.getData(), raw.getSize());
    }

    std::map<juce::String, OtoEntryCpp> db;
};
