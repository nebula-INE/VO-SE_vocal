// GraphEditorComponent.cpp

#include "GraphEditorComponent.h"
#include <algorithm>
#include <cmath>

GraphEditorComponent::GraphEditorComponent()
{
    setWantsKeyboardFocus (false);
    setOpaque (true);
    recalculateContentWidth();
}

//==============================================================================
void GraphEditorComponent::setMode (AutomationParam newMode)
{
    mode = newMode;
    draggingPointIndex = -1;
    hoverPointIndex = -1;
    repaint();
}

void GraphEditorComponent::setPenMode (bool enabled)
{
    penMode = enabled;
    penDragging = false;
    setMouseCursor (enabled ? juce::MouseCursor::CrosshairCursor : juce::MouseCursor::NormalCursor);
}

void GraphEditorComponent::setCurves (AutomationCurves newCurves)
{
    curves = std::move (newCurves);
    curves.sortAll();
    recalculateContentWidth();
    repaint();
}

void GraphEditorComponent::setTempo (double bpm)
{
    tempoBpm = juce::jmax (1.0, bpm);
    repaint();
}

void GraphEditorComponent::setHorizontalZoom (double newPixelsPerSecond)
{
    pixelsPerSecond = juce::jlimit (10.0, 800.0, newPixelsPerSecond);
    recalculateContentWidth();
    repaint();
}

void GraphEditorComponent::setViewHeight (int newHeight)
{
    setSize (getWidth(), juce::jmax (60, newHeight));
    repaint();
}

void GraphEditorComponent::setPlayheadSecondsProvider (std::function<double()> provider)
{
    playheadProvider = std::move (provider);
    if (playheadProvider != nullptr)
        startTimerHz (30);
    else
        stopTimer();
}

//==============================================================================
double GraphEditorComponent::xToTimeSec (float x) const
{
    return juce::jmax (0.0, (double) x / pixelsPerSecond);
}

float GraphEditorComponent::timeSecToX (double t) const
{
    return (float) (t * pixelsPerSecond);
}

double GraphEditorComponent::yToValue (float y, AutomationParam forMode) const
{
    const double h = (double) getHeight();
    if (forMode == AutomationParam::pitch)
    {
        const double centerY = h / 2.0;
        const double rangeY = centerY * 0.8;
        const double val = -((y - centerY) / rangeY) * AutomationRanges::kPitchMax;
        return juce::jlimit (AutomationRanges::kPitchMin, AutomationRanges::kPitchMax, val);
    }
    const double val = (h - (double) y - (h * 0.1)) / (h * 0.8);
    return juce::jlimit (AutomationRanges::kNormMin, AutomationRanges::kNormMax, val);
}

float GraphEditorComponent::valueToY (double value, AutomationParam forMode) const
{
    const double h = (double) getHeight();
    if (forMode == AutomationParam::pitch)
    {
        const double centerY = h / 2.0;
        return (float) (centerY - (value / AutomationRanges::kPitchMax) * (centerY * 0.8));
    }
    return (float) (h - (value * (h * 0.8) + (h * 0.1)));
}

juce::Colour GraphEditorComponent::colourFor (AutomationParam p) const
{
    switch (p)
    {
        case AutomationParam::pitch:   return juce::Colour (0xff00ff7f);
        case AutomationParam::gender:  return juce::Colour (0xffe74c3c);
        case AutomationParam::tension: return juce::Colour (0xff2ecc71);
        case AutomationParam::breath:  return juce::Colour (0xfff1c40f);
    }
    return juce::Colours::white;
}

juce::String GraphEditorComponent::labelFor (AutomationParam p) const
{
    switch (p)
    {
        case AutomationParam::pitch:   return "Pitch";
        case AutomationParam::gender:  return "Gender";
        case AutomationParam::tension: return "Tension";
        case AutomationParam::breath:  return "Breath";
    }
    return {};
}

void GraphEditorComponent::recalculateContentWidth()
{
    double latestEnd = 30.0;
    for (auto* c : { &curves.pitch, &curves.gender, &curves.tension, &curves.breath })
        if (! c->empty())
            latestEnd = juce::jmax (latestEnd, c->back().time);

    const int width = (int) ((latestEnd + 4.0) * pixelsPerSecond);
    setSize (juce::jmax (width, getParentWidth()), getHeight() > 0 ? getHeight() : 180);
}

