// PitchCurveBuilder.h
//
// UST由来のポルタメント(PBS/PBW/PBY)とビブラート(VBR)を合成して、
// ノート1個分のピッチカーブ(Hz)を組み立てる。
//
// 【出自の違いに注意】
//   - PortamentoCurveBuilder: modules/data/ust_parser.py の
//     UstConverter.extract_portamento_curve() の移植（制御点構築ロジックは同一）。
//   - vibratoSemitonesAt(): ust_parser.py はVBRのパラメータを保持するだけで
//     カーブ生成自体は実装されていなかった（別モジュール任せだったと思われる）。
//     ここでは一般的なUTAU実装に準拠した標準的な計算式を新規に実装している
//     （＝Pythonからの移植ではない）。
//
// 【Python版との意図的な差分】
//   元の extract_portamento_curve は "total_width_ms / resolution" という
//   ノート長と無関係な独自の時間刻みでサンプルしていたが、ここでは
//   ノート全体のピッチカーブ（0〜durationMs、resolution点）と同じ絶対時刻軸で
//   制御点を評価し直している。そうしないとビブラートカーブや基準ピッチと
//   時間軸がズレて合成できないため。制御点そのものの構築ロジックは同一。

#pragma once

#include "UstProject.h"
#include <vector>
#include <cmath>
#include <optional>

namespace vose_pitch
{
    // 単純な線形補間（ust_parser.py の _interp() の移植）。範囲外はクランプ。
    inline double interpLinear (double x, const std::vector<double>& xs, const std::vector<double>& ys)
    {
        if (xs.empty())
            return 0.0;
        if (x <= xs.front())
            return ys.front();
        if (x >= xs.back())
            return ys.back();

        for (size_t i = 0; i + 1 < xs.size(); ++i)
        {
            if (xs[i] <= x && x <= xs[i + 1])
            {
                const double t = (x - xs[i]) / (xs[i + 1] - xs[i]);
                return ys[i] + t * (ys[i + 1] - ys[i]);
            }
        }
        return 0.0;
    }

    // PBS(開始オフセット;開始ピッチ) / PBW(区間幅msのCSV) / PBY(区間高さ10centのCSV)
    // から制御点列を作る。build()後に at(tMs) でセミトーンオフセットを取れる。
    struct PortamentoCurveBuilder
    {
        std::vector<double> cpTimes;
        std::vector<double> cpValues;
        std::vector<char> shapes;
        bool valid = false;

