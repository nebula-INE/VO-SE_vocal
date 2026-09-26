// PianoRollComponent.h
//
// 【重要】このヘッダは PianoRollComponent.cpp（アップロードされた実装）から
// 逆算して再構築したものです。以前私が渡していたヘッダオンリー版
// （VoseAudioProcessor直接参照、拍単位のPianoRollNote等）とはAPIが
// 根本的に異なります。この.cppは:
//   - VoseAudioProcessorを直接参照しない疎結合設計
//     （onNotesChangedコールバック + setPlayheadSecondsProviderで連携）
//   - 時間は「秒」単位で保持（拍単位ではない）
//   - ラバーバンド選択・複数選択・ループ範囲・左右リサイズ・
//     キーボードショートカット(Delete/Cmd+A/Esc)・独立した歌詞エディタを持つ
// という、より高機能な実装です。
//
// 【要確認】VoseColourIds は本来 VoseLookAndFeel.h 側で一元管理すべきものです。
// 現時点ではその実体が見えていないため、ここに暫定定義を置いています。
// もし VoseLookAndFeel.h 側に既に同名の enum があれば重複定義エラーになるので、
// その場合は VoseLookAndFeel.h を共有してください。こちらの定義を削除して
// 一本化します。
//
// 【要確認】kLowestNote/kHighestNote/kKeyboardWidth/kRulerHeight/kEdgeGrabPx/
// kMinNoteDurationSec の値は .cpp から使用箇所は分かるものの、正確な元の値までは
// 復元できないため、妥当と思われる値を暫定的に入れています。見た目やヒット
// テストの挙動が想定と違う場合はここを調整してください。

#pragma once

#include <juce_gui_basics/juce_gui_basics.h>
#include "UstProject.h"
#include "VoseColourIds.h"
#include <functional>
#include <memory>
#include <vector>





#include "PianoRollNote.h"
// ------------------------------------------------------------------
// PianoRollComponent
// ------------------------------------------------------------------
class PianoRollComponent : public juce::Component, private juce::Timer
{
public:
    PianoRollComponent();

    void setNotes (std::vector<PianoRollNote> newNotes);
    void setTempo (double bpm);
    void setPlayheadSecondsProvider (std::function<double()> provider);
    void clearLoopRange();
    void setHorizontalZoom (double newPixelsPerSecond);
    void setVerticalZoom (double newPixelsPerRow);

    void paint (juce::Graphics&) override;
    void resized() override;
    void mouseDown (const juce::MouseEvent&) override;
    void mouseDrag (const juce::MouseEvent&) override;
    void mouseUp (const juce::MouseEvent&) override;
    void mouseMove (const juce::MouseEvent&) override;
    void mouseWheelMove (const juce::MouseEvent&, const juce::MouseWheelDetails&) override;
    bool keyPressed (const juce::KeyPress&) override;

    void deleteSelectedNotes();
    void selectAll();
    void deselectAll();

    void beginLyricEdit (int noteIndex);
    void commitLyricEdit();

    // 編集結果の通知。呼び出し側(PluginEditor等)でScheduledSongNoteへ変換して
    // processor.setEditedNotes()へ渡すことを想定。
    std::function<void (const std::vector<PianoRollNote>&, double tempoBpm)> onNotesChanged;

private:
    enum class DragMode { none, moveNotes, resizeLeft, resizeRight, rubberBand, loopRange };

    struct HitResult
    {
        int  index = -1;
        bool onLeftEdge = false;
        bool onRightEdge = false;
    };

    void timerCallback() override;

    double xToTimeSec (float x) const;
    float  timeSecToX (double t) const;
    int    yToNoteNumber (float y) const;
    float  noteNumberToY (int noteNumber) const;
    double snapTimeSec (double t) const;
    double secondsPerBeat() const { return 60.0 / juce::jmax (1.0, tempoBpm); }

    void recalculateContentSize();
    void notifyChanged();
    HitResult hitTestNote (juce::Point<float> pos) const;

    std::vector<PianoRollNote> notes;
    double tempoBpm = 120.0;

    double pixelsPerSecond = 80.0;
    double pixelsPerRow    = 16.0;
    int    snapDivisionsPerBeat = 4;

    bool   loopRangeValid = false;
    double loopStartSec = 0.0;
    double loopEndSec   = 0.0;
    double loopDragAnchorSec = 0.0;

    DragMode dragMode = DragMode::none;
    juce::Point<float> dragStartPos;
    std::vector<PianoRollNote> dragOriginalNotes;
    bool notesChangedDuringDrag = false;
    juce::Rectangle<float> rubberBandRect;

    int lyricEditingIndex = -1;
    std::unique_ptr<juce::TextEditor> lyricEditor;

    std::function<double()> playheadProvider;
    int nextNoteId = 1;

    static constexpr int    kLowestNote  = 36;
    static constexpr int    kHighestNote = 84;
    static constexpr int    kKeyboardWidth = 50;
    static constexpr int    kRulerHeight   = 24;
    static constexpr int    kEdgeGrabPx    = 6;
    static constexpr double kMinNoteDurationSec = 0.05;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR (PianoRollComponent)
};
