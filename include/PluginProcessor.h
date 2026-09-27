// PluginProcessor.h
// フェーズ4 PoC: マルチトラック(最大4)、buffer_ms調整、ホストトランスポート同期、
// UST書き出しをサポート。

#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include "VoseBridge.h"
#include "VoiceTrack.h"
#include "VowelClassifier.h"
#include "UstParser.h"
#include "UstFlags.h"
#include "PitchCurveBuilder.h"
#include "AutomationCurves.h"
#include <array>
#include <deque>

class VoseAudioProcessor : public juce::AudioProcessor
{
public:
    static constexpr int kMaxTracks = 4;
    static constexpr int kDefaultBufferMs = 500;
    static constexpr int kMinBufferMs = 100;
    static constexpr int kMaxBufferMs = 2000;

    VoseAudioProcessor();
    ~VoseAudioProcessor() override;

    void prepareToPlay (double sampleRate, int samplesPerBlock) override;
    void releaseResources() override;
    void processBlock (juce::AudioBuffer<float>&, juce::MidiBuffer&) override;

    juce::AudioProcessorEditor* createEditor() override;
    bool hasEditor() const override { return true; }

    const juce::String getName() const override { return "VO-SE"; }
    bool acceptsMidi() const override { return true; }
    bool producesMidi() const override { return false; }
    double getTailLengthSeconds() const override { return 0.0; }

    int getNumPrograms() override { return 1; }
    int getCurrentProgram() override { return 0; }
    void setCurrentProgram (int) override {}
    const juce::String getProgramName (int) override { return {}; }
    void changeProgramName (int, const juce::String&) override {}

    void getStateInformation (juce::MemoryBlock&) override {}
    void setStateInformation (const void*, int) override {}

    // --- 音源フォルダ（マルチトラック対応） ---
    // trackIndex省略時はトラック0（フェーズ3以前のUIとの後方互換用）。
    void loadVoiceDirectory (const juce::File& dir, int trackIndex = 0);
    int  getLoadedAliasCount (int trackIndex = 0) const;

    // トラックのミキサー設定
    void setTrackGain (int trackIndex, float linearGain);
    void setTrackPan (int trackIndex, float pan);
    void setTrackMuted (int trackIndex, bool muted);
    juce::String getTrackVoiceDirName (int trackIndex) const;

    // 優先3: 内蔵歌詞キューUI（生MIDIキーボード演奏用のフォールバック経路）。
    void setLyricSequence (const juce::String& spaceSeparatedText);
    juce::String getLyricSequenceText() const;

    // --- UST曲再生（優先1/3のライブMIDIとは独立した経路） ---
    bool loadUstFile (const juce::File& ustFile);
    void startSongPlayback();  // 曲頭から再生開始（内部クロックモード時のみ意味を持つ）
    void stopSongPlayback();
    bool isSongPlaying() const { return songPlaying.load(); }
    int  getLoadedSongNoteCount() const { const juce::SpinLock::ScopedLockType sl (songNotesLock); return (int) songNotes.size(); }

    void setEditedNotes (std::vector<ScheduledSongNote> notes);
    std::vector<ScheduledSongNote> getSongNotesSnapshot() const
    {
        const juce::SpinLock::ScopedLockType sl (songNotesLock);
        return songNotes;
    }

    double getCurrentTempo() const { return currentTempoBpm; }

    // ピアノロールのカーソル表示用。songPositionSecはaudio threadが書き込むが、
    // 表示目的の読み取り(message threadからのポーリング)なので厳密な
    // アトミック保証までは求めない（実用上、現代の64bitプラットフォームで
    // doubleの読み書きが割り込まれて破損することは事実上無い）。
    double getSongPositionSec() const { return songPositionSec; }

    // --- グラフエディタ(GraphEditorComponent)からのAutomationCurves反映 ---
    // message threadから呼ぶ。以後、UST/ピアノロール由来のノート(pushSongNote)は
    // ここで設定したカーブをGender/Tension/Breath/Pitchに反映する。
    // ライブMIDI(pushNote)には適用しない（タイムライン基準の時間軸が無いため）。
    void setAutomationCurves (AutomationCurves curves)
    {
        const juce::SpinLock::ScopedLockType sl (automationCurvesLock);
        automationCurves = std::move (curves);
    }
    void setProjectName (const juce::String& name) { projectName = name; }
    juce::String getProjectName() const { return projectName; }

