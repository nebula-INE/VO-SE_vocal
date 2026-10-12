#include "AutomationCurves.h"

#include <cassert>
#include <cmath>
#include <iostream>

namespace
{
    bool near (double actual, double expected, double tolerance = 1.0e-12)
    {
        return std::abs (actual - expected) <= tolerance;
    }
}

int main()
{
    using AutomationRanges::pitchValueToSemitones;

    // Both ends of the editor's documented pitch range should map to +/-2 semitones.
    assert (near (pitchValueToSemitones (-8192.0), -2.0));
    assert (near (pitchValueToSemitones (0.0), 0.0));
    assert (near (pitchValueToSemitones (8191.0), 2.0));

    // Interior values use the appropriate side's endpoint, keeping the mapping continuous.
    assert (near (pitchValueToSemitones (-4096.0), -1.0));
    assert (near (pitchValueToSemitones (4095.5), 1.0));

    // Malformed/out-of-range automation values must not exceed the supported pitch range.
    assert (near (pitchValueToSemitones (-9000.0), -2.0));
    assert (near (pitchValueToSemitones (9000.0), 2.0));

    // Desktop's semitone-to-frequency ratio must match the Web bridge formula.
    using AutomationRanges::semitonesToFrequencyRatio;
    assert (near (semitonesToFrequencyRatio (-2.0), std::pow (2.0, -2.0 / 12.0)));
    assert (near (semitonesToFrequencyRatio (-1.0), std::pow (2.0, -1.0 / 12.0)));
    assert (near (semitonesToFrequencyRatio (0.0), 1.0));
    assert (near (semitonesToFrequencyRatio (1.0), std::pow (2.0, 1.0 / 12.0)));
    assert (near (semitonesToFrequencyRatio (2.0), std::pow (2.0, 2.0 / 12.0)));

    // Native automation sampling is step-hold: a point remains active until the next point.
    AutomationCurves curves;
    assert (! curves.evaluate (AutomationParam::pitch, 0.0).has_value());

    curves.pitch = {{0.25, -4096.0}, {0.75, 4095.5}};
    assert (near (*curves.evaluate (AutomationParam::pitch, 0.0), -4096.0));
    assert (near (*curves.evaluate (AutomationParam::pitch, 0.25), -4096.0));
    assert (near (*curves.evaluate (AutomationParam::pitch, 0.74), -4096.0));
    assert (near (*curves.evaluate (AutomationParam::pitch, 0.75), 4095.5));
    assert (near (*curves.evaluate (AutomationParam::pitch, 1.0), 4095.5));

    std::cout << "Desktop pitch automation boundary tests passed\n";
    return 0;
}