void GraphEditorComponent::notifyChanged()
{
    if (onCurvesChanged != nullptr)
        onCurvesChanged (curves);
}

//==============================================================================
void GraphEditorComponent::paint (juce::Graphics& g)
{
    const auto bounds = getLocalBounds();
    const double h = (double) bounds.getHeight();
    auto& lf = getLookAndFeel();
    g.fillAll (lf.findColour (VoseColourIds::canvasBackground));

    // --- 拍/小節グリッド（ピアノロールと視覚的に揃えるため同じテンポを使う） ---
    const double secPerBeat = 60.0 / tempoBpm;
    const double totalSec = xToTimeSec ((float) bounds.getWidth());
    int beatIndex = 0;
    for (double t = 0.0; t <= totalSec; t += secPerBeat, ++beatIndex)
    {
        const float x = timeSecToX (t);
        g.setColour ((beatIndex % 4 == 0) ? lf.findColour (VoseColourIds::canvasGridMeasure)
                                           : lf.findColour (VoseColourIds::canvasGrid));
        g.drawVerticalLine ((int) x, 0.0f, (float) bounds.getHeight());
    }

    // --- Pitchモード時の中心線 ---
    if (mode == AutomationParam::pitch)
    {
        g.setColour (lf.findColour (VoseColourIds::canvasGridBeat));
        float dashLengths[] = { 4.0f, 4.0f };
        juce::Line<float> centerLine (0.0f, (float) (h / 2.0), (float) bounds.getWidth(), (float) (h / 2.0));
        g.drawDashedLine (centerLine, dashLengths, 2, 1.0f);
    }

    // --- ゴーストカーブ（編集中でない他のパラメータを薄く重ねる） ---
    for (auto p : { AutomationParam::pitch, AutomationParam::gender, AutomationParam::tension, AutomationParam::breath })
    {
        if (p == mode)
            continue;
        const auto& pts = curves.curveFor (p);
        if (pts.size() < 2)
            continue;

        juce::Path path;
        path.startNewSubPath (timeSecToX (pts.front().time), valueToY (pts.front().value, p));
        for (size_t i = 1; i < pts.size(); ++i)
            path.lineTo (timeSecToX (pts[i].time), valueToY (pts[i].value, p));

        g.setColour (colourFor (p).withAlpha (0.12f));
        g.strokePath (path, juce::PathStrokeType (1.0f));
    }

    // --- メインカーブ（現在のモード） ---
    const auto& pts = curves.curveFor (mode);
    const auto mainColour = colourFor (mode);

    if (pts.size() >= 2)
    {
        juce::Path path;
        path.startNewSubPath (timeSecToX (pts.front().time), valueToY (pts.front().value, mode));
        for (size_t i = 1; i < pts.size(); ++i)
            path.lineTo (timeSecToX (pts[i].time), valueToY (pts[i].value, mode));

        g.setColour (mainColour);
        g.strokePath (path, juce::PathStrokeType (2.0f));
    }

    for (size_t i = 0; i < pts.size(); ++i)
    {
        const float px = timeSecToX (pts[i].time);
        const float py = valueToY (pts[i].value, mode);
        const bool isHover = ((int) i == hoverPointIndex);
        const float radius = isHover ? kPointRadiusHover : kPointRadius;

        if (isHover)
        {
            g.setColour (juce::Colours::white);
            g.fillEllipse (px - radius, py - radius, radius * 2.0f, radius * 2.0f);
            g.setColour (mainColour);
            g.drawEllipse (px - radius, py - radius, radius * 2.0f, radius * 2.0f, 2.0f);
        }
        else
        {
            g.setColour (mainColour);
            g.fillEllipse (px - radius, py - radius, radius * 2.0f, radius * 2.0f);
        }
    }

    // --- 再生ヘッド ---
    if (playheadProvider != nullptr)
    {
        const double posSec = playheadProvider();
        const float x = timeSecToX (posSec);
        g.setColour (juce::Colours::red);
        g.drawVerticalLine ((int) x, 0.0f, (float) bounds.getHeight());
    }

    // --- モード/ペン状態ラベル（左上に小さく表示） ---
    g.setColour (mainColour);
    g.setFont (13.0f);
    g.drawText (labelFor (mode) + (penMode ? "  [Pen]" : ""), 6, 4, 200, 18, juce::Justification::left);
}

