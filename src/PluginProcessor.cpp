#include "PluginProcessor.h"
#include "PluginEditor.h"
#include "UstWriter.h"

VoseAudioProcessor::VoseAudioProcessor()
    : juce::AudioProcessor (BusesProperties().withOutput ("Output", juce::AudioChannelSet::stereo(), true)),
      apvts (*this, nullptr, "PARAMS", createParameterLayout())
{
    auto pluginDir = juce::File::getSpecialLocation (juce::File::currentApplicationFile)
                          .getParentDirectory();
    if (! coreLib.load (pluginDir))
        coreLib.load (juce::File::getCurrentWorkingDirectory());

    if (coreLib.isLoaded() && ! coreLib.supportsStreaming())
    {
        juce::Logger::writeToLog ("VO-SE: vose_core loaded but no streaming API found. "
                                   "Rebuild vose_core with vose_streaming_final.cpp linked.");
    }

    // 開発用デフォルト音源フォルダをトラック0に読み込む。
    auto defaultVoiceDir = pluginDir.getChildFile ("voices").getChildFile ("default");
    if (defaultVoiceDir.isDirectory())
        loadVoiceDirectory (defaultVoiceDir, 0);
}

VoseAudioProcessor::~VoseAudioProcessor()
{
    for (auto& t : tracks)
        t.voice.stop();
}

juce::AudioProcessorValueTreeState::ParameterLayout VoseAudioProcessor::createParameterLayout()
{
    using Param = juce::AudioParameterFloat;
    std::vector<std::unique_ptr<juce::RangedAudioParameter>> params;

    // Gender/Tension/Breathは現状トラック共通のグローバル値（各トラック別設定は
    // マルチトラック対応の次のステップとして持ち越し。UST Flagsによる
    // ノート単位の上書きは既にトラックごとに効く）。
    params.push_back (std::make_unique<Param> (juce::ParameterID { "gender", 1 }, "Gender",
                                                juce::NormalisableRange<float> (0.0f, 1.0f), 0.5f));
    params.push_back (std::make_unique<Param> (juce::ParameterID { "tension", 1 }, "Tension",
                                                juce::NormalisableRange<float> (0.0f, 1.0f), 0.5f));
    params.push_back (std::make_unique<Param> (juce::ParameterID { "breath", 1 }, "Breath",
                                                juce::NormalisableRange<float> (0.0f, 1.0f), 0.0f));

    return { params.begin(), params.end() };
}

void VoseAudioProcessor::prepareToPlay (double sampleRate, int samplesPerBlock)
{
    pullScratch.setSize (1, samplesPerBlock);
    mixScratch.setSize (2, samplesPerBlock);
    anyNoteHeld = false;
    currentSampleRate = sampleRate;
    lastKnownHostTimeSec = -1.0;

    activeBufferMs = pendingBufferMs.load();
    if (coreLib.supportsStreaming())
        for (auto& t : tracks)
            t.startStreaming (coreLib, sampleRate, activeBufferMs.load());
}

void VoseAudioProcessor::releaseResources()
{
    for (auto& t : tracks)
        t.voice.stop();
}

void VoseAudioProcessor::loadVoiceDirectory (const juce::File& dir, int trackIndex)
{
    trackIndex = juce::jlimit (0, kMaxTracks - 1, trackIndex);
    auto& track = tracks[(size_t) trackIndex];

    track.otoDb.clear();
    const int entryCount = track.otoDb.loadVoiceDir (dir);

    if (entryCount == 0)
    {
        juce::Logger::writeToLog ("VO-SE: oto.ini が見つからないか0エントリでした: "
                                   + dir.getFullPathName());
        return;
    }

    const int loaded = track.otoDb.pushAllToCore (coreLib);
    track.voiceDirPath = dir.getFullPathName();

    juce::Logger::writeToLog ("VO-SE: [track" + juce::String (trackIndex) + "] oto.ini "
                               + juce::String (entryCount) + "エントリ解析、"
                               + juce::String (loaded) + "件のWAVをコアへ事前登録しました。");
}

int VoseAudioProcessor::getLoadedAliasCount (int trackIndex) const
{
    trackIndex = juce::jlimit (0, kMaxTracks - 1, trackIndex);
    return tracks[(size_t) trackIndex].otoDb.size();
}

