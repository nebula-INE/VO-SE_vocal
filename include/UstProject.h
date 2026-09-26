// UstProject.h
// UST parser/writer shared data structures.

#pragma once

#include <juce_core/juce_core.h>
#include <vector>
#include <optional>

static constexpr double kUstDefaultTempo = 120.0;
static constexpr int    kUstTicksPerBeat = 480;

struct UstVibratoParams
{
    double length = 0.0;
    double cycle = 160.0;
    double depth = 35.0;
    double fadeIn = 20.0;
    double fadeOut = 20.0;
    double phase = 0.0;
    double height = 0.0;
    double depthSemitones() const { return depth / 100.0; }
    double rateHz() const { return cycle > 0.0 ? 1000.0 / cycle : 5.5; }
};

struct UstNote
{
    int index = 0;
    int length = 0;
    juce::String lyric;
    int noteNum = 60;
    double tempo = kUstDefaultTempo;
    double intensity = 100.0;
    double modulation = 0.0;
    juce::String flags;
    juce::String pbs, pbw, pby, pbm;
    std::optional<UstVibratoParams> vibrato;
    std::optional<double> preUtterance;
    std::optional<double> overlap;
    bool isRest() const { return lyric.trim().equalsIgnoreCase ("R"); }
};

struct UstProject
{
    juce::String version { "UST Version 1.2" };
    juce::String projectName { "Untitled" };
    juce::String outputFile;
    juce::String voiceDir;
    juce::String cacheDir;
    double tempo = kUstDefaultTempo;
    juce::String flags;
    bool isMode2 = false;
    std::vector<UstNote> notes;
};

struct ScheduledSongNote
{
    double startTimeSec = 0.0;
    double durationSec = 0.0;
    int noteNum = 60;
    juce::String lyric;
    double velocity01 = 1.0;
    double intensity = 100.0;
    double modulation = 0.0;
    juce::String flags;

    std::optional<double> genderOverride01;
    std::optional<double> tensionOverride01;
    std::optional<double> breathOverride01;

    juce::String pbs, pbw, pby, pbm;
    std::optional<UstVibratoParams> vibrato;
    std::optional<double> preUtteranceMs;
    std::optional<double> overlapMs;
};
