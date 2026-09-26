// UstProject.h
//
// modules/data/ust_parser.py の dataclass 群 (UstVibratoParams / UstNote /
// UstProject) の1:1移植。フィールド名・デフォルト値ともPython版と揃えている。

#pragma once

#include <juce_core/juce_core.h>
#include <vector>
#include <optional>

// UTAUのデフォルト値
static constexpr double kUstDefaultTempo    = 120.0;
static constexpr int    kUstTicksPerBeat    = 480;

// VBR=length,cycle,depth,fade_in,fade_out,phase,height
struct UstVibratoParams
{
    double length   = 0.0;    // % (ノート長に対する割合)
    double cycle    = 160.0;  // ms
    double depth    = 35.0;   // cents
    double fadeIn   = 20.0;   // %
    double fadeOut  = 20.0;   // %
    double phase    = 0.0;    // 0-100
    double height   = 0.0;    // cents

    double depthSemitones() const { return depth / 100.0; }
    double rateHz() const { return cycle > 0.0 ? 1000.0 / cycle : 5.5; }
};

struct UstNote
{
    int    index = 0;
    int    length = 0;             // ticks
    juce::String lyric;            // ひらがな / "R"（休符）
    int    noteNum = 60;           // MIDIノート番号
    double tempo = kUstDefaultTempo;

    double intensity  = 100.0;     // 0-200
    double modulation = 100.0;     // 0-200
    juce::String flags;            // 例: "g-5B50"

    juce::String pbs, pbw, pby, pbm; // ポルタメント関連（生文字列のまま保持）

    std::optional<UstVibratoParams> vibrato;

    // 空(nullopt) = oto.ini の値を使う
    std::optional<double> preUtterance;
    std::optional<double> overlap;

    bool isRest() const { return lyric.trim().equalsIgnoreCase ("R"); }
};

struct UstProject
{
    juce::String version      { "UST Version 1.2" };
    juce::String projectName  { "Untitled" };
    juce::String outputFile;
    juce::String voiceDir;
    juce::String cacheDir;
    double       tempo = kUstDefaultTempo;
    juce::String flags;
    bool         isMode2 = false;

    std::vector<UstNote> notes;
};

// UstConverter.to_note_dicts に相当する1エントリ分。
// ticks→秒変換済みの「絶対開始時刻つきノート」で、これをそのまま
// PluginProcessor側のスケジューラが使う。
struct ScheduledSongNote
{
    double startTimeSec = 0.0;
    double durationSec  = 0.0;
    int    noteNum = 60;
    juce::String lyric;
    double velocity01 = 1.0;       // intensity(0-200) を 0-1 に正規化したもの
    double modulation = 100.0;     // UST Modulation (0-100)
    juce::String flags;            // 例: "g-5B50"。UstFlags.h でパースする

    // グラフエディタ(GraphEditorComponent)からの明示的な上書き。
    // 優先順位: これ > Flagsパース結果 > APVTSのグローバル値。
    // UstParser::toScheduledNotes からは設定されない（nullopt=未指定）。
    std::optional<double> genderOverride01;
    std::optional<double> tensionOverride01;
    std::optional<double> breathOverride01;

    // ポルタメント/ビブラート用の生データ。カーブそのものは
    // PitchCurveBuilder.h（呼び出し側）が組み立てる。
    juce::String pbs, pbw, pby;
    std::optional<UstVibratoParams> vibrato;

    std::optional<double> preUtteranceMs;
    std::optional<double> overlapMs;
};
