// PianoRollComponent.cpp

#include "PianoRollComponent.h"
#include <cmath>
#include <algorithm>

namespace
{
    bool isBlackKeyPitchClass (int noteNumber)
    {
        static const bool blackKeys[12] = { false, true, false, true, false,
                                             false, true, false, true, false, true, false };
        return blackKeys[((noteNumber % 12) + 12) % 12];
    }

    juce::String noteNameFor (int noteNumber)
    {
        static const char* names[12] = { "C", "C#", "D", "D#", "E", "F",
                                          "F#", "G", "G#", "A", "A#", "B" };
        const int octave = noteNumber / 12 - 1;
        return juce::String (names[((noteNumber % 12) + 12) % 12]) + juce::String (octave);
    }
}

PianoRollComponent::PianoRollComponent()
{
    setWantsKeyboardFocus (true);
    setOpaque (true);
    recalculateContentSize();
}

//==============================================================================
void PianoRollComponent::setNotes (std::vector<PianoRollNote> newNotes)
{
    notes = std::move (newNotes);

    // 外部から読み込んだノートのidと衝突しないよう、以降に生成するIDの
    // 開始値を調整する（setNotes()は主にプロセッサからの初期読み込み/
    // UST再読込で呼ばれ、その後の新規ノート作成はこのコンポーネント内で行うため）。
    for (auto& n : notes)
        nextNoteId = juce::jmax (nextNoteId, n.id + 1);

    recalculateContentSize();
    repaint();
}

void PianoRollComponent::setTempo (double bpm)
{
    tempoBpm = juce::jmax (1.0, bpm);
    recalculateContentSize();
    repaint();
}

void PianoRollComponent::setPlayheadSecondsProvider (std::function<double()> provider)
{
    playheadProvider = std::move (provider);
    if (playheadProvider != nullptr)
        startTimerHz (30);
    else
        stopTimer();
}

void PianoRollComponent::clearLoopRange()
{
    loopRangeValid = false;
    repaint();
}

void PianoRollComponent::setHorizontalZoom (double newPixelsPerSecond)
{
    pixelsPerSecond = juce::jlimit (10.0, 800.0, newPixelsPerSecond);
    recalculateContentSize();
    repaint();
}

void PianoRollComponent::setVerticalZoom (double newPixelsPerRow)
{
    pixelsPerRow = juce::jlimit (6.0, 40.0, newPixelsPerRow);
    recalculateContentSize();
    repaint();
}

//==============================================================================
double PianoRollComponent::xToTimeSec (float x) const
{
    return juce::jmax (0.0, (double) (x - kKeyboardWidth) / pixelsPerSecond);
}

float PianoRollComponent::timeSecToX (double t) const
{
    return (float) (kKeyboardWidth + t * pixelsPerSecond);
}

int PianoRollComponent::yToNoteNumber (float y) const
{
    const int row = (int) std::floor ((double) (y - kRulerHeight) / pixelsPerRow);
    return kHighestNote - row;
}

float PianoRollComponent::noteNumberToY (int noteNumber) const
{
    const int row = kHighestNote - noteNumber;
    return (float) (kRulerHeight + row * pixelsPerRow);
}

double PianoRollComponent::snapTimeSec (double t) const
{
    const double step = secondsPerBeat() / (double) juce::jmax (1, snapDivisionsPerBeat);
    const double snapped = std::round (t / step) * step;
    return juce::jmax (0.0, snapped);
}

void PianoRollComponent::recalculateContentSize()
{
    double latestEnd = 30.0; // 最低でも30秒分は表示領域を確保
    for (auto& n : notes)
        latestEnd = juce::jmax (latestEnd, n.endTimeSec());

    const int width  = kKeyboardWidth + (int) ((latestEnd + 4.0) * pixelsPerSecond);
    const int height = kRulerHeight + (int) ((kHighestNote - kLowestNote + 1) * pixelsPerRow);

    setSize (width, height);
}

void PianoRollComponent::notifyChanged()
{
    if (onNotesChanged != nullptr)
        onNotesChanged (notes, tempoBpm);
}

