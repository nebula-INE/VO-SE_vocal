// AutomationCurves.h
//
// modules/gui/graph_editor_widget.py の self.all_parameters（Pitch/Gender/Tension/
// Breathそれぞれの List[PitchEvent]）に相当するデータモデル。GraphEditorComponent
// はこの構造体だけを扱い、VoseAudioProcessor や ScheduledSongNote を知らない
// （PianoRollComponent と同じ設計方針）。
//
// evaluate() は Python版 GraphEditorWidget.get_value_at_time() と同じ
// "ステップホールド"（直近のブレークポイント値を次のポイントまで保持する）
// 方式でサンプリングする。画面上の折れ線描画は点同士を直線で結ぶが、
// 実際の値取得はステップホールドである点はPython版から意図的に踏襲している
// （元実装の仕様に忠実であるべきという判断。線形補間に変えたい場合は
// evaluate() だけを差し替えれば良い）。

#pragma once

#include "AutomationPoint.h"
#include <vector>
#include <algorithm>
#include <optional>

enum class AutomationParam
{
    pitch,
    gender,
    tension,
    breath
};

namespace AutomationRanges
{
    constexpr double kPitchMin = -8192.0;
    constexpr double kPitchMax = 8191.0;
    constexpr double kNormMin  = 0.0;
    constexpr double kNormMax  = 1.0;

    // Pitchオートメーションの値レンジ(-8192..8191)を半音オフセットに変換する際の
    // 可動域。DAW一般のピッチベンドレンジ(±2半音)に合わせた暫定値で、実際の
    // 音楽的要件に応じて調整してよい（PluginProcessor.cpp側で使用）。
    constexpr double kPitchAutomationSemitoneRange = 2.0;

    inline double pitchValueToSemitones (double value)
    {
        return (value / kPitchMax) * kPitchAutomationSemitoneRange;
    }
}

struct AutomationCurves
{
    std::vector<AutomationPoint> pitch;
    std::vector<AutomationPoint> gender;
    std::vector<AutomationPoint> tension;
    std::vector<AutomationPoint> breath;

    std::vector<AutomationPoint>& curveFor (AutomationParam p)
    {
        switch (p)
        {
            case AutomationParam::pitch:   return pitch;
            case AutomationParam::gender:  return gender;
            case AutomationParam::tension: return tension;
            case AutomationParam::breath:  return breath;
        }
        return pitch; // unreachable
    }

    const std::vector<AutomationPoint>& curveFor (AutomationParam p) const
    {
        return const_cast<AutomationCurves*> (this)->curveFor (p);
    }

    bool hasPoints (AutomationParam p) const { return ! curveFor (p).empty(); }

    void sortAll()
    {
        std::sort (pitch.begin(), pitch.end());
        std::sort (gender.begin(), gender.end());
        std::sort (tension.begin(), tension.end());
        std::sort (breath.begin(), breath.end());
    }

    // Python版 get_value_at_time() と同じステップホールド評価。
    // ポイントが1つも無ければ nullopt（呼び出し側でフォールバック値を決める）。
    std::optional<double> evaluate (AutomationParam p, double timeSec) const
    {
        const auto& events = curveFor (p);
        if (events.empty())
            return std::nullopt;

        double lastVal = events.front().value;
        for (auto& e : events)
        {
            if (e.time > timeSec)
                break;
            lastVal = e.value;
        }
        return lastVal;
    }
};
