// PluginEditor.h
// フェーズ4 PoC v2: PianoRollComponent / GraphEditorComponent が
// VoseAudioProcessorを直接知らない疎結合設計に変わったことに合わせて全面改訂。
// データの橋渡し（processor <-> notes/curves の変換）はこのファイルの責務になる。

#pragma once

#include <juce_audio_processors/juce_audio_processors.h>
#include "PluginProcessor.h"
#include "VoseLookAndFeel.h"
#include "PianoRollComponent.h"
#include "GraphEditorComponent.h"
#include "VoiceGalleryComponent.h"
#include "TrackMixerComponent.h"

// ------------------------------------------------------------------
// ControlsPanel: デバッグ用の生パラメータ調整パネル。
// ------------------------------------------------------------------
class ControlsPanel : public juce::Component
{
public:
    explicit ControlsPanel (VoseAudioProcessor& p) : processor (p)
    {
        setupSlider (genderSlider, genderAttach, "gender");
        setupSlider (tensionSlider, tensionAttach, "tension");
        setupSlider (breathSlider, breathAttach, "breath");

        lyricSequenceBox.setText (processor.getLyricSequenceText(), juce::dontSendNotification);
        lyricSequenceBox.setTooltip ("スペース区切りで歌詞を並べる（例: a i u e o）。"
                                      "MIDIノートオンのたびに1語ずつ消費し、最後まで行くと最初に戻ります。"
                                      "DAW側にLyricメタイベントがあればそちらが優先されます。");
        lyricSequenceBox.onTextChange = [this] { processor.setLyricSequence (lyricSequenceBox.getText()); };
        addAndMakeVisible (lyricSequenceBox);

        loadVoiceButton.onClick = [this]
        {
            fileChooser = std::make_unique<juce::FileChooser> (
                "音源フォルダを選択 (oto.iniを含むフォルダ、トラック1に読み込みます)",
                juce::File::getSpecialLocation (juce::File::userHomeDirectory));

            fileChooser->launchAsync (juce::FileBrowserComponent::canSelectDirectories,
                                       [this] (const juce::FileChooser& fc)
            {
                auto dir = fc.getResult();
                if (dir.isDirectory())
                {
                    processor.loadVoiceDirectory (dir); // トラック0
                    updateStatusLabel();
                }
            });
        };
        addAndMakeVisible (loadVoiceButton);
        addAndMakeVisible (statusLabel);

        loadUstButton.onClick = [this]
        {
            ustFileChooser = std::make_unique<juce::FileChooser> (
                "USTファイルを選択", juce::File::getSpecialLocation (juce::File::userHomeDirectory),
                "*.ust");

            ustFileChooser->launchAsync (juce::FileBrowserComponent::canSelectFiles,
                                          [this] (const juce::FileChooser& fc)
            {
                auto f = fc.getResult();
                if (f.existsAsFile())
                {
                    processor.loadUstFile (f);
                    updateStatusLabel();
                    if (onUstLoaded)
                        onUstLoaded();
                }
            });
        };
        addAndMakeVisible (loadUstButton);

        exportUstButton.onClick = [this]
        {
            exportFileChooser = std::make_unique<juce::FileChooser> (
                "書き出し先を選択", juce::File::getSpecialLocation (juce::File::userHomeDirectory)
                                          .getChildFile (processor.getProjectName() + ".ust"),
                "*.ust");

            exportFileChooser->launchAsync (juce::FileBrowserComponent::saveMode,
                                             [this] (const juce::FileChooser& fc)
            {
                auto f = fc.getResult();
                if (f != juce::File())
                {
                    const bool ok = processor.exportToUstFile (f);
                    statusLabel.setText (ok ? ("書き出し成功: " + f.getFullPathName())
                                             : "書き出し失敗（ノートが無いか、書き込みエラー）",
                                          juce::dontSendNotification);
                }
            });
        };
        addAndMakeVisible (exportUstButton);

        playButton.onClick = [this] { processor.startSongPlayback(); };
        stopButton.onClick = [this] { processor.stopSongPlayback(); };
        addAndMakeVisible (playButton);
        addAndMakeVisible (stopButton);

        bufferMsSlider.setRange ((double) VoseAudioProcessor::kMinBufferMs,
                                  (double) VoseAudioProcessor::kMaxBufferMs, 10.0);
        bufferMsSlider.setValue (processor.getActiveBufferMs(), juce::dontSendNotification);
        bufferMsSlider.setSliderStyle (juce::Slider::LinearHorizontal);
        bufferMsSlider.setTextBoxStyle (juce::Slider::TextBoxRight, false, 60, 20);
        bufferMsSlider.setTooltip ("先読みバッファ量[ms]。大きいほど発音までの遅延は増えるが安定する。"
                                    "変更時は瞬間的に音が途切れることがあります（低頻度の設定変更として許容）。");
        bufferMsSlider.onValueChange = [this] { processor.requestBufferMs ((int) bufferMsSlider.getValue()); };
        addAndMakeVisible (bufferMsLabel);
        addAndMakeVisible (bufferMsSlider);

        hostSyncButton.setClickingTogglesState (true);
        hostSyncButton.setToggleState (processor.getSyncToHostTransport(), juce::dontSendNotification);
        hostSyncButton.onClick = [this]
        {
            processor.setSyncToHostTransport (hostSyncButton.getToggleState());
            playButton.setEnabled (! hostSyncButton.getToggleState());
            stopButton.setEnabled (! hostSyncButton.getToggleState());
        };
        addAndMakeVisible (hostSyncButton);

        updateStatusLabel();
    }