//==============================================================================
void PianoRollComponent::paint (juce::Graphics& g)
{
    const auto bounds = getLocalBounds();
    auto& lf = getLookAndFeel();
    g.fillAll (lf.findColour (VoseColourIds::canvasBackground));

    // --- 行の背景（黒鍵の行を少し暗く塗って読みやすくする） ---
    const auto baseBg = lf.findColour (VoseColourIds::canvasBackground);
    const auto altBg  = lf.findColour (VoseColourIds::canvasRowAlt);
    for (int n = kLowestNote; n <= kHighestNote; ++n)
    {
        const float y = noteNumberToY (n);
        juce::Rectangle<float> row ((float) kKeyboardWidth, y, (float) (bounds.getWidth() - kKeyboardWidth), (float) pixelsPerRow);
        g.setColour (isBlackKeyPitchClass (n) ? altBg : baseBg.brighter (0.03f));
        g.fillRect (row);
    }

    // --- 縦グリッド線（スナップ単位 / 拍 / 小節） ---
    const double step = secondsPerBeat() / (double) juce::jmax (1, snapDivisionsPerBeat);
    const double totalSec = xToTimeSec ((float) bounds.getWidth());
    int subdivisionIndex = 0;
    for (double t = 0.0; t <= totalSec; t += step, ++subdivisionIndex)
    {
        const float x = timeSecToX (t);
        const bool isBeat = (subdivisionIndex % snapDivisionsPerBeat) == 0;
        const int beatIndex = subdivisionIndex / snapDivisionsPerBeat;
        const bool isMeasure = isBeat && (beatIndex % 4 == 0);

        g.setColour (isMeasure ? lf.findColour (VoseColourIds::canvasGridMeasure)
                                : (isBeat ? lf.findColour (VoseColourIds::canvasGridBeat)
                                          : lf.findColour (VoseColourIds::canvasGrid)));
        g.drawVerticalLine ((int) x, (float) kRulerHeight, (float) bounds.getHeight());
    }

    // --- ループ範囲のハイライト ---
    if (loopRangeValid)
    {
        juce::Rectangle<float> loopRect (timeSecToX (loopStartSec), (float) kRulerHeight,
                                          timeSecToX (loopEndSec) - timeSecToX (loopStartSec),
                                          (float) (bounds.getHeight() - kRulerHeight));
        g.setColour (lf.findColour (VoseColourIds::accentPrimary).withAlpha (0.2f));
        g.fillRect (loopRect);
    }

    // --- ノート ---
    const auto noteColour = lf.findColour (VoseColourIds::accentPrimary);
    const auto noteSelectedColour = lf.findColour (VoseColourIds::noteSelected);
    for (auto& n : notes)
    {
        juce::Rectangle<float> r (timeSecToX (n.startTimeSec), noteNumberToY (n.noteNum),
                                   juce::jmax (2.0f, timeSecToX (n.endTimeSec()) - timeSecToX (n.startTimeSec)),
                                   (float) pixelsPerRow);
        r.reduce (0.5f, 1.0f);

        g.setColour (n.selected ? noteSelectedColour : noteColour);
        g.fillRoundedRectangle (r, 2.0f);
        g.setColour (juce::Colours::black.withAlpha (0.6f));
        g.drawRoundedRectangle (r, 2.0f, 1.0f);

        if (r.getWidth() > 14.0f && pixelsPerRow >= 10.0)
        {
            g.setColour (juce::Colours::black);
            g.setFont (juce::jmin (12.0f, (float) pixelsPerRow - 4.0f));
            g.drawFittedText (n.lyric, r.reduced (2.0f).toNearestInt(), juce::Justification::centredLeft, 1);
        }
    }

    // --- ラバーバンド選択矩形 ---
    if (dragMode == DragMode::rubberBand && ! rubberBandRect.isEmpty())
    {
        const auto accent = lf.findColour (VoseColourIds::accentPrimary);
        g.setColour (accent.withAlpha (0.3f));
        g.fillRect (rubberBandRect);
        g.setColour (accent);
        g.drawRect (rubberBandRect, 1.0f);
    }

    // --- 鍵盤サイドバー（スクロール内容と一体化した簡易版。上部コメント参照） ---
    g.setColour (lf.findColour (VoseColourIds::canvasHeaderBackground));
    g.fillRect (0, kRulerHeight, kKeyboardWidth, bounds.getHeight() - kRulerHeight);
    const auto keyWhite = lf.findColour (VoseColourIds::keyboardWhite);
    const auto keyBlack = lf.findColour (VoseColourIds::keyboardBlack);
    for (int n = kLowestNote; n <= kHighestNote; ++n)
    {
        const float y = noteNumberToY (n);
        juce::Rectangle<float> key (0.0f, y, (float) kKeyboardWidth, (float) pixelsPerRow);
        g.setColour (isBlackKeyPitchClass (n) ? keyBlack : keyWhite);
        g.fillRect (key.reduced (0.0f, 0.5f));

        if (pixelsPerRow >= 11.0 && (n % 12) == 0) // C音だけラベルを出す（詰まりすぎ防止）
        {
            g.setColour (isBlackKeyPitchClass (n) ? keyWhite : keyBlack);
            g.setFont (juce::jmin (10.0f, (float) pixelsPerRow - 3.0f));
            g.drawFittedText (noteNameFor (n), key.toNearestInt(), juce::Justification::centredLeft, 1);
        }
    }

    // --- 上部ルーラー ---
    g.setColour (lf.findColour (VoseColourIds::canvasHeaderBackground));
    g.fillRect (0, 0, bounds.getWidth(), kRulerHeight);
    g.setColour (lf.findColour (VoseColourIds::canvasGridMeasure).withAlpha (0.9f));
    g.setFont (11.0f);
    for (double t = 0.0, beat = 0.0; t <= totalSec; t += secondsPerBeat(), beat += 1.0)
    {
        const float x = timeSecToX (t);
        if (std::fmod (beat, 4.0) < 0.001)
            g.drawText (juce::String ((int) (beat / 4.0) + 1), (int) x + 2, 0, 40, kRulerHeight,
                        juce::Justification::centredLeft);
        g.drawVerticalLine ((int) x, 0.0f, (float) kRulerHeight);
    }

    // --- 再生ヘッド ---
    if (playheadProvider != nullptr)
    {
        const double posSec = playheadProvider();
        const float x = timeSecToX (posSec);
        g.setColour (juce::Colours::red);
        g.drawVerticalLine ((int) x, 0.0f, (float) bounds.getHeight());
    }
}

