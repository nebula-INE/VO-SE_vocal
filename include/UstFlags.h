// UstFlags.h
//
// UST の Flags フィールド（例: "g-5B50t20"）をパースする。
//
// 【重要な注意】UTAU の Flags はリサンプラー実装依存で、厳密な標準規格が
// 存在しない。ここでは最も広く使われている慣例に基づいて以下をサポートする:
//   g : Gender（フォルマント/ジェンダーシフト）。慣例的に -100〜100 の整数。
//       vose_core の gender_curve は 0.0〜1.0（0.5=ニュートラル）規約なので
//       gender01 = 0.5 + g/200.0 に変換する（±100 で ±0.5 動く）。
//   B : Breathiness（息成分）。慣例的に 0〜100。
//       breath_curve も 0.0〜1.0 規約なので breath01 = B/100.0。
//   t : Pitch shift（音程シフト）。UTAUでは10cent単位なので、t20 = +200cent。
// 該当する文字が無ければ、そのパラメータはUST側からの指定なし
// （呼び出し側でAPVTSのグローバル値にフォールバックする）とみなす。

#pragma once

#include <juce_core/juce_core.h>
#include <optional>

struct UstFlagOverrides
{
    std::optional<double> gender01;
    std::optional<double> tension01;
    std::optional<double> breath01;
    std::optional<double> pitchShiftCents;
};

inline UstFlagOverrides parseUstFlags (const juce::String& flags)
{
    UstFlagOverrides result;
    if (flags.isEmpty())
        return result;

    // UTAUのFlagsは "文字(符号付き数値)" の連続で、区切り文字が無い
    // (例: "g-5B50t20" = g=-5, B=50, t=20)。1文字ずつ見て、
    // アルファベットなら新しいフラグ開始、数字/マイナスなら値の続きとして読む。
    int i = 0;
    const int n = flags.length();
    while (i < n)
    {
        // 名前空間 juce:: を明示的に追加して解決
        const juce::juce_wchar letter = flags[i];
        if (! juce::CharacterFunctions::isLetter (letter))
        {
            ++i; // 不明な区切り文字はスキップ
            continue;
        }

        int j = i + 1;
        while (j < n && (flags[j] == '-' || flags[j] == '+' || juce::CharacterFunctions::isDigit (flags[j])))
            ++j;

        if (j > i + 1) // 数値部分が取れた場合のみ有効なフラグとして扱う
        {
            const double value = flags.substring (i + 1, j).getDoubleValue();

            if (letter == 'g')
                result.gender01 = juce::jlimit (0.0, 1.0, 0.5 + value / 200.0);
            else if (letter == 'B')
                result.breath01 = juce::jlimit (0.0, 1.0, value / 100.0);
            else if (letter == 't')
                result.pitchShiftCents = value * 10.0;
            // 他の文字(H, Y, A 等、エンジン固有フラグ)は現状未対応。無視する。
        }

        i = j;
    }

    return result;
}
