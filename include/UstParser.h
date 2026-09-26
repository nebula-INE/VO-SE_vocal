// UstParser.h
//
// modules/data/ust_parser.py の UstParser / UstConverter.to_note_dicts の移植。
// セクション [#XXXX] ごとに key=value を集め、セクション境界でフラッシュする
// という構造をそのまま踏襲している。

#pragma once

#include "UstProject.h"
#include "TextEncoding.h"
#include <map>

class UstParser
{
public:
    // .ust を読んでパースする。失敗時（ファイルが無い等）は空のプロジェクトを返す。
    UstProject load (const juce::File& file)
    {
        UstProject project;
        if (! file.existsAsFile())
        {
            juce::Logger::writeToLog ("UstParser: ファイルが見つかりません: " + file.getFullPathName());
            return project;
        }

        juce::MemoryBlock raw;
        file.loadFileAsData (raw);
        // Python版は cp932→utf-8-sig→utf-8→latin-1 の順だが、
        // TextEncoding.h は「UTF-8として厳密に妥当か」を先に検証するため、
        // 実際のCP932バイト列がUTF-8として誤認されることは通常無く、
        // 実用上は同じ結果になる。
        const auto content = vose_text::decodeAutoEncoding (raw.getData(), raw.getSize());

        parse (content, project);
        return project;
    }

    // UstConverter.to_note_dicts 相当: ticks→秒変換した絶対時刻つきノート列を返す。
    // 休符("R")はスキップせず lyric="R" のまま返す（呼び出し側で判定させる）。
    static std::vector<ScheduledSongNote> toScheduledNotes (const UstProject& project)
    {
        std::vector<ScheduledSongNote> results;
        results.reserve (project.notes.size());

        double currentTimeSec = 0.0;
        for (const auto& n : project.notes)
        {
            const double beats = (double) n.length / (double) kUstTicksPerBeat;
            const double durationSec = beats * (60.0 / juce::jmax (1.0, n.tempo));

            ScheduledSongNote sn;
            sn.startTimeSec = currentTimeSec;
            sn.durationSec  = durationSec;
            sn.noteNum      = n.noteNum;
            sn.lyric        = n.lyric;
            sn.velocity01   = juce::jlimit (0.0, 1.0, n.intensity / 200.0);
            sn.intensity    = juce::jlimit (0.0, 200.0, n.intensity);
            sn.modulation   = juce::jlimit (0.0, 100.0, n.modulation);
            sn.flags        = n.flags;
            sn.preUtteranceMs = n.preUtterance;
            sn.overlapMs      = n.overlap;
            sn.pbs = n.pbs;
            sn.pbw = n.pbw;
            sn.pby = n.pby;
            sn.pbm = n.pbm;
            sn.vibrato = n.vibrato;

            results.push_back (sn);
            currentTimeSec += durationSec;
        }
        return results;
    }

    // PBS/PBW/PBYからのピッチカーブ生成は PitchCurveBuilder.h に実装している
    // (vose_pitch::buildPitchCurveHz)。ここでは生データ(pbs/pbw/pby/vibrato)を
    // ScheduledSongNote にそのまま保持するだけに留める。

private:
    void parse (const juce::String& content, UstProject& project)
    {
        juce::String currentSection;
        bool haveSection = false;
        std::map<juce::String, juce::String> currentBlock;
        double currentTempo = kUstDefaultTempo;

        auto flush = [&] ()
        {
            if (! haveSection)
                return;
            flushBlock (project, currentSection, currentBlock, currentTempo);
            auto it = currentBlock.find ("Tempo");
            if (it != currentBlock.end())
                currentTempo = it->second.getDoubleValue();
        };

        for (auto rawLine : juce::StringArray::fromLines (content))
        {
            const auto line = rawLine.trim();
            if (line.isEmpty())
                continue;

            if (line.startsWith ("[#") && line.endsWith ("]"))
            {
                flush();
                currentSection = line.substring (2, line.length() - 1);
                haveSection = true;
                currentBlock.clear();
                continue;
            }

            const int eq = line.indexOfChar ('=');
            if (eq > 0)
                currentBlock[line.substring (0, eq).trim()] = line.substring (eq + 1);
        }
        flush();
    }