void PianoRollComponent::resized()
{
    // コンテンツサイズは recalculateContentSize() が管理するため、
    // ここでは特別な子コンポーネントの再配置は不要（歌詞エディタは編集開始時に配置）。
}

//==============================================================================
PianoRollComponent::HitResult PianoRollComponent::hitTestNote (juce::Point<float> pos) const
{
    for (int i = (int) notes.size() - 1; i >= 0; --i)
    {
        const auto& n = notes[(size_t) i];
        const float y0 = noteNumberToY (n.noteNum);
        if (pos.y < y0 || pos.y >= y0 + (float) pixelsPerRow)
            continue;

        const float x0 = timeSecToX (n.startTimeSec);
        const float x1 = timeSecToX (n.endTimeSec());
        if (pos.x < x0 || pos.x > x1)
            continue;

        HitResult r;
        r.index = i;
        r.onRightEdge = (x1 - pos.x) <= (float) kEdgeGrabPx;
        r.onLeftEdge  = (! r.onRightEdge) && ((pos.x - x0) <= (float) kEdgeGrabPx);
        return r;
    }
    return {};
}

//==============================================================================
void PianoRollComponent::mouseDown (const juce::MouseEvent& event)
{
    if (lyricEditor != nullptr)
        commitLyricEdit();

    const auto pos = event.position;

    // --- 上部ルーラー: ループ範囲のドラッグ開始 ---
    if (pos.y < (float) kRulerHeight && pos.x >= (float) kKeyboardWidth)
    {
        dragMode = DragMode::loopRange;
        loopDragAnchorSec = snapTimeSec (xToTimeSec (pos.x));
        loopStartSec = loopEndSec = loopDragAnchorSec;
        loopRangeValid = true;
        repaint();
        return;
    }

    // 鍵盤サイドバーのクリックは無視（将来: プレビュー再生に使える）
    if (pos.x < (float) kKeyboardWidth)
        return;

    const auto hit = hitTestNote (pos);

    // --- 右クリック: ノート削除 ---
    if (event.mods.isRightButtonDown())
    {
        if (hit.index >= 0)
        {
            notes.erase (notes.begin() + hit.index);
            recalculateContentSize();
            notifyChanged();
            repaint();
        }
        return;
    }

    if (hit.index >= 0)
    {
        const bool alreadySelected = notes[(size_t) hit.index].selected;

        if (event.mods.isShiftDown())
        {
            notes[(size_t) hit.index].selected = ! alreadySelected;
        }
        else if (! alreadySelected)
        {
            deselectAll();
            notes[(size_t) hit.index].selected = true;
        }
        // 既に選択済みグループの一員をShiftなしでクリックした場合は選択状態を維持
        // （そのままグループドラッグに入れるようにするため）。

        if (event.getNumberOfClicks() >= 2 && ! hit.onLeftEdge && ! hit.onRightEdge)
        {
            beginLyricEdit (hit.index);
            dragMode = DragMode::none;
            repaint();
            return;
        }

        dragOriginalNotes = notes;
        dragStartPos = pos;
        notesChangedDuringDrag = false;
        dragMode = hit.onRightEdge ? DragMode::resizeRight
                 : hit.onLeftEdge  ? DragMode::resizeLeft
                                   : DragMode::moveNotes;
        repaint();
        return;
    }

    // --- 空白ダブルクリック: 新規ノート作成 ---
    if (event.getNumberOfClicks() >= 2)
    {
        PianoRollNote n;
        n.id = nextNoteId++;
        n.startTimeSec = snapTimeSec (xToTimeSec (pos.x));
        n.durationSec = secondsPerBeat();
        n.noteNum = juce::jlimit ((int) kLowestNote, (int) kHighestNote, yToNoteNumber (pos.y));
        n.lyric = "a";

        if (! event.mods.isShiftDown())
            deselectAll();
        n.selected = true;

        notes.push_back (n);
        recalculateContentSize();
        notifyChanged();
        repaint();
        return;
    }

    // --- 空白ドラッグ: ラバーバンド選択 ---
    if (! event.mods.isShiftDown())
        deselectAll();

    dragOriginalNotes = notes; // 選択状態のベースラインとして保持
    dragMode = DragMode::rubberBand;
    dragStartPos = pos;
    rubberBandRect = {};
    repaint();
}

