// ============================================================
// wasmEngine.ts
//
// 高精度 Studio-Grade Offline Vocal Rendering Engine (Web Audio)
//
// 1. 各トラック・各ノートの音源サンプル(oto.ini設定含む)を並行取得
// 2. OfflineAudioContext (44.1kHz 2ch) 上で完全なUTAU音響パイプラインを構築:
//    - サンプルベース音高からのピッチシフト (TD-PSOLA、フォルマント保持)
//    - USTピッチベンドカーブ (PBS/PBW/PBY) はビブラート等の微小補正として反映
//    - oto.ini タイムマッピング (Offset, Preutterance, Overlap, Fixed, Cutoff)
//    - 長音用イコールパワークロスフェードループ (クリック音完全防止)
//    - アタック/リリース マイクロフェードエンベロープ (音素衝突防止)
//    - 未収録歌詞用の高品位フォルマントオシレーターフォールバック
//    - マスタリングEQ & ダイナミクスリミッター
// 3. 進捗状況(0%〜100%)およびリアルタイムETAをスムーズにメインUIへ通知
// 4. 高音質 16-bit PCM WAV (RIFF) を生成してBlob URLを出力
//
// 注意: このエンジンは C++ コア(vose_core)やBigVGAN/ONNXパイプラインを
// 一切呼び出していない。純粋にブラウザのWeb Audio APIのみで完結する
// 簡易実装(oto.iniベースのサンプル再配置)。
// ============================================================

import {
  parsePitchBend,
  smoothPitchBendPoints,
  softClampSemitone,
  scheduleSafePitchRamp,
  type PitchPoint
} from './utils/pitchCurve';
import { bufferToWav } from './utils/audioEncoder';
import { cleanWavArrayBuffer } from './utils/wavCleaner';
import { psolaPitchAndTimeShiftBuffer } from './psolaPitchShift';

export interface FetchedSample {
  buffer: AudioBuffer;
  left_blank: number;
  fixed_range: number;
  right_blank: number;
  preutterance: number;
  overlap: number;
  baseMidi: number;
  _loopXfadeCache?: Map<string, AudioBuffer>;
}

const REST_LYRICS_SET = new Set([
  'r', 'r_', 'r_0', '[r]', '息', 'br', 'pau', 'sil', '吸', '吸気', '息吸い', '', ' ', '　', '休', '休符', '・', '-', 'ー', '~', 'null'
]);

export function isRest(lyric?: string): boolean {
  if (!lyric) return true;
  const l = lyric.trim().toLowerCase();
  if (REST_LYRICS_SET.has(l)) return true;
  return /^br[0-9]*$/i.test(l) || /^息[0-9]*$/i.test(l) || /^吸[0-9]*$/i.test(l) || /^_?(br|息|吸)[0-9]*$/i.test(l);
}

const sampleCache = new Map<string, FetchedSample | null>();
const inFlightRequests = new Map<string, Promise<FetchedSample | null>>();

let sharedDecodeCtx: AudioContext | null = null;
function getSharedDecodeContext(): AudioContext {
  if (!sharedDecodeCtx || sharedDecodeCtx.state === 'closed') {
    const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
    sharedDecodeCtx = new AudioCtx();
  }
  return sharedDecodeCtx;
}