    void resized() override
    {
        auto area = getLocalBounds().reduced (16);
        genderSlider.setBounds (area.removeFromTop (32));
        tensionSlider.setBounds (area.removeFromTop (32));
        breathSlider.setBounds (area.removeFromTop (32));

        area.removeFromTop (6);
        auto lyricRow = area.removeFromTop (26);
        lyricSequenceBox.setBounds (lyricRow.removeFromLeft (200));
        lyricRow.removeFromLeft (8);
        loadVoiceButton.setBounds (lyricRow);

        area.removeFromTop (6);
        statusLabel.setBounds (area.removeFromTop (36));

        area.removeFromTop (6);
        auto ustRow = area.removeFromTop (26);
        loadUstButton.setBounds (ustRow.removeFromLeft (110));
        ustRow.removeFromLeft (6);
        exportUstButton.setBounds (ustRow.removeFromLeft (110));
        ustRow.removeFromLeft (6);
        playButton.setBounds (ustRow.removeFromLeft (60));
        ustRow.removeFromLeft (6);
        stopButton.setBounds (ustRow.removeFromLeft (60));
        ustRow.removeFromLeft (6);
        hostSyncButton.setBounds (ustRow);

        area.removeFromTop (10);
        auto bufRow = area.removeFromTop (26);
        bufferMsLabel.setBounds (bufRow.removeFromLeft (110));
        bufferMsSlider.setBounds (bufRow);
    }

    std::function<void()> onUstLoaded;

private:
    using SliderAttachment = juce::AudioProcessorValueTreeState::SliderAttachment;

    void setupSlider (juce::Slider& slider, std::unique_ptr<SliderAttachment>& attach,
                       const juce::String& paramId)
    {
        slider.setSliderStyle (juce::Slider::LinearHorizontal);
        slider.setTextBoxStyle (juce::Slider::TextBoxRight, false, 60, 20);
        addAndMakeVisible (slider);
        attach = std::make_unique<SliderAttachment> (processor.apvts, paramId, slider);
    }

    void updateStatusLabel()
    {
        statusLabel.setText (
            "トラック1 alias数: " + juce::String (processor.getLoadedAliasCount (0))
                + " / USTノート数: " + juce::String (processor.getLoadedSongNoteCount())
                + "\nMIDI Lyricメタイベントがあれば優先、無ければ上の歌詞シーケンスをローテーション消費",
            juce::dontSendNotification);
    }

    VoseAudioProcessor& processor;
    juce::Slider genderSlider, tensionSlider, breathSlider;
    std::unique_ptr<SliderAttachment> genderAttach, tensionAttach, breathAttach;

    juce::TextEditor lyricSequenceBox;
    juce::TextButton loadVoiceButton { "音源フォルダを開く..." };
    juce::Label statusLabel;
    std::unique_ptr<juce::FileChooser> fileChooser;

    juce::TextButton loadUstButton { "USTを開く..." };
    juce::TextButton exportUstButton { "USTを書き出す..." };
    juce::TextButton playButton { "再生" };
    juce::TextButton stopButton { "停止" };
    juce::TextButton hostSyncButton { "ホスト同期" };
    std::unique_ptr<juce::FileChooser> ustFileChooser;
    std::unique_ptr<juce::FileChooser> exportFileChooser;

    juce::Label bufferMsLabel { "bufLabel", "先読みバッファ(ms)" };
    juce::Slider bufferMsSlider;
};