void VoseAudioProcessor::setTrackGain (int trackIndex, float linearGain)
{
    trackIndex = juce::jlimit (0, kMaxTracks - 1, trackIndex);
    tracks[(size_t) trackIndex].gain.store (juce::jlimit (0.0f, 2.0f, linearGain));
}

void VoseAudioProcessor::setTrackPan (int trackIndex, float pan)
{
    trackIndex = juce::jlimit (0, kMaxTracks - 1, trackIndex);
    tracks[(size_t) trackIndex].pan.store (juce::jlimit (-1.0f, 1.0f, pan));
}

void VoseAudioProcessor::setTrackMuted (int trackIndex, bool muted)
{
    trackIndex = juce::jlimit (0, kMaxTracks - 1, trackIndex);
    tracks[(size_t) trackIndex].muted.store (muted);
}

juce::String VoseAudioProcessor::getTrackVoiceDirName (int trackIndex) const
{
    trackIndex = juce::jlimit (0, kMaxTracks - 1, trackIndex);
    const auto& path = tracks[(size_t) trackIndex].voiceDirPath;
    return path.isEmpty() ? juce::String ("(未設定)") : juce::File (path).getFileName();
}

void VoseAudioProcessor::setLyricSequence (const juce::String& spaceSeparatedText)
{
    auto tokens = juce::StringArray::fromTokens (spaceSeparatedText, " \t\n\r", "");
    tokens.removeEmptyStrings();
    if (tokens.isEmpty())
        tokens.add ("a");

    const juce::SpinLock::ScopedLockType sl (lyricLock);
    lyricSequence = tokens;
    lyricSequenceIndex = 0;
}

juce::String VoseAudioProcessor::getLyricSequenceText() const
{
    const juce::SpinLock::ScopedLockType sl (lyricLock);
    return lyricSequence.joinIntoString (" ");
}

juce::String VoseAudioProcessor::consumeNextLyric()
{
    if (! midiLyricQueue.empty())
    {
        auto lyric = midiLyricQueue.front();
        midiLyricQueue.pop_front();
        return lyric;
    }

    const juce::SpinLock::ScopedLockType sl (lyricLock);
    if (lyricSequence.isEmpty())
        return "a";

    const auto lyric = lyricSequence[lyricSequenceIndex % lyricSequence.size()];
    lyricSequenceIndex = (lyricSequenceIndex + 1) % lyricSequence.size();
    return lyric;
}

void VoseAudioProcessor::pushNote (int midiNoteNumber, int trackIndex)
{
    constexpr int kRes = 128;
    const double hz = 440.0 * std::pow (2.0, (midiNoteNumber - 69) / 12.0);
    std::vector<double> flatCurve (kRes, hz);

    std::vector<double> genderCurve (kRes, (double) apvts.getRawParameterValue ("gender")->load());
    std::vector<double> tensionCurve (kRes, (double) apvts.getRawParameterValue ("tension")->load());
    std::vector<double> breathCurve (kRes, (double) apvts.getRawParameterValue ("breath")->load());

    resolveAndPushNote (trackIndex, flatCurve, consumeNextLyric(), genderCurve, tensionCurve, breathCurve);
}

std::vector<double> VoseAudioProcessor::buildAutomatedCurve (AutomationParam param, double startSec, double durationSec,
                                                              std::optional<double> explicitOverride,
                                                              double fallbackScalar,
                                                              const AutomationCurves& curvesSnapshot,
                                                              int resolution) const
{
    std::vector<double> out ((size_t) resolution);
    const int denom = juce::jmax (1, resolution - 1);

    for (int j = 0; j < resolution; ++j)
    {
        if (explicitOverride.has_value())
        {
            out[(size_t) j] = *explicitOverride; // 最優先: ノート単位の明示上書き
            continue;
        }

        const double tAbs = startSec + durationSec * ((double) j / (double) denom);
        const auto automated = curvesSnapshot.evaluate (param, tAbs);
        out[(size_t) j] = automated.value_or (fallbackScalar); // 次点: 連続カーブ、無ければFlags/APVTS
    }
    return out;
}