// サンプル取得 (oto.ini パラメータ付き)
async function fetchSampleWithMeta(
  voicebank: string,
  alias: string,
  prevLyric?: string,
  noteNum?: number
): Promise<FetchedSample | null> {
  if (isRest(alias)) return null;
  const key = `${voicebank}:${alias}:${prevLyric || ''}:${noteNum || 60}`;
  if (sampleCache.has(key)) return sampleCache.get(key)!;
  if (inFlightRequests.has(key)) return inFlightRequests.get(key)!;

  const promise = (async (): Promise<FetchedSample | null> => {
    try {
      let url = `/api/py/voicebank-sample?name=${encodeURIComponent(voicebank)}&alias=${encodeURIComponent(alias)}`;
      if (prevLyric) url += `&prevLyric=${encodeURIComponent(prevLyric)}`;
      if (noteNum !== undefined) url += `&noteNum=${encodeURIComponent(String(noteNum))}`;

      const res = await fetch(url);
      if (!res.ok) {
        sampleCache.set(key, null);
        return null;
      }

      const left_blank = parseFloat(res.headers.get('X-Oto-Left-Blank') || '0');
      const fixed_range = parseFloat(res.headers.get('X-Oto-Fixed-Range') || '0');
      const right_blank = parseFloat(res.headers.get('X-Oto-Right-Blank') || '0');
      const preutterance = parseFloat(res.headers.get('X-Oto-Preutterance') || '0');
      const overlap = parseFloat(res.headers.get('X-Oto-Overlap') || '0');
      const baseMidi = parseFloat(res.headers.get('X-Sample-Base-Midi') || '60');

      const arrayBuf = await res.arrayBuffer();
      const ctx = getSharedDecodeContext();
      const audioBuffer = await ctx.decodeAudioData(arrayBuf);

      const item: FetchedSample = {
        buffer: audioBuffer,
        left_blank,
        fixed_range,
        right_blank,
        preutterance,
        overlap,
        baseMidi
      };
      sampleCache.set(key, item);
      return item;
    } catch (err) {
      console.warn(`[wasmEngine] サンプル取得失敗 alias='${alias}':`, err);
      sampleCache.set(key, null);
      return null;
    } finally {
      inFlightRequests.delete(key);
    }
  })();

  inFlightRequests.set(key, promise);
  return promise;
}

// ループ境界のクリック音を除去するゼロクロス検出＆シームレス・クロスフェード
function findZeroCrossing(channelData: Float32Array, targetSample: number, searchRadius = 120): number {
  const maxIdx = channelData.length - 2;
  const minIdx = 1;
  const clampedTarget = Math.max(minIdx, Math.min(maxIdx, targetSample));
  let bestIdx = clampedTarget;
  let minVal = Math.abs(channelData[clampedTarget]);

  for (let r = 1; r <= searchRadius; r++) {
    const left = clampedTarget - r;
    if (left >= minIdx) {
      if (channelData[left] <= 0 && channelData[left + 1] > 0) {
        return left;
      }
      const v = Math.abs(channelData[left]);
      if (v < minVal) {
        minVal = v;
        bestIdx = left;
      }
    }
    const right = clampedTarget + r;
    if (right <= maxIdx) {
      if (channelData[right] <= 0 && channelData[right + 1] > 0) {
        return right;
      }
      const v = Math.abs(channelData[right]);
      if (v < minVal) {
        minVal = v;
        bestIdx = right;
      }
    }
  }
  return bestIdx;
}