void PianoRollComponent::mouseDrag (const juce::MouseEvent& event)
{
    const auto pos = event.position;

    switch (dragMode)
    {
        case DragMode::moveNotes:
        {
            // 基準ノート（最初に選択されているノート）でスナップ位置を決め、
            // グループ全体を同じオフセットで動かす。
            int refIndex = -1;
            for (size_t i = 0; i < dragOriginalNotes.size(); ++i)
                if (dragOriginalNotes[i].selected) { refIndex = (int) i; break; }
            if (refIndex < 0)
                break;

            const double rawDt = xToTimeSec (pos.x) - xToTimeSec (dragStartPos.x);
            const double refNewStart = snapTimeSec (dragOriginalNotes[(size_t) refIndex].startTimeSec + rawDt);
            const double actualDt = refNewStart - dragOriginalNotes[(size_t) refIndex].startTimeSec;

            const int refRowDelta = yToNoteNumber (dragStartPos.y) - yToNoteNumber (pos.y);
            const int noteDelta = -refRowDelta; // 下にドラッグ = 音程が下がる

            for (size_t i = 0; i < notes.size() && i < dragOriginalNotes.size(); ++i)
            {
                if (! dragOriginalNotes[i].selected)
                    continue;
                notes[i].startTimeSec = juce::jmax (0.0, dragOriginalNotes[i].startTimeSec + actualDt);
                notes[i].noteNum = juce::jlimit ((int) kLowestNote, (int) kHighestNote,
                                                  dragOriginalNotes[i].noteNum + noteDelta);
            }
            recalculateContentSize();
            notesChangedDuringDrag = true;
            repaint();
            break;
        }

        case DragMode::resizeRight:
        {
            const double rawDt = xToTimeSec (pos.x) - xToTimeSec (dragStartPos.x);
            for (size_t i = 0; i < notes.size() && i < dragOriginalNotes.size(); ++i)
            {
                if (! dragOriginalNotes[i].selected)
                    continue;
                const auto& orig = dragOriginalNotes[i];
                const double newEnd = juce::jmax (orig.startTimeSec + kMinNoteDurationSec,
                                                   snapTimeSec (orig.endTimeSec() + rawDt));
                notes[i].durationSec = newEnd - orig.startTimeSec;
            }
            recalculateContentSize();
            notesChangedDuringDrag = true;
            repaint();
            break;
        }

        case DragMode::resizeLeft:
        {
            const double rawDt = xToTimeSec (pos.x) - xToTimeSec (dragStartPos.x);
            for (size_t i = 0; i < notes.size() && i < dragOriginalNotes.size(); ++i)
            {
                if (! dragOriginalNotes[i].selected)
                    continue;
                const auto& orig = dragOriginalNotes[i];
                double newStart = snapTimeSec (orig.startTimeSec + rawDt);
                newStart = juce::jlimit (0.0, orig.endTimeSec() - kMinNoteDurationSec, newStart);
                notes[i].startTimeSec = newStart;
                notes[i].durationSec = orig.endTimeSec() - newStart;
            }
            notesChangedDuringDrag = true;
            repaint();
            break;
        }

        case DragMode::rubberBand:
        {
            rubberBandRect = juce::Rectangle<float> (dragStartPos, pos);
            for (size_t i = 0; i < notes.size() && i < dragOriginalNotes.size(); ++i)
            {
                juce::Rectangle<float> noteRect (timeSecToX (notes[i].startTimeSec), noteNumberToY (notes[i].noteNum),
                                                  timeSecToX (notes[i].endTimeSec()) - timeSecToX (notes[i].startTimeSec),
                                                  (float) pixelsPerRow);
                const bool intersects = rubberBandRect.intersects (noteRect);
                notes[i].selected = dragOriginalNotes[i].selected || intersects;
            }
            repaint();
            break;
        }

        case DragMode::loopRange:
        {
            const double t = snapTimeSec (xToTimeSec (pos.x));
            loopStartSec = juce::jmin (loopDragAnchorSec, t);
            loopEndSec = juce::jmax (loopDragAnchorSec, t);
            repaint();
            break;
        }

        default:
            break;
    }
}

