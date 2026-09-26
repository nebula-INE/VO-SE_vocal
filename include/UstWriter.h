// UstWriter.h
//
// UstParser.h の逆方向（export_as_ust相当）。ScheduledSongNote（絶対秒）を
// UST形式のテキストへシリアライズする。
//
// 【重要な設計判断】USTフォーマットには「絶対開始時刻」というフィールドが
// 無く、各ノートのLengthを積み上げてタイムラインを表現する。そのため
// ノート間に空白（無音区間）がある場合、そのままLengthを並べただけでは
// 後ろのノートが前へ詰まってしまう。これを避けるため、ノート間に
// ギャップがあれば Lyric="R"（休符）のノートを自動挿入して長さを埋める。
//
// 【往復性(round-trip)について】UstParser -> UstWriter -> UstParser で
// 元のタイミング・歌詞・ポルタメント・ビブラート・Flagsはほぼ保持される
// （浮動小数点の丸め誤差を除く）。ただしIntensity/Modulationは
// ScheduledSongNoteが保持するIntensity/Modulationをそのまま書き出す
// （velocity01はあるが、これは元々UST Intensityから作った値なので
// 逆変換して書き戻している）。

#pragma once

#include "UstProject.h"
#include <juce_core/juce_core.h>

namespace UstWriter
{
    inline juce::String formatVbr (const UstVibratoParams& v)
    {
        return juce::String (v.length, 2) + "," + juce::String (v.cycle, 2) + "," + juce::String (v.depth, 2) + ","
             + juce::String (v.fadeIn, 2) + "," + juce::String (v.fadeOut, 2) + "," + juce::String (v.phase, 2) + ","
             + juce::String (v.height, 2);
    }

    inline bool write (const juce::File& outFile, const std::vector<ScheduledSongNote>& notesIn,
                        double tempoBpm, const juce::String& projectName)
    {
        if (tempoBpm <= 0.0)
            tempoBpm = kUstDefaultTempo;

        // 開始時刻順にソート（PianoRollComponent::commitToProcessor が既にソートしているはずだが念のため）
        auto notes = notesIn;
        std::sort (notes.begin(), notes.end(),
                   [] (const auto& a, const auto& b) { return a.startTimeSec < b.startTimeSec; });

        juce::String text;
        text << "[#VERSION]\n" << "UST Version 1.2\n";
        text << "[#SETTING]\n";
        text << "Tempo=" << juce::String (tempoBpm, 2) << "\n";
        text << "Tracks=1\n";
        text << "ProjectName=" << projectName << "\n";
        text << "Mode2=True\n";

        auto secToTicks = [&] (double sec) -> int
        {
            const double beats = sec * tempoBpm / 60.0;
            return juce::roundToInt (beats * (double) kUstTicksPerBeat);
        };

        auto writeNoteSection = [&] (int index, int lengthTicks, const juce::String& lyric,
                                      int noteNum, double intensity, const ScheduledSongNote* src)
        {
            text << "[#" << juce::String (index).paddedLeft ('0', 4) << "]\n";
            text << "Length=" << juce::String (juce::jmax (1, lengthTicks)) << "\n";
            text << "Lyric=" << lyric << "\n";
            text << "NoteNum=" << juce::String (noteNum) << "\n";
            const double storedIntensity = src != nullptr ? juce::jlimit (0.0, 200.0, src->intensity) : intensity;
            text << "Intensity=" << juce::String (storedIntensity, 1) << "\n";
            const double modulation = src != nullptr ? juce::jlimit (0.0, 100.0, src->modulation) : 0.0;
            text << "Modulation=" << juce::String (modulation, 1) << "\n";

            if (src != nullptr)
            {
                if (src->flags.isNotEmpty())
                    text << "Flags=" << src->flags << "\n";
                if (src->pbs.isNotEmpty()) text << "PBS=" << src->pbs << "\n";
                if (src->pbw.isNotEmpty()) text << "PBW=" << src->pbw << "\n";
                if (src->pby.isNotEmpty()) text << "PBY=" << src->pby << "\n";
                if (src->pbm.isNotEmpty()) text << "PBM=" << src->pbm << "\n";
                if (src->vibrato.has_value())
                    text << "VBR=" << formatVbr (*src->vibrato) << "\n";
                if (src->preUtteranceMs.has_value())
                    text << "PreUtterance=" << juce::String (*src->preUtteranceMs, 2) << "\n";
                if (src->overlapMs.has_value())
                    text << "VoiceOverlap=" << juce::String (*src->overlapMs, 2) << "\n";
            }
        };

        int index = 0;
        double expectedStartSec = 0.0;
        constexpr double kGapEpsilonSec = 0.001; // 丸め誤差を無視する閾値

        for (const auto& n : notes)
        {
            // ノート開始前に無音区間があれば休符ノートで埋める（USTは絶対時刻を持たないため）
            if (n.startTimeSec > expectedStartSec + kGapEpsilonSec)
            {
                const int restTicks = secToTicks (n.startTimeSec - expectedStartSec);
                writeNoteSection (index++, restTicks, "R", 60, 100.0, nullptr);
            }

            const int lengthTicks = secToTicks (n.durationSec);
            writeNoteSection (index++, lengthTicks, n.lyric, n.noteNum, n.intensity, &n);

            expectedStartSec = n.startTimeSec + n.durationSec;
        }

        text << "[#TRACKEND]\n";

        // UTF-8（BOM無し）で書き出す。TextEncoding.hの読み込み側はUTF-8を
        // 最優先で判定するため、自分のUstParserで読み戻す分には問題ない。
        // 他のUTAUツールとの互換性が必要な場合はCP932での書き出しに切り替えること。
        return outFile.replaceWithText (text, true, false, "\n");
    }
}