void VoseAudioProcessor::pushSongNote (const ScheduledSongNote& note)
{
    constexpr int kRes = 128;
    const double durationMs = note.durationSec * 1000.0;

    // GraphEditorComponent由来のAutomationCurvesをスナップショット取得
    // (audio thread内でのロック保持時間を最小化するため、一度コピーしてから
    //  ロック無しで評価する。頻度が低い操作なのでコピーコストは許容する)。
    AutomationCurves curvesSnapshot;
    {
        const juce::SpinLock::ScopedLockType sl (automationCurvesLock);
        curvesSnapshot = automationCurves;
    }

    auto pitchCurve = vose_pitch::buildVibratoPitchCurveHz (note.noteNum, durationMs, note.vibrato, kRes);

    // Pitchオートメーション（あれば）をベースピッチ(ビブラート込み)に乗算で加算適用。
    // AutomationRanges::pitchValueToSemitones()で値域(-8192..8191)を semitone に変換してから
    // 周波数比へ変換する（加算前の semitone 空間で足すのと数学的に等価）。
    if (! curvesSnapshot.pitch.empty())
    {
        const int denom = juce::jmax (1, kRes - 1);
        for (int j = 0; j < kRes; ++j)
        {
            const double tAbs = note.startTimeSec + note.durationSec * ((double) j / (double) denom);
            if (auto v = curvesSnapshot.evaluate (AutomationParam::pitch, tAbs))
            {
                const double semitoneOffset = AutomationRanges::pitchValueToSemitones (*v);
                pitchCurve[(size_t) j] *= std::pow (2.0, semitoneOffset / 12.0);
            }
        }
    }

    auto portamentoCents = vose_pitch::buildPortamentoCentsCurve (note.pbs, note.pbw, note.pby, note.pbm, durationMs, kRes);

    const auto flagOverrides = parseUstFlags (note.flags);
    const double genderFallback  = flagOverrides.gender01.value_or  ((double) apvts.getRawParameterValue ("gender")->load());
    const double tensionFallback = flagOverrides.tension01.value_or ((double) apvts.getRawParameterValue ("tension")->load());
    const double breathFallback  = flagOverrides.breath01.value_or  ((double) apvts.getRawParameterValue ("breath")->load());

    // 優先順位: note.xxxOverride01（明示上書き）> AutomationCurves（連続カーブ）
    //           > Flags > APVTSグローバル値（fallbackに集約済み）
    auto genderCurve  = buildAutomatedCurve (AutomationParam::gender,  note.startTimeSec, note.durationSec,
                                              note.genderOverride01,  genderFallback,  curvesSnapshot, kRes);
    auto tensionCurve = buildAutomatedCurve (AutomationParam::tension, note.startTimeSec, note.durationSec,
                                              note.tensionOverride01, tensionFallback, curvesSnapshot, kRes);
    auto breathCurve  = buildAutomatedCurve (AutomationParam::breath,  note.startTimeSec, note.durationSec,
                                              note.breathOverride01,  breathFallback,  curvesSnapshot, kRes);

    // USTは単一パート仕様のため、常にトラック0を使う（マルチトラックUST風合成は対象外）。
    resolveAndPushNote (0, pitchCurve, note.lyric, genderCurve, tensionCurve, breathCurve, portamentoCents);
}

void VoseAudioProcessor::resolveAndPushNote (int trackIndex, const std::vector<double>& pitchCurveHz,
                                              const juce::String& lyric,
                                              const std::vector<double>& genderCurve,
                                              const std::vector<double>& tensionCurve,
                                              const std::vector<double>& breathCurve,
                                              const std::vector<double>& portamentoOffsetsCents)
{
    trackIndex = juce::jlimit (0, kMaxTracks - 1, trackIndex);
    auto& track = tracks[(size_t) trackIndex];

    juce::String prevVowel;
    if (track.otoDb.hasVcv() && prevLyric.isNotEmpty())
        prevVowel = vowelClassifier.trailingVowel (prevLyric);

    const auto* entry = track.otoDb.resolveAlias (lyric, prevVowel);

    if (entry == nullptr)
    {
        juce::Logger::writeToLog ("VO-SE: [track" + juce::String (trackIndex) + "] 歌詞 '" + lyric
                                   + "' に対応するoto.iniエントリが見つかりません。");
    }

    const juce::String aliasToUse = (entry != nullptr) ? entry->alias : lyric;

    track.voice.pushNote (nextNoteId++, aliasToUse, pitchCurveHz, genderCurve, tensionCurve, breathCurve,
                           portamentoOffsetsCents);

    prevLyric = lyric;
}

