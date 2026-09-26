// PianoRollNote.h
// Piano-roll editing representation. UST expression data is carried through
// edits so loading/editing/exporting a UST does not silently discard it.

#pragma once

#include <juce_core/juce_core.h>
#include <cstdint>
#include <optional>
#include "UstProject.h"

struct PianoRollNote
{
    int64_t id = 0;
    double startTimeSec = 0.0;
    double durationSec = 0.5;
    int noteNum = 60;
    juce::String lyric = "a";
    int velocity = 100;
    bool selected = false;

    // UST expression/detail data preserved across piano-roll edits.
    double intensity = 100.0;
    double modulation = 100.0;
    juce::String flags;
    juce::String pbs, pbw, pby, pbm;
    std::optional<UstVibratoParams> vibrato;
    std::optional<double> genderOverride01;
    std::optional<double> tensionOverride01;
    std::optional<double> breathOverride01;
    std::optional<double> preUtteranceMs;
    std::optional<double> overlapMs;

    double endTimeSec() const { return startTimeSec + durationSec; }
};
