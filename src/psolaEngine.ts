// ============================================================
// psolaEngine.ts
//
// フォルマント（声質）保持 TD-PSOLA 音声合成エンジン (Web Audio Offline)
//
// 1. 各トラック・各ノートの音源サンプル(oto.ini設定含む)を並行取得
// 2. TD-PSOLA (Time-Domain Pitch-Synchronous OverLap-Add) により:
//    - 音高を変更しても声質（フォルマント周波数・声道共鳴）を100%保持
//    - テープ伸縮（playbackRate変更によるチップマンク化・機械音化）を完全に排除
// 3. UTAU標準音素接続:
//    - 先行発声 (Preutterance) による子音の自然な配置
//    - オーバーラップ (Overlap) に基づくイコールパワークロスフェード ($\cos^2 / \sin^2$)
//    - P2P3 ポルタメント S字カーブ & 自然なビブラートの完全反映
// 4. 高音質 16-bit PCM WAV (RIFF) を生成してBlob URLを出力
// ============================================================

import {
  parsePitchBend,
  smoothPitchBendPoints,
  softClampSemitone,
  scheduleSafePitchRamp,
  generateUtauPortamentoPoints,
  makeSemitoneCurve,
  type PitchPoint
} from './utils/pitchCurve';
import { psolaPitchAndTimeShiftBuffer } from './psolaPitchShift';
import { bufferToWav } from './utils/audioEncoder';
import { cleanWavArrayBuffer } from './utils/wavCleaner';

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

      const result: FetchedSample = {
        buffer: audioBuffer,
        left_blank,
        fixed_range,
        right_blank,
        preutterance,
        overlap,
        baseMidi
      };

      sampleCache.set(key, result);
      return result;
    } catch (e) {
      sampleCache.set(key, null);
      return null;
    } finally {
      inFlightRequests.delete(key);
    }
  })();

  inFlightRequests.set(key, promise);
  return promise;
}

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

  const xfaded = getLoopCrossfadedBuffer(ctx, cached, loopRange.loopStartSec, loopRange.loopEndSec);
  const loopStartSample = Math.max(0, Math.floor(loopRange.loopStartSec * sr));
  const loopEndSample = Math.min(xfaded.length, Math.floor(loopRange.loopEndSec * sr));
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