bool VoseAudioProcessor::loadUstFile (const juce::File& ustFile)
{
    UstParser parser;
    auto project = parser.load (ustFile);

    if (project.notes.empty())
    {
        juce::Logger::writeToLog ("VO-SE: UST読み込み失敗、またはノートが0件でした: "
                                   + ustFile.getFullPathName());
        return false;
    }

    songPlaying = false;
    auto newNotes = UstParser::toScheduledNotes (project);
    {
        const juce::SpinLock::ScopedLockType sl (songNotesLock);
        songNotes = std::move (newNotes);
        songNoteCursor = 0;
    }
    songPositionSec = 0.0;
    currentTempoBpm = project.tempo;
    if (project.projectName.isNotEmpty())
        projectName = project.projectName;

    juce::Logger::writeToLog ("VO-SE: UST読み込み完了。" + juce::String ((int) songNotes.size()) + "ノート、"
                               + "テンポ=" + juce::String (project.tempo));
    return true;
}

void VoseAudioProcessor::setEditedNotes (std::vector<ScheduledSongNote> notes)
{
    songPlaying = false;
    const juce::SpinLock::ScopedLockType sl (songNotesLock);
    songNotes = std::move (notes);
    songNoteCursor = 0;
    songPositionSec = 0.0;
}

void VoseAudioProcessor::startSongPlayback()
{
    {
        const juce::SpinLock::ScopedLockType sl (songNotesLock);
        songNoteCursor = 0;
    }
    songPositionSec = 0.0;
    prevLyric.clear();
    songPlaying = (getLoadedSongNoteCount() > 0);
}

void VoseAudioProcessor::stopSongPlayback()
{
    songPlaying = false;
}

bool VoseAudioProcessor::exportToUstFile (const juce::File& outFile) const
{
    auto snapshot = getSongNotesSnapshot();
    return UstWriter::write (outFile, snapshot, currentTempoBpm, projectName);
}

void VoseAudioProcessor::applyPendingBufferMsIfNeeded()
{
    const int wanted = pendingBufferMs.load();
    if (wanted == activeBufferMs.load())
        return;

    // 【注意: 厳密にはリアルタイム安全ではない】
    // streaming_render_create/destroy はvose_core内部でスレッド生成・破棄を伴う
    // 可能性があり、これをaudio threadから同期的に呼ぶのは理想的ではない。
    // ただしbuffer_ms変更はユーザーが明示的に操作した時だけ発生する低頻度イベント
    // であり、変更の瞬間に短い音切れが起きることを許容する前提で、実装をシンプルに
    // 保つためあえてここで同期的に行っている。継続的な自動化には使わないこと。
    if (coreLib.supportsStreaming())
    {
        for (auto& t : tracks)
        {
            t.voice.stop();
            t.startStreaming (coreLib, currentSampleRate, wanted);
        }
    }
    activeBufferMs = wanted;
}

void VoseAudioProcessor::syncFromHostTransportIfEnabled()
{
    if (! syncToHostTransport.load())
        return;

    auto* playHead = getPlayHead();
    if (playHead == nullptr)
        return;

    const auto position = playHead->getPosition();
    if (! position.hasValue())
        return;

    const bool hostIsPlaying = position->getIsPlaying();
    const double hostTimeSec = position->getTimeInSeconds().orFallback (0.0);

    songPlaying = hostIsPlaying && (getLoadedSongNoteCount() > 0);

    if (! hostIsPlaying)
    {
        lastKnownHostTimeSec = -1.0; // 停止中はシーク検出をリセット
        return;
    }

    // シーク検出: 前回位置からの差が「1ブロック分の進み」から大きく外れていたら
    // ユーザーが再生位置を動かしたとみなし、スキップした区間のノートは
    // 発音せずにカーソルだけ進める（DAWの一般的な挙動に合わせる）。
    constexpr double kSeekToleranceSec = 0.25;
    const bool looksLikeSeek = (lastKnownHostTimeSec < 0.0)
                                || std::abs (hostTimeSec - lastKnownHostTimeSec) > kSeekToleranceSec;

    if (looksLikeSeek)
    {
        const juce::SpinLock::ScopedLockType sl (songNotesLock);
        songNoteCursor = 0;
        while (songNoteCursor < songNotes.size() && songNotes[songNoteCursor].startTimeSec < hostTimeSec)
            ++songNoteCursor; // 発音はせず読み飛ばすだけ
    }

    songPositionSec = hostTimeSec;
    lastKnownHostTimeSec = hostTimeSec;
}