    // --- フェーズ4: UST書き出し ---
    // 現在のsongNotes(+テンポ)をUST形式のテキストファイルとして保存する。
    bool exportToUstFile (const juce::File& outFile) const;

    // --- フェーズ4: buffer_ms調整 ---
    // UI(message thread)から呼ぶ。実際の反映はprocessBlock冒頭で行う
    // （vose_core側のセッション再生成を伴うため、audio thread内で
    //  同期的に行うのは理想的ではないが、頻度が低いユーザー操作なので許容する。
    //  詳細はPluginProcessor.cppのコメント参照）。
    void requestBufferMs (int ms) { pendingBufferMs.store (juce::jlimit (kMinBufferMs, kMaxBufferMs, ms)); }
    int  getActiveBufferMs() const { return activeBufferMs.load(); }

    // --- フェーズ4: ホストトランスポート同期 ---
    void setSyncToHostTransport (bool shouldSync) { syncToHostTransport = shouldSync; }
    bool getSyncToHostTransport() const { return syncToHostTransport; }

    juce::AudioProcessorValueTreeState apvts;

private:
    juce::AudioProcessorValueTreeState::ParameterLayout createParameterLayout();

    // ノートオンに応じて次の歌詞を1つ確定し、streaming APIへノートを積む。
    // trackIndexはMIDIチャンネル(1-16)から決まる（ch1-4 -> track0-3、ch5以降はtrack3に丸める）。
    void pushNote (int midiNoteNumber, int trackIndex);

    // USTスケジューラから呼ばれる版。常にトラック0（UST自体が単一パート仕様のため）。
    void pushSongNote (const ScheduledSongNote& note);

    void resolveAndPushNote (int trackIndex, const std::vector<double>& pitchCurveHz, const juce::String& lyric,
                              const std::vector<double>& genderCurve,
                              const std::vector<double>& tensionCurve,
                              const std::vector<double>& breathCurve,
                              const std::vector<double>& portamentoOffsetsCents = {});

    // AutomationCurves.evaluate() をノート区間全体にわたってサンプリングし、
    // explicitOverride > オートメーション > fallbackScalar(Flags or APVTSグローバル) の
    // 優先順位でresolution点のカーブを組み立てる。
    std::vector<double> buildAutomatedCurve (AutomationParam param, double startSec, double durationSec,
                                              std::optional<double> explicitOverride, double fallbackScalar,
                                              const AutomationCurves& curvesSnapshot, int resolution) const;

    juce::String consumeNextLyric();

    // buffer_ms変更の実適用。processBlock冒頭から呼ぶ。
    void applyPendingBufferMsIfNeeded();

    // ホストトランスポート同期の実適用。processBlock冒頭から呼ぶ。
    void syncFromHostTransportIfEnabled();

    VoseCoreLibrary coreLib;
    std::array<VoiceTrack, kMaxTracks> tracks;
    VowelClassifier vowelClassifier;

    std::deque<juce::String> midiLyricQueue;

    juce::SpinLock    lyricLock;
    juce::StringArray lyricSequence { "a" };
    int               lyricSequenceIndex = 0;

    std::array<juce::String, kMaxTracks> prevLyrics;
    juce::String projectName { "Untitled" };

    mutable juce::SpinLock automationCurvesLock;
    AutomationCurves automationCurves; // GraphEditorComponent由来。message threadから設定、audio threadでスナップショット読み取り。

    mutable juce::SpinLock         songNotesLock;
    std::vector<ScheduledSongNote> songNotes;
    size_t songNoteCursor = 0;
    std::atomic<bool> songPlaying { false };
    double songPositionSec = 0.0;
    double currentSampleRate = 44100.0;
    double currentTempoBpm = kUstDefaultTempo;

    // --- buffer_ms調整用 ---
    std::atomic<int> pendingBufferMs { kDefaultBufferMs };
    std::atomic<int> activeBufferMs  { kDefaultBufferMs };

    // --- ホストトランスポート同期用 ---
    std::atomic<bool> syncToHostTransport { false };
    double lastKnownHostTimeSec = -1.0;

    juce::AudioBuffer<float> pullScratch;
    juce::AudioBuffer<float> mixScratch;

    int64_t nextNoteId = 1;
    bool anyNoteHeld = false;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (VoseAudioProcessor)
};