        void build (const juce::String& pbs, const juce::String& pbw, const juce::String& pby, const juce::String& pbm = {}, double durationMs = 0.0)
        {
            valid = false;
            if (pbw.trim().isEmpty())
                return;

            std::vector<double> widths;
            for (auto& w : juce::StringArray::fromTokens (pbw, ",", ""))
                if (w.trim().isNotEmpty())
                    widths.push_back (w.getDoubleValue());

            std::vector<double> heights;
            if (pby.isNotEmpty())
                for (auto& h : juce::StringArray::fromTokens (pby, ",", ""))
                    if (h.trim().isNotEmpty())
                        heights.push_back (h.getDoubleValue());

            double pbsOffsetMs = 0.0, pbsStartPitch = 0.0;
            auto pbsParts = juce::StringArray::fromTokens (pbs, ";", "");
            if (pbsParts.size() > 0 && pbsParts[0].trim().isNotEmpty())
                pbsOffsetMs = pbsParts[0].getDoubleValue();
            if (pbsParts.size() > 1 && pbsParts[1].trim().isNotEmpty())
                pbsStartPitch = pbsParts[1].getDoubleValue() * 0.1; // UTAU: 10cent単位 → semitone

            double totalWidthMs = 0.0;
            for (auto w : widths)
                totalWidthMs += w;
            if (totalWidthMs <= 0.0)
                return;

            cpTimes.clear();
            cpValues.clear();
            shapes.clear();
            cpTimes.push_back (pbsOffsetMs);
            cpValues.push_back (pbsStartPitch);

            juce::StringArray shapeText;
            shapeText.addTokens (pbm, ",", "");

            double t = pbsOffsetMs;
            for (size_t i = 0; i < widths.size(); ++i)
            {
                t += widths[i];
                const double h = (i < heights.size()) ? heights[i] * 0.1 : 0.0; // UTAU: 10cent単位
                cpTimes.push_back (t);
                cpValues.push_back (h);

                char shape = 0;
                if ((int) i < shapeText.size())
                {
                    const auto token = shapeText[(int) i].trim().toLowerCase();
                    if (token == "s") shape = "s"[0];
                    else if (token == "r") shape = "r"[0];
                    else if (token == "j") shape = "j"[0];
                }
                shapes.push_back (shape);
            }
            // PBWで定義された最後の点の後は、ノート終端を暗黙の0 semitone点とする。
            // 「+10ms」のマジック値はUST仕様にないため使用しない。
            const double endpointMs = durationMs > 0.0
                ? juce::jmax (pbsOffsetMs, durationMs)
                : (totalWidthMs + pbsOffsetMs);
            cpTimes.push_back (endpointMs);
            cpValues.push_back (0.0);

            char finalShape = 0;
            if ((int) widths.size() < shapeText.size())
            {
                const auto token = shapeText[(int) widths.size()].trim().toLowerCase();
                if (token == "s") finalShape = "s"[0];
                else if (token == "r") finalShape = "r"[0];
                else if (token == "j") finalShape = "j"[0];
            }
            shapes.push_back (finalShape);

            valid = true;
        }

        double at (double tMs) const
        {
            if (! valid || cpTimes.empty())
                return 0.0;
            if (tMs <= cpTimes.front())
                return cpValues.front();
            if (tMs >= cpTimes.back())
                return cpValues.back();

            for (size_t i = 0; i + 1 < cpTimes.size(); ++i)
            {
                if (tMs < cpTimes[i] || tMs > cpTimes[i + 1])
                    continue;
                const double span = cpTimes[i + 1] - cpTimes[i];
                const double u = span > 0.0 ? juce::jlimit (0.0, 1.0, (tMs - cpTimes[i]) / span) : 0.0;
                const char shape = i < shapes.size() ? shapes[i] : 0;
                double shaped = u;
                if (shape == "r"[0])
                    shaped = std::sin (juce::MathConstants<double>::pi * u / 2.0);
                else if (shape == "j"[0])
                    shaped = 1.0 - std::cos (juce::MathConstants<double>::pi * u / 2.0);
                else if (shape != "s"[0])
                    shaped = 0.5 - 0.5 * std::cos (juce::MathConstants<double>::pi * u);
                return cpValues[i] + shaped * (cpValues[i + 1] - cpValues[i]);
            }
            return 0.0;
        }
    };

    // 標準的なUTAUビブラート仕様に基づく新規実装（Pythonからの移植ではない）。
    //   - ビブラート区間: ノート末尾の length% の区間のみ
    //   - 区間内で cycle ms 周期のサイン波、振幅 depth cents
    //   - fade_in%/fade_out% で台形のエンベロープをかける
    //   - phase(0-100) で開始位相をずらす
    //   - height(cents) でビブラート中心を上下にオフセット
    inline double vibratoSemitonesAt (const UstVibratoParams& v, double tMs, double durationMs)
    {
        if (v.length <= 0.0 || durationMs <= 0.0)
            return 0.0;

        const double vibLenMs   = durationMs * (v.length / 100.0);
        const double vibStartMs = durationMs - vibLenMs;
        if (tMs < vibStartMs)
            return 0.0;

        const double tInVib = tMs - vibStartMs; // 0..vibLenMs

        const double fadeInMs  = vibLenMs * (v.fadeIn  / 100.0);
        const double fadeOutMs = vibLenMs * (v.fadeOut / 100.0);
        double env = 1.0;
        if (fadeInMs > 0.0 && tInVib < fadeInMs)
            env = tInVib / fadeInMs;
        if (fadeOutMs > 0.0 && tInVib > vibLenMs - fadeOutMs)
            env = juce::jmin (env, (vibLenMs - tInVib) / fadeOutMs);
        env = juce::jlimit (0.0, 1.0, env);

        const double cycleMs = v.cycle > 0.0 ? v.cycle : 160.0;
        const double phaseOffset = v.phase / 100.0; // 0-100 -> 0-1周期分
        const double cycles = (tInVib / cycleMs) + phaseOffset;
        const double raw = std::sin (2.0 * juce::MathConstants<double>::pi * cycles);

        const double cents = raw * v.depth * env + v.height;
        return cents / 100.0; // cents -> semitone
    }