void VoseAudioProcessor::processBlock (juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midi)
{
    juce::ScopedNoDenormals noDenormals;
    buffer.clear();

    applyPendingBufferMsIfNeeded();
    syncFromHostTransportIfEnabled();

    // ---- MIDI処理 ----
    // 優先1: Lyric(type=5)/Text(type=1)メタイベントを見つけたらキューに積む。
    // 優先3: 見つからなければ内蔵歌詞キューUIをローテーション消費する(pushNote内)。
    // MIDIチャンネル(1-16)でトラックを選ぶ: ch1-4 -> track0-3、ch5以降はtrack3に丸める。
    for (const auto metadata : midi)
    {
        const auto msg = metadata.getMessage();

        if (msg.isTextMetaEvent())
        {
            const int metaType = msg.getMetaEventType();
            if (metaType == 5 || metaType == 1)
                midiLyricQueue.push_back (msg.getTextFromTextMetaEvent());
        }
        else if (msg.isNoteOn())
        {
            const int trackIndex = juce::jlimit (0, kMaxTracks - 1, msg.getChannel() - 1);
            pushNote (msg.getNoteNumber(), trackIndex);
            anyNoteHeld = true;
        }
        else if (msg.isNoteOff())
        {
            anyNoteHeld = false;
        }
    }

    // ---- UST曲スケジューラ ----
    // 内部クロックモード: songPositionSecをこのブロックの長さぶん進める。
    // ホスト同期モード: syncFromHostTransportIfEnabled()が既にsongPositionSecを
    // 更新済みなので、ここでは「前回位置からの経過」ではなく現在値をそのまま使う。
    if (songPlaying.load())
    {
        const double blockDurationSec = (double) buffer.getNumSamples() / currentSampleRate;
        const double windowEnd = syncToHostTransport.load() ? songPositionSec
                                                              : songPositionSec + blockDurationSec;

        std::vector<ScheduledSongNote> notesToTrigger;
        {
            const juce::SpinLock::ScopedLockType sl (songNotesLock);
            while (songNoteCursor < songNotes.size() && songNotes[songNoteCursor].startTimeSec < windowEnd)
            {
                notesToTrigger.push_back (songNotes[songNoteCursor]);
                ++songNoteCursor;
            }
            if (songNoteCursor >= songNotes.size() && ! syncToHostTransport.load())
                songPlaying = false; // 内部クロックモードのみ自動停止（ホスト同期時はホストが止めるまで待つ）
        }

        for (const auto& sn : notesToTrigger)
        {
            if (! sn.lyric.trim().equalsIgnoreCase ("R"))
            {
                pushSongNote (sn);
                anyNoteHeld = true;
            }
        }

        if (! syncToHostTransport.load())
            songPositionSec = windowEnd;
    }

    // ---- トラックのpull + ミックス ----
    const int numOut = buffer.getNumSamples();
    pullScratch.setSize (1, numOut, false, false, true);
    float* mono = pullScratch.getWritePointer (0);

    for (auto& t : tracks)
    {
        if (! t.voice.isActive() || t.muted.load())
            continue;

        const int got = t.voice.pull (mono, numOut);
        if (got <= 0)
            continue;

        const float gain = t.gain.load();
        const float pan  = t.pan.load();
        // 等パワーパン則。pan=0で両ch -3dB、pan=-1で左のみ、pan=+1で右のみ。
        const double angle = (pan + 1.0) * (juce::MathConstants<double>::pi / 4.0);
        const float leftGain  = gain * (float) std::cos (angle);
        const float rightGain = gain * (float) std::sin (angle);

        float* dstL = buffer.getWritePointer (0);
        float* dstR = buffer.getNumChannels() > 1 ? buffer.getWritePointer (1) : dstL;

        for (int i = 0; i < got; ++i)
        {
            dstL[i] += mono[i] * leftGain;
            dstR[i] += mono[i] * rightGain;
        }
    }
}

juce::AudioProcessorEditor* VoseAudioProcessor::createEditor()
{
    return new VoseAudioProcessorEditor (*this);
}

juce::AudioProcessor* JUCE_CALLTYPE createPluginFilter()
{
    return new VoseAudioProcessor();
}