// ------------------------------------------------------------------
// GraphEditorTab: モード切替(Pitch/Gender/Tension/Breath) + ペンモードトグル
// + GraphEditorComponent本体。
// GraphEditorComponentはVoseAudioProcessorもPianoRollComponentも知らない
// 独立コンポーネントなので、AutomationCurvesの受け渡しはこのタブが仲介する。
// ------------------------------------------------------------------
class GraphEditorTab : public juce::Component
{
public:
    GraphEditorTab()
    {
        for (auto* b : { &pitchTab, &genderTab, &tensionTab, &breathTab })
        {
            b->setClickingTogglesState (true);
            addAndMakeVisible (b);
        }
        pitchTab.setRadioGroupId (1);
        genderTab.setRadioGroupId (1);
        tensionTab.setRadioGroupId (1);
        breathTab.setRadioGroupId (1);
        genderTab.setToggleState (true, juce::dontSendNotification); // Genderを既定表示（フェーズ3までの慣習に合わせた）
        graphEditor.setMode (AutomationParam::gender);

        pitchTab.onClick   = [this] { graphEditor.setMode (AutomationParam::pitch); };
        genderTab.onClick  = [this] { graphEditor.setMode (AutomationParam::gender); };
        tensionTab.onClick = [this] { graphEditor.setMode (AutomationParam::tension); };
        breathTab.onClick  = [this] { graphEditor.setMode (AutomationParam::breath); };

        penModeButton.setClickingTogglesState (true);
        penModeButton.onClick = [this] { graphEditor.setPenMode (penModeButton.getToggleState()); };
        addAndMakeVisible (penModeButton);

        addAndMakeVisible (graphEditor);
    }

    GraphEditorComponent& getGraphEditor() { return graphEditor; }

    void resized() override
    {
        auto area = getLocalBounds();
        auto tabRow = area.removeFromTop (28);
        const int w = tabRow.getWidth() * 3 / 5 / 4; // 4モードボタン + ペンボタンで幅を分ける
        pitchTab.setBounds (tabRow.removeFromLeft (w));
        genderTab.setBounds (tabRow.removeFromLeft (w));
        tensionTab.setBounds (tabRow.removeFromLeft (w));
        breathTab.setBounds (tabRow.removeFromLeft (w));
        tabRow.removeFromLeft (8);
        penModeButton.setBounds (tabRow);
        graphEditor.setBounds (area);
    }

private:
    juce::TextButton pitchTab { "Pitch" }, genderTab { "Gender" }, tensionTab { "Tension" }, breathTab { "Breath" };
    juce::TextButton penModeButton { "ペンモード" };
    GraphEditorComponent graphEditor;
};

// ------------------------------------------------------------------
// VoseAudioProcessorEditor: トップレベル。タブ構成 + テーマ切替。
//
// PianoRollComponent/GraphEditorComponentはprocessorを直接知らないため、
// このクラスが processor <-> notes/curves の変換を担う
// （refreshPianoRollFromProcessor / pianoRoll.onNotesChanged / graphEditor.onCurvesChanged）。
// ------------------------------------------------------------------
class VoseAudioProcessorEditor : public juce::AudioProcessorEditor
{
public:
    explicit VoseAudioProcessorEditor (VoseAudioProcessor& p)
        : juce::AudioProcessorEditor (&p), voseProcessor (p), voiceGallery (p), trackMixer (p)
    {
        setLookAndFeel (&lookAndFeel);

        themeToggleButton.onClick = [this]
        {
            lookAndFeel.toggleTheme();
            applyThemeToChildren();
        };
        addAndMakeVisible (themeToggleButton);

        // --- ピアノロール: processorとの橋渡し ---
        pianoRoll.setPlayheadSecondsProvider ([this] { return voseProcessor.getSongPositionSec(); });
        pianoRoll.onNotesChanged = [this] (const std::vector<PianoRollNote>& notes, double /*tempoBpm*/)
        {
            std::vector<ScheduledSongNote> out;
            out.reserve (notes.size());
            for (const auto& n : notes)
            {
                ScheduledSongNote sn;
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
                sn.preUtteranceMs = n.preUtteranceMs;
                sn.overlapMs = n.overlapMs;
                sn.genderOverride01  = n.genderOverride01;
                sn.tensionOverride01 = n.tensionOverride01;
                sn.breathOverride01  = n.breathOverride01;
                out.push_back (std::move (sn));
            }
            voseProcessor.setEditedNotes (std::move (out));
        };
        refreshPianoRollFromProcessor();

        // --- グラフエディタ: 現状はUIローカルに保持するのみ ---
        // TODO: AutomationCurvesを実際の合成パイプライン（gender/tension/breath
        // カーブ）へ反映する経路はまだ無い。PianoRollNoteのgenderOverride01等
        // (ノート単位のスカラー)とAutomationCurves(任意時刻の連続カーブ)を
        // どう統合するかは別途設計が必要なため、ここでは受け取って保持するだけ。
        graphEditorTab.getGraphEditor().setPlayheadSecondsProvider (
            [this] { return voseProcessor.getSongPositionSec(); });
        graphEditorTab.getGraphEditor().onCurvesChanged = [this] (const AutomationCurves& c)
        {
            latestCurves = c;
            juce::Logger::writeToLog ("VO-SE: AutomationCurvesが更新されましたが、"
                                       "まだ合成パイプラインへは未接続です（TODO）。");
        };

        controls.onUstLoaded = [this] { refreshPianoRollFromProcessor(); };
        voiceGallery.onVoiceLoaded = [this] { /* 将来: トラック名表示の更新等 */ };

        tabs.addTab ("コントロール", juce::Colours::transparentBlack, &controls, false);
        tabs.addTab ("ピアノロール", juce::Colours::transparentBlack, &pianoRoll, false);
        tabs.addTab ("グラフエディタ", juce::Colours::transparentBlack, &graphEditorTab, false);
        tabs.addTab ("音源ブラウザ", juce::Colours::transparentBlack, &voiceGallery, false);
        tabs.addTab ("ミキサー", juce::Colours::transparentBlack, &trackMixer, false);
        addAndMakeVisible (tabs);

        applyThemeToChildren();
        setSize (760, 520);
    }

