// RenderEngine.h
//
// 【役割の変更】StreamingVoice.h が本物のリアルタイム再生を担うようになったため、
// このファイルはもう processBlock の再生パスには使わない。
// execute_render はやはりファイル書き出し(オフラインバウンド)専用の関数なので、
// 「WAVに書き出す」「プレビュー用に事前レンダリングする」といった
// 非リアルタイムの用途（フェーズ4のエクスポート機能等）のために残している。
//
// execute_render は「関数」ではなく「バッチジョブ」（マルチスレッド解析 + ファイル書き出し）
// なので、絶対にオーディオスレッドから呼んではいけない。
// このクラスは専用スレッド上でのみ execute_render を実行し、
// 完了したらレンダリング結果の WAV を juce::AudioSampleBuffer に読み込んで
// アトミックにポインタを差し替える。呼び出し側はそのポインタを
// 読むだけ（ロックフリー）。

#pragma once

#include <juce_audio_formats/juce_audio_formats.h>
#include <juce_audio_basics/juce_audio_basics.h>
#include <atomic>
#include <vector>
#include "VoseBridge.h"

struct VoseNoteInput
{
    juce::String wavPath;      // oto_map から解決済みのサンプルパス
    std::vector<double> pitchCurve;    // Hz（ベースピッチのみ。ポルタメント/ビブラートは
                                        // ここでは焼き込まず、コア側のネイティブ処理に任せる）
    std::vector<double> genderCurve;   // 0..1
    std::vector<double> tensionCurve;  // 0..1
    std::vector<double> breathCurve;   // 0..1

    // ネイティブ対応分。execute_render(NoteEvent)専用 —
    // ストリーミングAPI(VoseStreamNote)には同等フィールドが無いため渡せない
    // (docs/INTENSITY_API_PROPOSAL.md と同様、ストリーミング側への追加は別途提案が必要)。
    std::vector<double> portamentoOffsetsCents; // セント単位。pitchCurveと同じ解像度
    std::vector<double> vibratoDepthCurve;      // 1.0 = デフォルト深さ（vose_core既定）
    std::vector<double> vibratoRateCurve;       // Hz
};

class RenderEngine : private juce::Thread
{
public:
    RenderEngine() : juce::Thread ("VoseRenderThread") {}
    ~RenderEngine() override { stopThread (5000); }

    bool loadCore (const juce::File& pluginBinaryDir)
    {
        return core.load (pluginBinaryDir);
    }

    // UIスレッド／MIDIハンドラから呼ぶ。すでにレンダリング中なら
    // 「もう一度最新の内容で」フラグだけ立てて、今のジョブが終わったら再実行する。
    void requestRender (std::vector<VoseNoteInput> notesToRender)
    {
        {
            const juce::ScopedLock sl (jobLock);
            pendingNotes = std::move (notesToRender);
            hasPendingJob = true;
        }
        if (! isThreadRunning())
            startThread (juce::Thread::Priority::normal);
        else
            notify();
    }

    // processBlock から呼ぶ。ロックフリーでスナップショットを取得する。
    const juce::AudioBuffer<float>* getRenderedBuffer() const
    {
        return readyBuffer.load();
    }

    bool isRendering() const { return rendering.load(); }

private:
    void run() override
    {
        while (! threadShouldExit())
        {
            std::vector<VoseNoteInput> job;
            {
                const juce::ScopedLock sl (jobLock);
                if (! hasPendingJob)
                {
                    // 新しい依頼が来るまで待機（processBlockをブロックしない）
                }
                else
                {
                    job = std::move (pendingNotes);
                    hasPendingJob = false;
                }
            }

            if (! job.empty())
            {
                rendering = true;
                renderOnThisThread (job);
                rendering = false;
            }

            // 待機。requestRender() 内の notify() で早期に起きる。
            wait (hasPendingJob ? 0 : 200);
        }
    }