    // ノート1個分の「ビブラートのみ」を焼き込んだピッチカーブ(Hz)を組み立てる。
    // resolution点、0〜durationMsを均等分割。
    //
    // 【ポルタメントはここに含めない】VoseStreamNote/NoteEventの両方に
    // portamento_offsets というネイティブフィールドが存在し、しかも簡略化されて
    // いない汎用的な処理（セントカーブをそのままF0へ掛けるだけ）なので、
    // ポルタメントは buildPortamentoCentsCurve() で別カーブとして作り、
    // ネイティブAPI経由で渡すこと。ここで焼き込むと二重適用になる。
    //
    // ビブラートは逆にネイティブ側(apply_vibrato)が「ノート後半50%固定・
    // フェードアウト無し・phase/height無し」という簡易モデルで、USTのVBR
    // (length/fade_in/fade_out/phase/height)を再現できないため、
    // 引き続きここで焼き込む（VoseStreamNoteにはビブラート用フィールドも無い）。
    inline std::vector<double> buildVibratoPitchCurveHz (int baseMidiNote, double durationMs,
                                                          const std::optional<UstVibratoParams>& vibrato,
                                                          int resolution)
    {
        std::vector<double> curve ((size_t) resolution);
        const int denom = juce::jmax (1, resolution - 1);

        for (int j = 0; j < resolution; ++j)
        {
            const double tMs = durationMs * ((double) j / (double) denom);

            double semitoneOffset = 0.0;
            if (vibrato.has_value())
                semitoneOffset = vibratoSemitonesAt (*vibrato, tMs, durationMs);

            const double midiNoteWithOffset = (double) baseMidiNote + semitoneOffset;
            curve[(size_t) j] = 440.0 * std::pow (2.0, (midiNoteWithOffset - 69.0) / 12.0);
        }
        return curve;
    }

    // PBS/PBW/PBYからセミトーンのポルタメントカーブを作り、セント単位に変換して返す。
    // vose_core の portamento_offsets はセント単位（cents = 100 * semitone）を
    // 期待している（vose_core.cpp: base_f0_val *= pow(2, cents/1200)）。
    // resolution・時間軸は buildVibratoPitchCurveHz と揃えること
    // （コア側は同じ pitch_length を使って resample_curve するため、
    //  両カーブの index が同じ絶対時刻を指している前提で合成される）。
    inline std::vector<double> buildPortamentoCentsCurve (const juce::String& pbs, const juce::String& pbw,
                                                           const juce::String& pby, const juce::String& pbm,
                                                           double durationMs,
                                                           int resolution)
    {
        std::vector<double> curve ((size_t) resolution, 0.0);

        PortamentoCurveBuilder portamento;
        portamento.build (pbs, pbw, pby, pbm, durationMs);
        if (! portamento.valid)
            return curve; // PBWが無い等 → オフセット無し（0セント）

        const int denom = juce::jmax (1, resolution - 1);
        for (int j = 0; j < resolution; ++j)
        {
            const double tMs = durationMs * ((double) j / (double) denom);
            curve[(size_t) j] = portamento.at (tMs) * 100.0; // semitone -> cents
        }
        return curve;
    }
}