    ~VoseAudioProcessorEditor() override { setLookAndFeel (nullptr); }

    void paint (juce::Graphics& g) override
    {
        g.fillAll (lookAndFeel.colourBackground);
        g.setColour (lookAndFeel.colourText);
        g.setFont (juce::Font (16.0f, juce::Font::bold));
        g.drawFittedText ("VO-SE (Phase 4 PoC)", getLocalBounds().removeFromTop (30).reduced (8, 0),
                           juce::Justification::centredLeft, 1);
    }

    void resized() override
    {
        auto area = getLocalBounds();
        auto topRow = area.removeFromTop (30);
        themeToggleButton.setBounds (topRow.removeFromRight (110).reduced (4));
        tabs.setBounds (area);
    }

private:
    void refreshPianoRollFromProcessor()
    {
        auto snapshot = voseProcessor.getSongNotesSnapshot();
        std::vector<PianoRollNote> converted;
        converted.reserve (snapshot.size());
        for (const auto& sn : snapshot)
        {
            PianoRollNote n;
            n.startTimeSec = sn.startTimeSec;
            n.durationSec  = sn.durationSec;
            n.noteNum      = sn.noteNum;
            n.lyric        = sn.lyric;
            n.velocity     = juce::jlimit (0, 127, (int) std::lround (sn.velocity01 * 127.0));
            n.intensity    = juce::jlimit (0.0, 200.0, sn.intensity);
            n.modulation   = juce::jlimit (0.0, 100.0, sn.modulation);
            n.flags        = sn.flags;
            n.pbs = sn.pbs; n.pbw = sn.pbw; n.pby = sn.pby; n.pbm = sn.pbm;
            n.vibrato = sn.vibrato;
            n.preUtteranceMs = sn.preUtteranceMs;
            n.overlapMs = sn.overlapMs;
            n.genderOverride01  = sn.genderOverride01;
            n.tensionOverride01 = sn.tensionOverride01;
            n.breathOverride01  = sn.breathOverride01;
            converted.push_back (std::move (n));
        }
        pianoRoll.setNotes (std::move (converted));
        pianoRoll.setTempo (voseProcessor.getCurrentTempo());
    }

    void applyThemeToChildren()
    {
        // PianoRollComponent/GraphEditorComponentはVoseColourIds+findColour()経由で
        // 色を取得する設計になったため、setLookAndFeel(&lookAndFeel)が
        // トップレベルに掛かっていれば子コンポーネントは自動的に継承する
        // （個別のsetLookAndFeelRefは不要になった）。
        // ただしVoseLookAndFeel側でVoseColourIdsに対応する色を
        // setColour()していない場合、JUCEのデフォルト色にフォールバックする点に注意
        // （VoseLookAndFeel.hの更新が別途必要な可能性がある）。
        voiceGallery.setLookAndFeelRef (&lookAndFeel);
        trackMixer.setLookAndFeelRef (&lookAndFeel);
        themeToggleButton.setButtonText (
            lookAndFeel.getTheme() == VoseLookAndFeel::Theme::Dark ? "ライトに切替" : "ダークに切替");
        pianoRoll.repaint();
        graphEditorTab.repaint();
        repaint();
    }

    VoseAudioProcessor& voseProcessor;
    VoseLookAndFeel lookAndFeel;
    juce::TextButton themeToggleButton { "ライトに切替" };

    juce::TabbedComponent tabs { juce::TabbedButtonBar::TabsAtTop };
    ControlsPanel controls { voseProcessor };
    PianoRollComponent pianoRoll;
    GraphEditorTab graphEditorTab;
    VoiceGalleryComponent voiceGallery;
    TrackMixerComponent trackMixer;

    AutomationCurves latestCurves; // TODO: 合成パイプラインへの接続待ち
};
