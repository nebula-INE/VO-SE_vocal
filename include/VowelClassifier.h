// VowelClassifier.h
//
// modules/audio/vcv_resolver.py の VowelClassifier 移植（文字ベース判定のみ）。
//
// 【意図的に省いた部分】
// Python版は pyopenjtalk が使える環境で g2p による末尾音素推定を優先する
// (_use_g2p パス)。これはPython専用の辞書付き重量ライブラリで、C++側に
// 持ち込むには roadmapの「pyopenjtalk辞書の扱い」で触れた
// libopenjtalk の静的リンクが必要になる。フェーズ2の現段階では
// 文字ベースの _VOWEL_MAP フォールバックのみを移植し、g2p相当の精度向上は
// 別タスクとして持ち越す。
//
// 移植した部分（_VOWEL_MAP + 促音スキップ）はPython版と1:1で対応するので、
// ひらがな/カタカナの通常の歌詞であれば判定結果は完全に一致するはず。

#pragma once

#include <juce_core/juce_core.h>
#include <map>
#include <regex>
#include <string>

class VowelClassifier
{
public:
    VowelClassifier() { buildMap(); }

    // 歌詞文字列の末尾音素を母音ラベル("a"/"i"/"u"/"e"/"o"/"n")に変換。
    // 判定不能（記号・漢字・空文字等）なら空文字を返す。
    // Python版と同じく、促音「っ/ッ」は末尾ならスキップして一つ前を見る。
    juce::String trailingVowel (const juce::String& lyric) const
    {
        if (lyric.isEmpty())
            return {};

        std::string clean (lyric.toRawUTF8());
        static const std::regex prefix (R"(^[-aieuon_]\s*)", std::regex::icase);
        static const std::regex pitch (R"(_?[A-Ga-g][#b]?[0-9]$)");
        clean = std::regex_replace (clean, prefix, "");
        clean = std::regex_replace (clean, pitch, "");
        if (clean.empty())
            return {};

        const auto normalized = juce::String::fromUTF8 (clean.c_str());
        for (int i = normalized.length() - 1; i >= 0; --i)
        {
            const juce::juce_wchar ch = normalized[i];
            if (ch == (juce::juce_wchar) 0x3063 || ch == (juce::juce_wchar) 0x30C3
                || ch == (juce::juce_wchar) 0x30FC)
                continue;

            const auto it = vowelMap.find (ch);
            if (it != vowelMap.end())
                return it->second;

            break;
        }
        return {};
    }

private:
    // C++20対策：第一引数の型を const char8_t* に変更
    void addGroup (const char8_t* utf8Chars, const char* label)
    {
        // reinterpret_cast を噛ませて juce::String::fromUTF8 に安全に渡す
        const auto s = juce::String::fromUTF8 (reinterpret_cast<const char*>(utf8Chars));
        for (auto ch : s)
            vowelMap[ch] = label;
    }

    void buildMap()
    {
        // _build_vowel_map() の groups とバイト単位で同一内容。
        addGroup (u8"あかさたなはまやらわがざだばぱぁゃ"
                  u8"アカサタナハマヤラワガザダバパァャ", "a");
        addGroup (u8"いきしちにひみりぎじぢびぴぃ"
                  u8"イキシチニヒミリギジヂビピィ", "i");
        addGroup (u8"うくすつぬふむゆるぐずづぶぷぅゅ"
                  u8"ウクスツヌフムユルグズヅブプゥュ", "u");
        addGroup (u8"えけせてねへめれげぜでべぺぇ"
                  u8"エケセテネヘメレゲゼデベペェ", "e");
        addGroup (u8"おこそとのほもよろをごぞどぼぽぉょ"
                  u8"オコソトノホモヨロヲゴゾドボポォョ", "o");
        addGroup (u8"んン", "n");
    }

    // 名前空間を明示
    std::map<juce::juce_wchar, juce::String> vowelMap;
};