    static void flushBlock (UstProject& project, const juce::String& section,
                             const std::map<juce::String, juce::String>& block, double currentTempo)
    {
        const auto upper = section.toUpperCase();

        if (upper == "SETTING")
        {
            applySetting (project, block);
            return;
        }
        if (upper == "PREV" || upper == "NEXT" || upper == "TRACKEND")
            return; // 特殊セクションはスキップ（Python版と同じ）

        // [#0000] 等のノートセクション。16進4桁 or 10進として解釈を試みる。
        int index = 0;
        bool parsed = false;
        if (section.length() == 4)
        {
            index = section.getHexValue32();
            parsed = true;
        }
        else
        {
            index = section.getIntValue();
            parsed = section.containsOnly ("0123456789-");
        }
        if (! parsed)
            return;

        if (auto note = parseNote (index, block, currentTempo))
            project.notes.push_back (*note);
    }

    static void applySetting (UstProject& project, const std::map<juce::String, juce::String>& block)
    {
        auto get = [&] (const char* key) -> const juce::String*
        {
            auto it = block.find (key);
            return it != block.end() ? &it->second : nullptr;
        };

        if (auto* v = get ("Tempo"))       project.tempo = v->getDoubleValue();
        if (auto* v = get ("ProjectName")) project.projectName = *v;
        if (auto* v = get ("OutFile"))     project.outputFile = *v;
        if (auto* v = get ("VoiceDir"))    project.voiceDir = *v;
        if (auto* v = get ("CacheDir"))    project.cacheDir = *v;
        if (auto* v = get ("Flags"))       project.flags = *v;
        if (auto* v = get ("Mode2"))       project.isMode2 = v->trim().equalsIgnoreCase ("True");
    }

    static std::optional<UstNote> parseNote (int index, const std::map<juce::String, juce::String>& block,
                                              double currentTempo)
    {
        auto get = [&] (const char* key) -> const juce::String*
        {
            auto it = block.find (key);
            return it != block.end() ? &it->second : nullptr;
        };

        auto* lengthStr = get ("Length");
        auto* noteNumStr = get ("NoteNum");
        if (lengthStr == nullptr || noteNumStr == nullptr)
            return std::nullopt; // 不完全なブロックは無視（Python版と同じ）

        UstNote n;
        n.index    = index;
        n.length   = lengthStr->getIntValue();
        n.noteNum  = noteNumStr->getIntValue();
        n.lyric    = get ("Lyric") != nullptr ? *get ("Lyric") : juce::String ("R");
        n.tempo    = (get ("Tempo") != nullptr) ? get ("Tempo")->getDoubleValue() : currentTempo;
        n.intensity  = (get ("Intensity")  != nullptr) ? get ("Intensity")->getDoubleValue()  : 100.0;
        n.modulation = (get ("Modulation") != nullptr) ? get ("Modulation")->getDoubleValue() : 100.0;
        n.flags = get ("Flags") != nullptr ? *get ("Flags") : juce::String();
        n.pbs = get ("PBS") != nullptr ? *get ("PBS") : juce::String();
        n.pbw = get ("PBW") != nullptr ? *get ("PBW") : juce::String();
        n.pby = get ("PBY") != nullptr ? *get ("PBY") : juce::String();
        n.pbm = get ("PBM") != nullptr ? *get ("PBM") : juce::String();

        if (auto* v = get ("PreUtterance"); v != nullptr && v->isNotEmpty())
            n.preUtterance = v->getDoubleValue();
        if (auto* v = get ("VoiceOverlap"); v != nullptr && v->isNotEmpty())
            n.overlap = v->getDoubleValue();

        if (auto* vbr = get ("VBR"))
        {
            auto parts = juce::StringArray::fromTokens (*vbr, ",", "");
            auto at = [&] (int i, double def) -> double
            {
                return (i < parts.size() && parts[i].isNotEmpty()) ? parts[i].getDoubleValue() : def;
            };

            UstVibratoParams vp;
            vp.length  = at (0, 0.0);
            vp.cycle   = at (1, 160.0);
            vp.depth   = at (2, 35.0);
            vp.fadeIn  = at (3, 20.0);
            vp.fadeOut = at (4, 20.0);
            vp.phase   = at (5, 0.0);
            vp.height  = at (6, 0.0);
            n.vibrato = vp;
        }

        return n;
    }
};