    void renderOnThisThread (const std::vector<VoseNoteInput>& notes)
    {
        if (! core.isLoaded())
            return;

        // ---- VoseNoteInput -> NoteEvent (C ABI) へ変換 ----
        // 生ポインタの寿命は execute_render 呼び出しが終わるまで
        // このスコープ内で保証する（ctypes版の _temp_refs と同じ考え方）。
        std::vector<NoteEvent> cNotes (notes.size());
        std::vector<juce::CharPointer_UTF8> pathRefs; // wav_path の寿命確保用
        pathRefs.reserve (notes.size());

        for (size_t i = 0; i < notes.size(); ++i)
        {
            const auto& n = notes[i];
            pathRefs.push_back (n.wavPath.toUTF8());

            // vose_core.h の NoteEvent は非const の double* を要求する
            // (レガシーC ABIでconst性が付いていない)。execute_render側は
            // これらのカーブを読み取り専用で使うだけなので const_cast で対応する。
            cNotes[i].wav_path             = pathRefs.back().getAddress();
            cNotes[i].pitch_curve          = const_cast<double*> (n.pitchCurve.data());
            cNotes[i].pitch_length         = (int) n.pitchCurve.size();
            cNotes[i].gender_curve         = const_cast<double*> (n.genderCurve.data());
            cNotes[i].tension_curve        = const_cast<double*> (n.tensionCurve.data());
            cNotes[i].breath_curve         = const_cast<double*> (n.breathCurve.data());

            // ネイティブ対応（vose_core.cpp 1070-1080行目で実際に使われているのを確認済み）。
            // ただし apply_vibrato() 自体は「ノート後半50%固定・フェードアウト無し・
            // phase/height無し」という簡易モデルで、USTのVBR(length/fade_in/fade_out/
            // phase/height)を完全には再現できない。UST由来のノートはこれまで通り
            // PitchCurveBuilder.h側でビブラートを焼き込んだpitch_curveを使うこと
            // （このフィールドに同時に値を入れると二重適用になるので注意）。
            // ここはVBRデータを持たない将来的な用途向けの空きインフラとして残す。
            cNotes[i].vibrato_depth_curve  = n.vibratoDepthCurve.empty()
                                              ? nullptr : const_cast<double*> (n.vibratoDepthCurve.data());
            cNotes[i].vibrato_rate_curve   = n.vibratoRateCurve.empty()
                                              ? nullptr : const_cast<double*> (n.vibratoRateCurve.data());
            cNotes[i].vibrato_curve_length = n.vibratoDepthCurve.empty()
                                              ? 0 : (int) n.vibratoDepthCurve.size();

            // ポルタメントは逆にネイティブ側が簡略化されていない汎用処理
            // (cents = resample_curve(...); base_f0_val *= pow(2, cents/1200))
            // なので、忠実度の劣化なくこちらを使ってよい。
            cNotes[i].portamento_offsets   = n.portamentoOffsetsCents.empty()
                                              ? nullptr : const_cast<double*> (n.portamentoOffsetsCents.data());
            cNotes[i].portamento_length    = n.portamentoOffsetsCents.empty()
                                              ? 0 : (int) n.portamentoOffsetsCents.size();
            // UST表現値を持たないこのC++ API経路ではUTAU既定値を使う。
            // NoteEventはvalue-initializeされるため、未設定のままだと
            // intensity=0（完全ミュート）/modulation=0になってしまう。
            cNotes[i].intensity           = 100.0;
            cNotes[i].modulation          = 100.0;
        }

        auto tempFile = juce::File::getSpecialLocation (juce::File::tempDirectory)
                            .getChildFile ("vose_render_" + juce::String (juce::Random::getSystemRandom().nextInt64()) + ".wav");

        // mode_flag=0 (無料版相当: 16bit)。Pro切り替えはパラメータ化して後で渡す。
        core.execute_render (cNotes.data(), (int) cNotes.size(),
                              tempFile.getFullPathName().toRawUTF8(), 0);

        loadRenderedFile (tempFile);
        tempFile.deleteFile();
    }

    void loadRenderedFile (const juce::File& wavFile)
    {
        if (! wavFile.existsAsFile())
            return;

        juce::AudioFormatManager fm;
        fm.registerBasicFormats();
        std::unique_ptr<juce::AudioFormatReader> reader (fm.createReaderFor (wavFile));
        if (reader == nullptr)
            return;

        auto* newBuffer = new juce::AudioBuffer<float> ((int) reader->numChannels,
                                                          (int) reader->lengthInSamples);
        reader->read (newBuffer, 0, (int) reader->lengthInSamples, 0, true, true);

        // 古いバッファは即座に delete せず、次にどこからも参照されなくなってから解放する。
        // ここでは簡略化のため、直前のバッファを一世代だけ保持して差し替える。
        auto* old = readyBuffer.exchange (newBuffer);
        buffersPendingDeletion.add (old); // GC的に、次のレンダリング完了時にまとめて破棄
        while (buffersPendingDeletion.size() > 2)
        {
            delete buffersPendingDeletion.getFirst();
            buffersPendingDeletion.remove (0);
        }
    }

    VoseCoreLibrary core;

    juce::CriticalSection jobLock;
    std::vector<VoseNoteInput> pendingNotes;
    bool hasPendingJob = false;

    std::atomic<bool> rendering { false };
    std::atomic<juce::AudioBuffer<float>*> readyBuffer { nullptr };
    juce::Array<juce::AudioBuffer<float>*> buffersPendingDeletion;
};