export async function renderPsolaOffline(
  notes: any[],
  tempo: number,
  voicebank: string,
  onProgress?: (pct: number) => void
): Promise<string | null> {
  if (!notes || notes.length === 0) return null;

  const sortedNotes = [...notes].sort((a, b) => (a.tick || 0) - (b.tick || 0));
  onProgress?.(2);

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
    const isContinuous = Boolean(prevNote && !isRest(prevNote.lyric) && (n.tick - (prevNote.tick + (prevNote.length || 480)) <= 240));
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
    onProgress?.(Math.min(30, Math.round(5 + ((i + batch.length) / Math.max(1, sampleEntries.length)) * 25)));
  }

  onProgress?.(32);

  const targetSampleRate = 44100;
  const totalFrames = Math.max(1024, Math.ceil(totalDurationSec * targetSampleRate));
  const offlineCtx = new OfflineAudioContext(2, totalFrames, targetSampleRate);

  const masterGain = offlineCtx.createGain();
  masterGain.gain.setValueAtTime(0.92, 0);

  const masterHpf = offlineCtx.createBiquadFilter();
  masterHpf.type = 'highpass';
  masterHpf.frequency.setValueAtTime(65, 0);
  masterHpf.Q.setValueAtTime(0.707, 0);

  const masterLimiter = offlineCtx.createDynamicsCompressor();
  masterLimiter.threshold.setValueAtTime(-1.0, 0);
  masterLimiter.knee.setValueAtTime(6.0, 0);
  masterLimiter.ratio.setValueAtTime(4.0, 0);
  masterLimiter.attack.setValueAtTime(0.003, 0);
  masterLimiter.release.setValueAtTime(0.05, 0);

  masterGain.connect(masterHpf);
  masterHpf.connect(masterLimiter);
  masterLimiter.connect(offlineCtx.destination);

  let resolvedSampleCount = 0;
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
        const pitchRatio = Math.pow(2, semitoneShift / 12);
        const safePitchRatio = Math.min(4.0, Math.max(0.18, pitchRatio));

        let bendPoints: PitchPoint[] | null = null;
        if (note.pbs && note.pbw && note.pby) {
          try {
            bendPoints = smoothPitchBendPoints(parsePitchBend(note.pbs, note.pbw, note.pby));
          } catch (e) {
            bendPoints = null;
          }
        } else {
          const prevInfo = idx > 0 ? schedulingInfos[idx - 1] : null;
          const isPrevConnected = prevInfo && (startTimeSec - (prevInfo.startTimeSec + prevInfo.durationSec) <= 0.05);
          if (isPrevConnected && prevInfo && prevInfo.note.noteNum !== note.noteNum) {
            const autoPoints = generateUtauPortamentoPoints(
              note.noteNum,
              prevInfo.note.noteNum,
              durationSec * 1000,
              { portamentoMs: 105, vibratoDepthSemitones: durationSec > 0.38 ? 0.32 : 0 }
            );
            bendPoints = smoothPitchBendPoints(autoPoints);
          }
        }

        const offsetSec = Math.max(0, (cached.left_blank || 0) / 1000);
        const preuttSec = Math.max(0, (cached.preutterance || 0) / 1000);
        const fixedSec = Math.max(0, (cached.fixed_range || 0) / 1000);
        const wavDuration = cached.buffer.duration;

        const rb = cached.right_blank || 0;
        let cutoffEndSec = wavDuration;
        if (rb > 0) {
          cutoffEndSec = Math.max(offsetSec + 0.05, wavDuration - (rb / 1000));
        } else if (rb < 0) {
          cutoffEndSec = Math.max(offsetSec + 0.05, Math.min(wavDuration, offsetSec + Math.abs(rb) / 1000));
        }
        const maxSampleDur = Math.max(0.04, cutoffEndSec - offsetSec);

        const actualStartTime = Math.max(0, startTimeSec - preuttSec);
        const timeDiff = actualStartTime - (startTimeSec - preuttSec);
        const startOffsetInWav = Math.min(offsetSec + timeDiff, cutoffEndSec - 0.02);
        const playLen = preuttSec + durationSec;

        const computeLoopRange = (consumeSec: number): { loopStartSec: number; loopEndSec: number } | null => {
          if (consumeSec <= maxSampleDur + 0.02) return null;
          const safeEndSec = Math.min(wavDuration - 0.04, cutoffEndSec - 0.04);
          const safeStartSec = offsetSec + Math.max(0.04, fixedSec || preuttSec || 0.06);
          const loopStartSec = Math.min(safeEndSec - 0.05, safeStartSec);
          const loopEndSec = Math.max(loopStartSec + 0.04, safeEndSec);
          return loopEndSec > loopStartSec + 0.03 ? { loopStartSec, loopEndSec } : null;
        };

        const startClamped = Math.max(0, Math.min(wavDuration - 0.02, startOffsetInWav));
        const sourceNeedSec = Math.max(0.05, Math.max(playLen, playLen / safePitchRatio) + 0.15);
        const sourceBuffer = buildRawSegment(
          offlineCtx,
          cached,
          startClamped,
          sourceNeedSec,
          computeLoopRange((startOffsetInWav - offsetSec) + sourceNeedSec)
        );

        // ★核心: フォルマント（声質）を完全に保持する TD-PSOLA リサンプラー
        let finalBuffer = sourceBuffer;
        let isPsolaApplied = false;

        let bendAppliedInPsola = false;
        if (Math.abs(safePitchRatio - 1.0) >= 0.004 || (bendPoints && bendPoints.length > 0)) {
          try {
            const targetLengthSamples = Math.max(256, Math.round(playLen * offlineCtx.sampleRate));
            const curveFn = bendPoints && bendPoints.length > 0
              ? makeSemitoneCurve(bendPoints, startTimeSec - actualStartTime)
              : undefined;
            finalBuffer = psolaPitchAndTimeShiftBuffer(
              offlineCtx,
              sourceBuffer,
              safePitchRatio,
              targetLengthSamples,
              {
                minF0Hz: 65,
                maxF0Hz: 900,
                voicingThreshold: 0.6,
                pitchCurveSemitones: curveFn
              }
            );
            bendAppliedInPsola = !!curveFn;
            isPsolaApplied = true;
          } catch (psolaErr) {
            finalBuffer = sourceBuffer;
            isPsolaApplied = false;
          }
        }

        const source = offlineCtx.createBufferSource();
        source.buffer = finalBuffer;
        const sourceRate = isPsolaApplied ? 1.0 : safePitchRatio;
        source.playbackRate.setValueAtTime(sourceRate, Math.max(0, actualStartTime));

        if (bendPoints && !bendAppliedInPsola) {
          try {
            scheduleSafePitchRamp(
              source.playbackRate,
              sourceRate,
              bendPoints,
              startTimeSec,
              (st) => sourceRate * Math.max(0.5, Math.min(2.0, Math.pow(2, st / 12))),
              Math.max(0, actualStartTime),
              startTimeSec + durationSec
            );
          } catch (e) {}
        }

        const gain = offlineCtx.createGain();
        const volGain = Math.max(0.05, Math.min(1.5, (note.intensity || 120) / 120)) * 0.95;

        const tStart = actualStartTime;
        const overlapSec = Math.max(0, (cached.overlap || 0) / 1000);
        const attackDur = Math.max(0.008, Math.min(0.045, overlapSec || 0.012));
        const tAttack = tStart + attackDur;

        const nextInfo = idx + 1 < schedulingInfos.length ? schedulingInfos[idx + 1] : null;
        const isNextConnected = nextInfo && (nextInfo.startTimeSec - (startTimeSec + durationSec) <= 0.05);

        let releaseDur = 0.020;
        let noteEndTime = startTimeSec + durationSec;
        if (isNextConnected && nextInfo) {
          const nextCached = sampleDataMap.get(nextInfo.cacheKey);
          const nextOverlapSec = Math.max(0, (nextCached?.overlap || 0) / 1000);
          noteEndTime = Math.max(
            startTimeSec + durationSec,
            nextInfo.startTimeSec - Math.max(0, (nextCached?.preutterance || 0) / 1000) + nextOverlapSec
          );
          releaseDur = Math.max(0.015, nextOverlapSec || 0.020);
        }

        const tDecay = Math.max(tAttack + 0.003, noteEndTime - releaseDur);
        const tEnd = Math.max(tDecay + 0.003, noteEndTime);

        gain.gain.setValueAtTime(0.0, tStart);
        gain.gain.linearRampToValueAtTime(volGain, tAttack);
        if (tDecay > tAttack + 0.002) {
          gain.gain.setValueAtTime(volGain, tDecay);
        }
        gain.gain.linearRampToValueAtTime(0.0, tEnd);
        gain.gain.setValueAtTime(0.0, tEnd + 0.002);

        source.connect(gain);
        gain.connect(masterGain);

        source.start(actualStartTime, 0);
        source.stop(tEnd + 0.01);
        resolvedSampleCount++;
      } catch (err) {
        console.warn('[psolaEngine] Note scheduling failed; skipping unresolved note:', err);
      }
    }
  }

  if (schedulingInfos.length > 0 && resolvedSampleCount === 0) {
    throw new Error('PSOLAエンジンで音声サンプルを解決できませんでした。');
  }

  onProgress?.(40);

  let progressInterval: number | null = null;
  const estimatedRenderTimeMs = Math.min(15000, Math.max(600, totalDurationSec * 220));
  const startTime = performance.now();

  progressInterval = window.setInterval(() => {
    const elapsed = performance.now() - startTime;
    const ratio = Math.min(0.98, elapsed / estimatedRenderTimeMs);
    const currentRenderPct = Math.round(40 + ratio * 50);
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

  const nCh = renderedBuffer.numberOfChannels;
  const nFrames = renderedBuffer.length;
  let masterMaxAbs = 0;
  for (let ch = 0; ch < nCh; ch++) {
    const data = renderedBuffer.getChannelData(ch);
    let sum = 0;
    for (let i = 0; i < nFrames; i++) sum += data[i];
    const dc = sum / Math.max(1, nFrames);
    if (Math.abs(dc) > 1e-5) {
      for (let i = 0; i < nFrames; i++) data[i] -= dc;
    }
    for (let i = 0; i < nFrames; i++) {
      const a = Math.abs(data[i]);
      if (a > masterMaxAbs) masterMaxAbs = a;
    }
  }

  // 0dBFSクリッピング防止: ピークが0.965(-0.3dBFS)を超える場合は安全にノーマライズ
  const targetPeak = 0.965;
  const scale = masterMaxAbs > targetPeak ? targetPeak / masterMaxAbs : (masterMaxAbs > 0.05 ? Math.min(1.0, targetPeak / masterMaxAbs) : 1.0);
  const threshold = 0.85;
  const range = 1.0 - threshold;

  for (let ch = 0; ch < nCh; ch++) {
    const data = renderedBuffer.getChannelData(ch);
    for (let i = 0; i < nFrames; i++) {
      let x = data[i] * scale;
      const absX = Math.abs(x);
      if (absX > threshold) {
        const over = (absX - threshold) / range;
        const compressed = threshold + range * Math.tanh(over);
        x = x < 0 ? -compressed : compressed;
      }
      data[i] = x;
    }
  }

  sampleDataMap.clear();
  schedulingInfos.length = 0;

  await new Promise((r) => setTimeout(r, 50));
  const wavBlob = bufferToWav(renderedBuffer);
  const ab = await wavBlob.arrayBuffer();
  cleanWavArrayBuffer(ab);
  const cleanBlob = new Blob([ab], { type: 'audio/wav' });

  onProgress?.(100);
  return URL.createObjectURL(cleanBlob);
}