void GraphEditorComponent::resized()
{
}

//==============================================================================
int GraphEditorComponent::hitTestPoint (juce::Point<float> pos) const
{
    const auto& pts = curves.curveFor (mode);
    for (int i = (int) pts.size() - 1; i >= 0; --i)
    {
        const float px = timeSecToX (pts[(size_t) i].time);
        const float py = valueToY (pts[(size_t) i].value, mode);
        juce::Rectangle<float> hitBox (px - kHitTestPx, py - kHitTestPx, kHitTestPx * 2.0f, kHitTestPx * 2.0f);
        if (hitBox.contains (pos))
            return i;
    }
    return -1;
}

void GraphEditorComponent::addOrReplacePoint (double timeSec, double value)
{
    auto& pts = curves.curveFor (mode);
    pts.erase (std::remove_if (pts.begin(), pts.end(),
                                [timeSec] (const AutomationPoint& p)
                                { return std::abs (p.time - timeSec) < kSameTimeEpsilonSec; }),
               pts.end());
    pts.push_back ({ timeSec, value });
}

void GraphEditorComponent::addOrUpdatePenPoint (juce::Point<float> pos)
{
    const double t = xToTimeSec (pos.x);
    const double v = yToValue (pos.y, mode);
    addOrReplacePoint (t, v);
}

//==============================================================================
void GraphEditorComponent::mouseDown (const juce::MouseEvent& event)
{
    const auto pos = event.position;

    if (penMode && event.mods.isLeftButtonDown())
    {
        addOrUpdatePenPoint (pos);
        penDragging = true;
        lastPenPos = pos;
        recalculateContentWidth();
        repaint();
        return;
    }

    if (event.mods.isRightButtonDown())
    {
        const int idx = hitTestPoint (pos);
        if (idx >= 0)
        {
            curves.curveFor (mode).erase (curves.curveFor (mode).begin() + idx);
            notifyChanged();
            repaint();
        }
        return;
    }

    if (event.getNumberOfClicks() >= 2 && event.mods.isLeftButtonDown())
    {
        const double t = xToTimeSec (pos.x);
        const double v = yToValue (pos.y, mode);
        addOrReplacePoint (t, v);
        std::sort (curves.curveFor (mode).begin(), curves.curveFor (mode).end());
        recalculateContentWidth();
        notifyChanged();
        repaint();
        return;
    }

    draggingPointIndex = hitTestPoint (pos);
    repaint();
}

void GraphEditorComponent::mouseDrag (const juce::MouseEvent& event)
{
    const auto pos = event.position;

    if (penMode && penDragging)
    {
        if (pos.getDistanceFrom (lastPenPos) >= kPenIntervalPx)
        {
            addOrUpdatePenPoint (pos);
            lastPenPos = pos;
            recalculateContentWidth();
            notifyChanged();
            repaint();
        }
        return;
    }

    if (draggingPointIndex >= 0 && draggingPointIndex < (int) curves.curveFor (mode).size())
    {
        auto& pts = curves.curveFor (mode);
        pts[(size_t) draggingPointIndex].time = juce::jmax (0.0, xToTimeSec (pos.x));
        pts[(size_t) draggingPointIndex].value = yToValue (pos.y, mode);
        recalculateContentWidth();
        notifyChanged(); // Python版も移動中は毎回 parameters_changed を emit するため踏襲
        repaint();
    }
}

void GraphEditorComponent::mouseUp (const juce::MouseEvent&)
{
    if (penMode && penDragging)
    {
        penDragging = false;
        std::sort (curves.curveFor (mode).begin(), curves.curveFor (mode).end());
        notifyChanged();
        repaint();
        return;
    }

    if (draggingPointIndex >= 0)
    {
        std::sort (curves.curveFor (mode).begin(), curves.curveFor (mode).end());
        draggingPointIndex = -1;
        notifyChanged();
        repaint();
    }
}

void GraphEditorComponent::mouseMove (const juce::MouseEvent& event)
{
    if (draggingPointIndex < 0 && ! (penMode && penDragging))
    {
        const int idx = hitTestPoint (event.position);
        if (idx != hoverPointIndex)
        {
            hoverPointIndex = idx;
            repaint();
        }
    }
}

void GraphEditorComponent::timerCallback()
{
    if (playheadProvider != nullptr)
        repaint();
}