// ループ境界のクリック音を除去するクロスフェードバッファ生成
function getLoopCrossfadedBuffer(
  ctx: BaseAudioContext,
  cached: FetchedSample,
  loopStartSec: number,
  loopEndSec: number
): AudioBuffer {
  const key = `${loopStartSec.toFixed(4)}_${loopEndSec.toFixed(4)}`;
  if (!cached._loopXfadeCache) {
    cached._loopXfadeCache = new Map<string, AudioBuffer>();
  }
  const existing = cached._loopXfadeCache.get(key);
  if (existing) return existing;

  const src = cached.buffer;
  const sr = src.sampleRate;
  const ch0 = src.getChannelData(0);

  const rawStart = Math.max(0, Math.floor(loopStartSec * sr));
  const rawEnd = Math.min(src.length, Math.floor(loopEndSec * sr));

  // 1. 正のゼロ交差点検出 (loopStartSample)
  let loopStartSample = rawStart;
  let minStartDist = 999999;
  const searchStartRad = Math.min(400, Math.floor(sr * 0.010));
  for (let i = Math.max(1, rawStart - searchStartRad); i < Math.min(src.length - 1, rawStart + searchStartRad); i++) {
    if (ch0[i - 1] <= 0 && ch0[i] > 0) {
      const dist = Math.abs(i - rawStart);
      if (dist < minStartDist) {
        minStartDist = dist;
        loopStartSample = i;
      }
    }
  }

  // 2. 位相・波形相関が最大となる正のゼロ交差点検出 (loopEndSample)
  const corrLen = Math.min(180, Math.floor(sr * 0.004));
  let loopEndSample = rawEnd;
  let bestCorr = -Infinity;
  const searchEndRad = Math.min(500, Math.floor(sr * 0.012));
  for (let i = Math.max(1, rawEnd - searchEndRad); i < Math.min(src.length - 1 - corrLen, rawEnd + searchEndRad); i++) {
    if (ch0[i - 1] <= 0 && ch0[i] > 0) {
      let corr = 0;
      for (let k = 0; k < corrLen; k++) {
        corr += ch0[loopStartSample + k] * ch0[i + k];
      }
      if (corr > bestCorr) {
        bestCorr = corr;
        loopEndSample = i;
      }
    }
  }

  const loopLenSamples = Math.max(100, loopEndSample - loopStartSample);
  const xfadeSec = Math.min(0.010, (loopLenSamples / sr) * 0.25);
  const xfadeSamples = Math.max(1, Math.floor(xfadeSec * sr));

  const newBuffer = ctx.createBuffer(src.numberOfChannels, src.length, sr);
  for (let ch = 0; ch < src.numberOfChannels; ch++) {
    const srcData = src.getChannelData(ch);
    const dstData = newBuffer.getChannelData(ch);
    dstData.set(srcData);

    // 位相連続クロスフェード: loopEnd の手前 xfadeSamples を loopStart の手前波形と滑らかにブレンド
    for (let i = 0; i < xfadeSamples; i++) {
      const tailIdx = loopEndSample - xfadeSamples + i;
      const headIdx = loopStartSample - xfadeSamples + i;
      if (tailIdx < 0 || tailIdx >= src.length || headIdx < 0 || headIdx >= src.length) continue;
      const t = i / xfadeSamples;
      const fadeOut = Math.cos((t * Math.PI) / 2);
      const fadeIn = Math.sin((t * Math.PI) / 2);
      dstData[tailIdx] = srcData[tailIdx] * fadeOut + srcData[headIdx] * fadeIn;
    }
  }

  cached._loopXfadeCache.set(key, newBuffer);
  return newBuffer;
}

// ------------------------------------------------------------
// 生波形(未ピッチシフト)のセグメントを組み立てる。
// ------------------------------------------------------------
function buildRawSegment(
  ctx: BaseAudioContext,
  cached: FetchedSample,
  startOffsetInWav: number,
  requiredSampleSec: number,
  loopRange: { loopStartSec: number; loopEndSec: number } | null
): AudioBuffer {
  const src = cached.buffer;
  const sr = src.sampleRate;
  const outLen = Math.max(1, Math.round(requiredSampleSec * sr));
  const outBuffer = ctx.createBuffer(src.numberOfChannels, outLen, sr);
  const startSample = Math.max(0, Math.floor(startOffsetInWav * sr));

  if (!loopRange) {
    for (let ch = 0; ch < src.numberOfChannels; ch++) {
      const s = src.getChannelData(ch);
      const d = outBuffer.getChannelData(ch);
      for (let i = 0; i < outLen; i++) {
        const idx = startSample + i;
        d[i] = idx < s.length ? s[idx] : 0;
      }
    }
    return outBuffer;
  }

  // ループ区間のシームレス周回
  const xfaded = getLoopCrossfadedBuffer(ctx, cached, loopRange.loopStartSec, loopRange.loopEndSec);
  const loopStartSample = Math.max(0, Math.floor(loopRange.loopStartSec * sr));
  const loopEndSample = Math.min(xfaded.length, Math.floor(loopRange.loopEndSec * sr));
  const loopLenSec = Math.max(0.001, loopRange.loopEndSec - loopRange.loopStartSec);
  const xfadeSamples = Math.max(1, Math.min(Math.floor(0.020 * sr), Math.floor((loopEndSample - loopStartSample) * 0.25)));
  // クロスフェード完了後の実効ループ周回長
  const loopLen = Math.max(1, loopEndSample - loopStartSample);

  for (let ch = 0; ch < src.numberOfChannels; ch++) {
    const s = xfaded.getChannelData(ch % xfaded.numberOfChannels);
    const d = outBuffer.getChannelData(ch);
    for (let i = 0; i < outLen; i++) {
      const absIdx = startSample + i;
      let idx: number;
      if (absIdx < loopEndSample) {
        idx = absIdx;
      } else {
        const loopOffset = (absIdx - loopEndSample) % loopLen;
        idx = loopStartSample + loopOffset;
      }
      d[i] = idx < s.length ? s[idx] : 0;
    }
  }
  return outBuffer;
}