void PianoRollComponent::mouseUp (const juce::MouseEvent&)
{
    if ((dragMode == DragMode::moveNotes || dragMode == DragMode::resizeLeft || dragMode == DragMode::resizeRight)
        && notesChangedDuringDrag)
    {
        notifyChanged();
    }

    if (dragMode == DragMode::loopRange && (loopEndSec - loopStartSec) < 0.01)
        loopRangeValid = false;

    dragMode = DragMode::none;
    rubberBandRect = {};
    repaint();
}

void PianoRollComponent::mouseMove (const juce::MouseEvent& event)
{
    const auto hit = hitTestNote (event.position);
    if (hit.index >= 0 && (hit.onLeftEdge || hit.onRightEdge))
        setMouseCursor (juce::MouseCursor::LeftRightResizeCursor);
    else if (hit.index >= 0)
        setMouseCursor (juce::MouseCursor::DraggingHandCursor);
    else
        setMouseCursor (juce::MouseCursor::NormalCursor);
}

void PianoRollComponent::mouseWheelMove (const juce::MouseEvent& event, const juce::MouseWheelDetails& wheel)
{
    if (event.mods.isCommandDown() || event.mods.isCtrlDown())
    {
        const double factor = wheel.deltaY > 0.0f ? 1.1 : (1.0 / 1.1);
        setHorizontalZoom (pixelsPerSecond * factor);
        return;
    }

    // 通常のホイール/Shiftホイールは親のViewportに任せる（縦横スクロール）。
    Component::mouseWheelMove (event, wheel);
}

