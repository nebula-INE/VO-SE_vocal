// PianoRollBridge.h
//
// PianoRollComponent（PianoRollNote のリストだけを扱う）と
// VoseAudioProcessor（ScheduledSongNote のリストで再生スケジューリングする）
// の間の変換をここに閉じ込める。どちらのクラスもお互いを知らずに済むようにする。
//
// 注意: ScheduledSongNote が持つ flags / vibrato / pbs,pbw,pby / preUtteranceMs /
// overlapMs は UST 由来の詳細パラメータで、ピアノロールでは編集できない
// （フェーズ3の対象はノートの追加・削除・移動・リサイズ・歌詞のみ）。
// 既存ノートを編集した場合はこれらの値を保持し、ピアノロールから新規作成した
// ノートについてはデフォルト値（UTAUのFlagsなし・ビブラートなし）を使う。
// カーブ編集（Pitch/Gender/Tension/Breath）はグラフエディタ側の担当。

#pragma once

#include "PianoRollNote.h"
#include "UstProject.h"
#include <vector>
#include <unordered_map>
#include <algorithm>
#include <cmath>

namespace PianoRollBridge
{
    // ScheduledSongNote -> PianoRollNote への変換。
    // id は「配列インデックス+1」を仮のIDとして振る（ScheduledSongNoteに
    // 永続IDが無いため）。toScheduledSongNotes() 側で同じ対応表を使って
    // 詳細パラメータを復元する。
    inline std::vector<PianoRollNote> fromScheduledSongNotes (const std::vector<ScheduledSongNote>& src)
    {
        std::vector<PianoRollNote> out;
        out.reserve (src.size());

        int64_t id = 1;
        for (auto& sn : src)
        {
            PianoRollNote n;
            n.id = id++;
            n.startTimeSec = sn.startTimeSec;
            n.durationSec  = juce::jmax (0.01, sn.durationSec);
            n.noteNum      = sn.noteNum;
            n.lyric        = sn.lyric;
            n.velocity     = juce::jlimit (0, 127, (int) std::lround (sn.velocity01 * 127.0));
            n.intensity   = juce::jlimit (0.0, 200.0, sn.intensity);
            n.modulation  = juce::jlimit (0.0, 100.0, sn.modulation);
            n.flags       = sn.flags;
            n.pbs = sn.pbs; n.pbw = sn.pbw; n.pby = sn.pby; n.pbm = sn.pbm;
            n.vibrato = sn.vibrato;
            n.genderOverride01 = sn.genderOverride01;
            n.tensionOverride01 = sn.tensionOverride01;
            n.breathOverride01 = sn.breathOverride01;
            n.preUtteranceMs = sn.preUtteranceMs;
            n.overlapMs = sn.overlapMs;
            out.push_back (n);
        }
        return out;
    }

    // PianoRollNote -> ScheduledSongNote への変換。
    // originalById が渡された場合、id が一致する既存ノートから
    // flags/vibrato/pbs等の詳細パラメータを引き継ぐ（UST読み込み後の
    // 編集で情報を失わないようにするため）。新規作成ノート（対応するidが
    // originalByIdに無い）はデフォルト値になる。
    inline std::vector<ScheduledSongNote> toScheduledSongNotes (
        const std::vector<PianoRollNote>& src,
        const std::unordered_map<int64_t, ScheduledSongNote>* originalById = nullptr)
    {
        std::vector<ScheduledSongNote> out;
        out.reserve (src.size());

        for (auto& n : src)
        {
            ScheduledSongNote sn;

            if (originalById != nullptr)
            {
                auto it = originalById->find (n.id);
                if (it != originalById->end())
                    sn = it->second; // 詳細パラメータを引き継いだ上で下で上書きする
            }

            sn.startTimeSec = n.startTimeSec;
            sn.durationSec  = n.durationSec;
            sn.noteNum      = n.noteNum;
            sn.lyric        = n.lyric;
            sn.velocity01   = juce::jlimit (0.0, 1.0, n.velocity / 127.0);
            sn.intensity    = juce::jlimit (0.0, 200.0, n.intensity);
            sn.modulation   = juce::jlimit (0.0, 100.0, n.modulation);
            sn.flags        = n.flags;
            sn.pbs = n.pbs; sn.pbw = n.pbw; sn.pby = n.pby; sn.pbm = n.pbm;
            sn.vibrato = n.vibrato;
            sn.genderOverride01 = n.genderOverride01;
            sn.tensionOverride01 = n.tensionOverride01;
            sn.breathOverride01 = n.breathOverride01;
            sn.preUtteranceMs = n.preUtteranceMs;
            sn.overlapMs = n.overlapMs;

            out.push_back (sn);
        }

        // startTimeSec 昇順を保証する（processBlock のスケジューラが前提としているため）。
        std::sort (out.begin(), out.end(),
                   [] (const ScheduledSongNote& a, const ScheduledSongNote& b)
                   { return a.startTimeSec < b.startTimeSec; });

        return out;
    }

    // fromScheduledSongNotes() で振ったidと元のScheduledSongNoteを対応付けるヘルパー。
    // 呼び出し側（PluginEditor）で fromScheduledSongNotes() と対にして使う。
    inline std::unordered_map<int64_t, ScheduledSongNote> buildOriginalIdMap (
        const std::vector<ScheduledSongNote>& src)
    {
        std::unordered_map<int64_t, ScheduledSongNote> map;
        int64_t id = 1;
        for (auto& sn : src)
            map[id++] = sn;
        return map;
    }
}