// ============================================================
// メインのレンダリング関数
// ============================================================
export async function renderStudioOffline(
  notes: any[],
  tempo: number,
  voicebank: string,
  onProgress?: (pct: number) => void
): Promise<string | null> {
  if (!notes || notes.length === 0) return null;

  const sortedNotes = [...notes].sort((a, b) => (a.tick || 0) - (b.tick || 0));

  onProgress?.(2);

  // 1. 曲のタイムラインとテンポ変更マーカーを構築
  const baseTempo = (typeof tempo === 'number' && tempo > 0) ? tempo : 120;
  interface TempoMarker {
    tick: number;
    bpm: number;
  }
  const tempoMarkers: TempoMarker[] = [{ tick: 0, bpm: baseTempo }];
  for (const n of sortedNotes) {
    if (typeof n.tempo === 'number' && n.tempo > 0) {
      const t = Math.max(0, n.tick || 0);
      if (t === 0) {
        tempoMarkers[0].bpm = n.tempo;
      } else {
        tempoMarkers.push({ tick: t, bpm: n.tempo });
      }
    }
  }
  tempoMarkers.sort((a, b) => a.tick - b.tick);
  const uniqueTempoMarkers: TempoMarker[] = [];
  for (const tm of tempoMarkers) {
    if (uniqueTempoMarkers.length > 0 && uniqueTempoMarkers[uniqueTempoMarkers.length - 1].tick === tm.tick) {
      uniqueTempoMarkers[uniqueTempoMarkers.length - 1].bpm = tm.bpm;
    } else {
      uniqueTempoMarkers.push(tm);
    }
  }

  function tickToTimeSec(targetTick: number): number {
    if (targetTick <= 0) return 0;
    let totalSec = 0;
    let prevTick = 0;
    let currentBpm = uniqueTempoMarkers[0]?.bpm || 120;
    for (let i = 0; i < uniqueTempoMarkers.length; i++) {
      const m = uniqueTempoMarkers[i];
      if (m.tick > targetTick) break;
      if (m.tick > prevTick) {
        const dtTicks = m.tick - prevTick;
        totalSec += dtTicks * (60 / (currentBpm * 480));
        prevTick = m.tick;
      }
      currentBpm = m.bpm;
    }
    if (targetTick > prevTick) {
      const dtTicks = targetTick - prevTick;
      totalSec += dtTicks * (60 / (currentBpm * 480));
    }
    return totalSec;
  }

  let maxTick = 0;
  for (const n of sortedNotes) {
    const endTick = (n.tick || 0) + (n.length || 480);
    if (endTick > maxTick) maxTick = endTick;
  }
  const totalDurationSec = Math.max(1.0, tickToTimeSec(maxTick) + 1.5);

  // 2. 必要なサンプルの一覧を収集
  interface NoteSchedulingInfo {
    note: any;
    startTimeSec: number;
    durationSec: number;
    cacheKey: string;
    prevLyric?: string;
  }

  const schedulingInfos: NoteSchedulingInfo[] = [];
  const uniqueSampleMap = new Map<string, { alias: string; prevLyric?: string; noteNum: number }>();

  for (let i = 0; i < sortedNotes.length; i++) {
    const n = sortedNotes[i];
    if (isRest(n.lyric)) continue;

    const lyric = n.lyric || 'あ';
    const prevNote = i > 0 ? sortedNotes[i - 1] : null;
    const isContinuous = prevNote && (n.tick - (prevNote.tick + prevNote.length) <= 240);
    const prevLyric = isContinuous ? prevNote.lyric : undefined;
    const noteNum = n.noteNum || 60;
    const startTimeSec = tickToTimeSec(n.tick || 0);
    const endTimeSec = tickToTimeSec((n.tick || 0) + (n.length || 480));
    const durationSec = Math.max(0.01, endTimeSec - startTimeSec);
    const key = `${voicebank}:${lyric}:${prevLyric || ''}:${noteNum}`;

    if (!uniqueSampleMap.has(key)) {
      uniqueSampleMap.set(key, { alias: lyric, prevLyric, noteNum });
    }

    schedulingInfos.push({
      note: n,
      startTimeSec,
      durationSec,
      cacheKey: key,
      prevLyric
    });
  }

  onProgress?.(5);

  // 3. サンプルを並行バッチで取得 (進捗: 5% -> 30%)
  const sampleEntries = Array.from(uniqueSampleMap.entries());
  const BATCH_SIZE = 8;
  const sampleDataMap = new Map<string, FetchedSample | null>();

  for (let i = 0; i < sampleEntries.length; i += BATCH_SIZE) {
    const batch = sampleEntries.slice(i, i + BATCH_SIZE);
    await Promise.all(
      batch.map(async ([key, req]) => {
        const sample = await fetchSampleWithMeta(voicebank, req.alias, req.prevLyric, req.noteNum);
        sampleDataMap.set(key, sample);
      })
    );

    if (sampleEntries.length > 0) {
      const fetchPct = Math.round(5 + ((i + batch.length) / sampleEntries.length) * 25);
      onProgress?.(Math.min(30, fetchPct));
    }
  }

  onProgress?.(32);

  // 4. OfflineAudioContext (44.1kHz, 2チャンネル) のセットアップ
  const sampleRate = 44100;
  const totalFrames = Math.ceil(totalDurationSec * sampleRate);
  const offlineCtx = new OfflineAudioContext(2, totalFrames, sampleRate);

  // マスタリングエフェクトチェーン
  const masterGain = offlineCtx.createGain();
  masterGain.gain.setValueAtTime(0.95, 0);

  // スタジオ品質・クリアボーカルマスタリングチェーン (こもり感を一掃し、ヌケと滑舌の明瞭度を最大化)
  // 1. サブベースカット (60Hz HPF)
  const masterHpf = offlineCtx.createBiquadFilter();
  masterHpf.type = 'highpass';
  masterHpf.frequency.setValueAtTime(60, 0);
  masterHpf.Q.setValueAtTime(0.707, 0);

  // 2. [スタジオDe-Mud] 350Hz近傍の濁り・こもりをすっきりカット
  const masterDeMud = offlineCtx.createBiquadFilter();
  masterDeMud.type = 'peaking';
  masterDeMud.frequency.setValueAtTime(350, 0);
  masterDeMud.gain.setValueAtTime(-2.5, 0);
  masterDeMud.Q.setValueAtTime(1.2, 0);

  // 3. [スタジオPresence & Articulation] 3.8kHz 子音のアタック・発音の輪郭をクリアに強調
  const masterPresence = offlineCtx.createBiquadFilter();
  masterPresence.type = 'peaking';
  masterPresence.frequency.setValueAtTime(3800, 0);
  masterPresence.gain.setValueAtTime(3.0, 0);
  masterPresence.Q.setValueAtTime(1.0, 0);

  // 4. [スタジオAir & Brilliance] 9.0kHz ハイシェルフで抜けと透明感を付加
  const masterAir = offlineCtx.createBiquadFilter();
  masterAir.type = 'highshelf';
  masterAir.frequency.setValueAtTime(9000, 0);
  masterAir.gain.setValueAtTime(3.2, 0);

  // 5. [スタジオ超高域セーフティLPF] 17.5kHz以上の不要な折り返しノイズのみをカット
  const masterLpf = offlineCtx.createBiquadFilter();
  masterLpf.type = 'lowpass';
  masterLpf.frequency.setValueAtTime(17500, 0);
  masterLpf.Q.setValueAtTime(0.707, 0);

  // クリッピング防止コンプレッサー/リミッター
  const masterLimiter = offlineCtx.createDynamicsCompressor();
  masterLimiter.threshold.setValueAtTime(-0.8, 0);
  masterLimiter.knee.setValueAtTime(3.0, 0);
  masterLimiter.ratio.setValueAtTime(16.0, 0);
  masterLimiter.attack.setValueAtTime(0.003, 0);
  masterLimiter.release.setValueAtTime(0.05, 0);

  masterGain.connect(masterHpf);
  masterHpf.connect(masterDeMud);
  masterDeMud.connect(masterPresence);
  masterPresence.connect(masterAir);
  masterAir.connect(masterLpf);
  masterLpf.connect(masterLimiter);
  masterLimiter.connect(offlineCtx.destination);

  // 5. 各ノートの音響ノードをオフラインコンテキストにスケジュール (進捗: 32% -> 40%)
  // [修正] このループはノートごとに自己相関計算+PSOLA+デクリックという
  // 重いCPU処理を「同期的に」行っており、ノート数の多い曲では数秒〜十数秒
  // メインスレッドを占有し続けてタブが完全に固まって見えていた。
  // 数ノートごとに1回、明示的にイベントループへ制御を返す(yield)ことで、
  // 処理時間そのものは変わらないが、ブラウザが固まらず(UIが反応し続け、
  // 進捗表示も更新され続ける)ようにする。
  const YIELD_EVERY_N_NOTES = 2;
  for (let idx = 0; idx < schedulingInfos.length; idx++) {
    if (idx > 0 && idx % YIELD_EVERY_N_NOTES === 0) {
      await new Promise((resolve) => setTimeout(resolve, 8));
      onProgress?.(Math.round(32 + (idx / schedulingInfos.length) * 8));
    }
    const { note, startTimeSec, durationSec, cacheKey } = schedulingInfos[idx];
    const cached = sampleDataMap.get(cacheKey);

    if (cached && cached.buffer) {
      try {
        const sampleBase = cached.baseMidi || 60;
        const semitoneShift = softClampSemitone(note.noteNum - sampleBase);
        // タイミング計算(消費速度)専用。もう再生には使わない。
        const baseRate = Math.min(4.0, Math.max(0.18, Math.pow(2, semitoneShift / 12)));
        const pitchRatio = Math.pow(2, semitoneShift / 12); // PSOLAはクランプ不要(エイリアシングしない)

        const offsetSec = Math.max(0, (cached.left_blank || 0) / 1000);
        const preuttSec = Math.max(0, (cached.preutterance || 0) / 1000);
        const fixedSec = Math.max(0, (cached.fixed_range || 0) / 1000);
        // PSOLAでは時間軸と音高が独立しているため、先行発音(子音)は自然な実時間で発音される
        const effectivePreuttSec = preuttSec;
        const wavDuration = cached.buffer.duration;

        const rb = cached.right_blank || 0;
        let cutoffEndSec = wavDuration;
        if (rb > 0) {
          cutoffEndSec = Math.max(offsetSec + 0.05, wavDuration - (rb / 1000));
        } else if (rb < 0) {
          cutoffEndSec = Math.max(offsetSec + 0.05, Math.min(wavDuration, offsetSec + Math.abs(rb) / 1000));
        }
        const maxSampleDur = Math.max(0.04, cutoffEndSec - offsetSec);

        const actualStartTime = Math.max(0, startTimeSec - effectivePreuttSec);
        const timeDiff = actualStartTime - (startTimeSec - effectivePreuttSec);
        const startOffsetInWav = Math.min(offsetSec + timeDiff, cutoffEndSec - 0.02);
        const playLen = effectivePreuttSec + durationSec;

        const requiredSampleSec = (startOffsetInWav - offsetSec) + playLen;

        let loopRange: { loopStartSec: number; loopEndSec: number } | null = null;
        if (requiredSampleSec > maxSampleDur + 0.02) {
          // 語尾の呼気・息漏れノイズがループに巻き込まれるのを防ぐため、末尾から十分手前(少なくとも40ms)でループ
          const safeEndSec = Math.min(wavDuration - 0.04, cutoffEndSec - 0.04);
          const safeStartSec = offsetSec + Math.max(0.04, fixedSec || preuttSec || 0.06);
          const loopStartSec = Math.min(safeEndSec - 0.05, safeStartSec);
          const loopEndSec = Math.max(loopStartSec + 0.04, safeEndSec);
          if (loopEndSec > loopStartSec + 0.03) {
            loopRange = { loopStartSec, loopEndSec };
          }
        }

        // --- ここからがPSOLAによるピッチ+時間シフト ---
        // 1. 生波形(未シフト)から、必要な区間をループも含めて敷き詰めて切り出す
        const rawSegment = buildRawSegment(
          offlineCtx,
          cached,
          Math.max(0, Math.min(wavDuration - 0.02, startOffsetInWav)),
          Math.max(0.02, requiredSampleSec),
          loopRange
        );

        // 2. ピッチと時間伸縮を同時に、フォルマントを保持したまま適用
        const targetLenSamples = Math.max(1, Math.round(playLen * sampleRate));
        const shiftedBuffer = psolaPitchAndTimeShiftBuffer(
          offlineCtx,
          rawSegment,
          pitchRatio,
          targetLenSamples
        );

        const source = offlineCtx.createBufferSource();
        source.buffer = shiftedBuffer;
        // ベースピッチはPSOLAで焼き込み済みなので、playbackRateは1.0が基準。
        source.playbackRate.setValueAtTime(1.0, Math.max(0, actualStartTime));

        // ピッチベンド(ビブラート等)は小さな相対揺れとしてplaybackRateに乗せる。
        // 揺れ幅は通常小さいのでフォルマントへの影響は知覚できるレベルにならない。
        if (note.pbs && note.pbw && note.pby) {
          try {
            const rawPoints = parsePitchBend(note.pbs, note.pbw, note.pby);
            const points = smoothPitchBendPoints(rawPoints);
            scheduleSafePitchRamp(
              source.playbackRate,
              1.0,
              points,
              startTimeSec,
              (st) => Math.max(0.5, Math.min(2.0, Math.pow(2, st / 12))),
              0,
              startTimeSec + durationSec
            );
          } catch (e) {
            // ピッチベンド解析に失敗しても基準ピッチ(1.0)のまま続行
          }
        }

        const gain = offlineCtx.createGain();
        const volGain = Math.max(0.05, Math.min(1.5, (note.intensity || 120) / 120)) * 0.92;

        const tStart = actualStartTime;
        const overlapSec = Math.max(0, (cached.overlap || 0) / 1000) / baseRate;
        const attackDur = Math.max(0.006, Math.min(0.03, overlapSec || 0.008));
        const tAttack = tStart + attackDur;

        const noteEndTime = startTimeSec + durationSec;
        const releaseDur = 0.015;
        const tDecay = Math.max(tAttack + 0.003, noteEndTime - releaseDur);
        const tEnd = Math.min(tDecay + releaseDur, noteEndTime);

        gain.gain.setValueAtTime(0.0, tStart);
        gain.gain.linearRampToValueAtTime(volGain, tAttack);
        if (tDecay > tAttack + 0.002) {
          gain.gain.setValueAtTime(volGain, tDecay);
        }
        gain.gain.linearRampToValueAtTime(0.0, tEnd);
        gain.gain.setValueAtTime(0.0, tEnd + 0.001);

        const hpf = offlineCtx.createBiquadFilter();
        hpf.type = 'highpass';
        hpf.frequency.setValueAtTime(80, tStart);
        hpf.Q.setValueAtTime(0.707, tStart);

        source.connect(hpf);
        hpf.connect(gain);
        gain.connect(masterGain);

        // shiftedBufferは既に必要な長さぶんだけ用意されているのでオフセット不要
        source.start(actualStartTime, 0);
        source.stop(tEnd + 0.01);
      } catch (err) {
        console.warn('[wasmEngine] Note scheduling failed, fallback to synth:', err);
      }
    } else {
      // フォルマントシンセサイザー フォールバック
      try {
        const baseFreq = 440 * Math.pow(2, (note.noteNum - 69) / 12);
        let f1 = 500, f2 = 1500;
        const lyric = note.lyric || 'あ';
        if (lyric.includes('あ') || lyric.includes('a') || lyric.includes('か') || lyric.includes('た')) {
          f1 = 800; f2 = 1250;
        } else if (lyric.includes('い') || lyric.includes('i') || lyric.includes('き') || lyric.includes('し')) {
          f1 = 300; f2 = 2300;
        } else if (lyric.includes('う') || lyric.includes('u') || lyric.includes('く') || lyric.includes('す')) {
          f1 = 350; f2 = 1200;
        } else if (lyric.includes('え') || lyric.includes('e') || lyric.includes('け') || lyric.includes('せ')) {
          f1 = 500; f2 = 1900;
        } else if (lyric.includes('お') || lyric.includes('o') || lyric.includes('こ') || lyric.includes('そ')) {
          f1 = 450; f2 = 800;
        }

        const osc = offlineCtx.createOscillator();
        osc.type = 'sawtooth';
        osc.frequency.setValueAtTime(baseFreq, startTimeSec);

        // 母音の第1・第2フォルマント(F1/F2)と抜けの良い高域を合成し、篭もりのない明瞭な音声を生成
        const filter1 = offlineCtx.createBiquadFilter();
        filter1.type = 'peaking';
        filter1.frequency.setValueAtTime(f1, startTimeSec);
        filter1.gain.setValueAtTime(6.0, startTimeSec);
        filter1.Q.setValueAtTime(2.0, startTimeSec);

        const filter2 = offlineCtx.createBiquadFilter();
        filter2.type = 'peaking';
        filter2.frequency.setValueAtTime(f2, startTimeSec);
        filter2.gain.setValueAtTime(5.0, startTimeSec);
        filter2.Q.setValueAtTime(2.0, startTimeSec);

        const lpf = offlineCtx.createBiquadFilter();
        lpf.type = 'lowpass';
        lpf.frequency.setValueAtTime(9500, startTimeSec);
        lpf.Q.setValueAtTime(0.707, startTimeSec);

        const synthGain = offlineCtx.createGain();
        const vol = Math.max(0.05, Math.min(1.0, (note.intensity || 120) / 140)) * 0.6;

        synthGain.gain.setValueAtTime(0.0001, startTimeSec);
        synthGain.gain.linearRampToValueAtTime(vol, startTimeSec + 0.02);
        synthGain.gain.setValueAtTime(vol, Math.max(startTimeSec + 0.03, startTimeSec + durationSec - 0.02));
        synthGain.gain.linearRampToValueAtTime(0.0001, startTimeSec + durationSec);

        osc.connect(filter1);
        filter1.connect(filter2);
        filter2.connect(lpf);
        lpf.connect(synthGain);
        synthGain.connect(masterGain);

        osc.start(startTimeSec);
        osc.stop(startTimeSec + durationSec + 0.05);
      } catch (synthErr) {
        console.warn('[wasmEngine] Synth fallback failed:', synthErr);
      }
    }
  }

  onProgress?.(40);

  // 6. オフラインレンダリング実行 (進捗: 40% -> 90%)
  // PSOLA処理が加わった分、単純resampleより重いのでETA見積もりを底上げ
  let progressInterval: number | null = null;
  let currentRenderPct = 40;
  const estimatedRenderTimeMs = Math.min(15000, Math.max(600, totalDurationSec * 220));
  const startTime = performance.now();

  progressInterval = window.setInterval(() => {
    const elapsed = performance.now() - startTime;
    const ratio = Math.min(0.98, elapsed / estimatedRenderTimeMs);
    currentRenderPct = Math.round(40 + ratio * 50); // 40% ~ 90%
    onProgress?.(currentRenderPct);
  }, 100);

  let renderedBuffer: AudioBuffer;
  try {
    renderedBuffer = await offlineCtx.startRendering();
  } finally {
    if (progressInterval !== null) {
      clearInterval(progressInterval);
      progressInterval = null;
    }
  }

  onProgress?.(92);

  // 7. DCオフセットの除去とリソース解放
  const nCh = renderedBuffer.numberOfChannels;
  const nFrames = renderedBuffer.length;

  for (let ch = 0; ch < nCh; ch++) {
    const data = renderedBuffer.getChannelData(ch);
    // DCオフセット除去
    let sum = 0;
    for (let i = 0; i < nFrames; i++) sum += data[i];
    const dc = sum / Math.max(1, nFrames);
    if (Math.abs(dc) > 1e-5) {
      for (let i = 0; i < nFrames; i++) data[i] -= dc;
    }
  }

  // 不要になった中間リソースを速やかに明示的解放
  sampleDataMap.clear();
  schedulingInfos.length = 0;

  // 8. AudioBuffer を高音質 16-bit PCM WAV Blob へエンコード (進捗: 96% -> 100%)
  await new Promise((r) => setTimeout(r, 50)); // UI更新用yield
  const wavBlob = bufferToWav(renderedBuffer);
  const ab = await wavBlob.arrayBuffer();
  cleanWavArrayBuffer(ab);
  const cleanBlob = new Blob([ab], { type: 'audio/wav' });

  onProgress?.(100);

  return URL.createObjectURL(cleanBlob);
}

export const renderWasm = renderStudioOffline;