bool PianoRollComponent::keyPressed (const juce::KeyPress& key)
{
    if (key == juce::KeyPress::deleteKey || key == juce::KeyPress::backspaceKey)
    {
        deleteSelectedNotes();
        return true;
    }
    if (key == juce::KeyPress ('a', juce::ModifierKeys::commandModifier, 0)
        || key == juce::KeyPress ('a', juce::ModifierKeys::ctrlModifier, 0))
    {
        selectAll();
        return true;
    }
    if (key == juce::KeyPress::escapeKey)
    {
        deselectAll();
        repaint();
        return true;
    }
    return false;
}

//==============================================================================
void PianoRollComponent::deleteSelectedNotes()
{
    const auto before = notes.size();
    notes.erase (std::remove_if (notes.begin(), notes.end(),
                                  [] (const PianoRollNote& n) { return n.selected; }),
                 notes.end());
    if (notes.size() != before)
    {
        recalculateContentSize();
        notifyChanged();
        repaint();
    }
}

void PianoRollComponent::selectAll()
{
    for (auto& n : notes)
        n.selected = true;
    repaint();
}

void PianoRollComponent::deselectAll()
{
    for (auto& n : notes)
        n.selected = false;
}

void PianoRollComponent::beginLyricEdit (int noteIndex)
{
    if (noteIndex < 0 || noteIndex >= (int) notes.size())
        return;

    commitLyricEdit(); // 既存の編集中エディタがあれば確定してから開始

    lyricEditingIndex = noteIndex;
    lyricEditor = std::make_unique<juce::TextEditor>();
    lyricEditor->setText (notes[(size_t) noteIndex].lyric, juce::dontSendNotification);
    lyricEditor->setSelectAllWhenFocused (true);
    lyricEditor->setFont (juce::jmin (13.0f, (float) pixelsPerRow - 2.0f));

    const auto& n = notes[(size_t) noteIndex];
    juce::Rectangle<int> r ((int) timeSecToX (n.startTimeSec), (int) noteNumberToY (n.noteNum),
                             juce::jmax (40, (int) (timeSecToX (n.endTimeSec()) - timeSecToX (n.startTimeSec))),
                             (int) pixelsPerRow);
    lyricEditor->setBounds (r);

    lyricEditor->onReturnKey = [this] { commitLyricEdit(); };
    lyricEditor->onFocusLost = [this] { commitLyricEdit(); };
    lyricEditor->onEscapeKey = [this]
    {
        // Escapeは変更を破棄して閉じるだけ（歌詞は元のまま）。
        lyricEditingIndex = -1;
        removeChildComponent (lyricEditor.get());
        lyricEditor.reset();
        repaint();
    };

    addAndMakeVisible (*lyricEditor);
    lyricEditor->grabKeyboardFocus();
}

void PianoRollComponent::commitLyricEdit()
{
    if (lyricEditor == nullptr)
        return;

    if (lyricEditingIndex >= 0 && lyricEditingIndex < (int) notes.size())
    {
        auto text = lyricEditor->getText().trim();
        notes[(size_t) lyricEditingIndex].lyric = text.isEmpty() ? juce::String ("a") : text;
        notifyChanged();
    }

    lyricEditingIndex = -1;
    removeChildComponent (lyricEditor.get());
    lyricEditor.reset();
    repaint();
}

void PianoRollComponent::timerCallback()
{
    if (playheadProvider != nullptr)
        repaint(); // PoCスコープでは全体再描画。将来は再生ヘッド帯だけ repaint する最適化余地あり。
}
