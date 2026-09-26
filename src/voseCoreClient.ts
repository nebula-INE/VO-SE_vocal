// ============================================================
// voseCoreClient.ts
//
// メインスレッド側から voseCoreWorker.ts (本物のvose_core WASM/BigVGAN
// パイプラインを叩くWeb Worker) を起動・利用するためのブリッジ。
//
// renderWasm() (= wasmEngine.ts の renderStudioOffline、JSのみで完結する
// 簡易実装)と全く同じシグネチャ ((notes, tempo, voicebank, onProgress) =>
// Promise<string|null>) を持つ renderStudioCore() をエクスポートする。
//
// [vose_core.cpp / vose_core.h 確認済み事項]
//   - NoteEventに絶対時刻フィールドは無い。ノートは pitch_length
//     (5msフレーム数)ぶんの長さで単純に連結される。よって休符や
//     ノート間のギャップは「wav_path=null の無声NoteEvent」として
//     明示的に埋めないと、曲全体のタイミングがズレる。
//   - g_vocal_timeline(set_vocal_timelineが書き込む方)はレンダリング
//     コードから一切読まれていない(書き込み専用/未使用)。呼ばなくてよい。
//   - oto.ini相当のタイミング(offset/consonant/cutoff/preutterance/
//     overlap)は NoteEvent ではなく set_oto_data() で別途登録する。
//     alias文字列(=wav_path/load_embedded_resourceのkeyと同一)がキー。
//   - kFramePeriod = 5.0ms (vose_core.cpp内で固定)。
// ============================================================

import { renderStudioOffline } from './wasmEngine';
import {
  parsePitchBend,
  smoothPitchBendPoints,
  type PitchPoint
} from './utils/pitchCurve';
import { bufferToWav } from './utils/audioEncoder';
import { cleanWavArrayBuffer } from './utils/wavCleaner';
import type {
  RenderRequestMsg,
  RenderResponseMsg,
  WorkerSampleEntry,
  WorkerNoteEntry,
  OtoData
} from './voseCoreWorker';

// vose_core.wasm 側が前提とするサンプルレート(kFs)。
// vose_core.cpp をアップロードしてもらった際に kFs の実際値が見えなかった
// ため、wasmEngine.ts / OfflineAudioContext と合わせて44.1kHzと仮定している。
// 違う場合はここを実際の kFs に合わせること。
const CORE_SAMPLE_RATE = 44100;

// vose_core.cpp: static constexpr double kFramePeriod = 5.0; (ms)
const PITCH_FRAME_PERIOD_MS = 5;

const REST_LYRICS_SET = new Set([
  'r', 'r_', 'r_0', '[r]', '息', 'br', 'pau', 'sil', '吸', '吸気', '息吸い', '', ' ', '　', '休', '休符', '・', '-', 'ー', '~', 'null'
]);

function isRest(lyric?: string): boolean {
  if (!lyric) return true;
  const l = lyric.trim().toLowerCase();
  if (REST_LYRICS_SET.has(l)) return true;
  return /^br[0-9]*$/i.test(l) || /^息[0-9]*$/i.test(l) || /^吸[0-9]*$/i.test(l) || /^_?(br|息|吸)[0-9]*$/i.test(l);
}

interface FetchedRawSample {
  pcmF32: Float32Array;
  baseMidi: number;
  oto: OtoData;
}

let sharedDecodeCtx: AudioContext | null = null;

async function decodeToPcmF32(arrayBuf: ArrayBuffer): Promise<Float32Array> {
  const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
  if (!sharedDecodeCtx || sharedDecodeCtx.state === 'closed') {
    sharedDecodeCtx = new AudioCtx();
  }
  const audioBuffer = await sharedDecodeCtx.decodeAudioData(arrayBuf.slice(0));

  let float32: Float32Array;
  if (audioBuffer.sampleRate !== CORE_SAMPLE_RATE) {
    // 44100Hz に正確にリサンプリングして WORLD ボコーダーの前提 (kFs=44100) に合致させる
    const targetLength = Math.max(1, Math.round(audioBuffer.duration * CORE_SAMPLE_RATE));
    const offlineCtx = new OfflineAudioContext(1, targetLength, CORE_SAMPLE_RATE);
    const source = offlineCtx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(offlineCtx.destination);
    source.start(0);
    const resampledBuffer = await offlineCtx.startRendering();
    float32 = resampledBuffer.getChannelData(0);
  } else {
    float32 = audioBuffer.getChannelData(0);
  }

// getChannelData()のビューはAudioBufferに紐付いている。Workerへ安全に
  // Transferするためコピーし、Float32の精度を保ったままWORLDへ渡す。
  return new Float32Array(float32);
}

async function fetchRawSample(
  voicebank: string,
  alias: string,
  prevLyric?: string,
  noteNum?: number
): Promise<FetchedRawSample | null> {
  if (isRest(alias)) return null;
  try {
    let url = `/api/py/voicebank-sample?name=${encodeURIComponent(voicebank)}&alias=${encodeURIComponent(alias)}`;
    if (prevLyric) url += `&prevLyric=${encodeURIComponent(prevLyric)}`;
    if (noteNum !== undefined) url += `&noteNum=${encodeURIComponent(String(noteNum))}`;

    const res = await fetch(url);
    if (!res.ok) return null;

    const baseMidi = parseFloat(res.headers.get('X-Sample-Base-Midi') || '60');
    // oto.iniの値は生のまま(符号・単位ms)渡す。map_time()側で
    // cutoff<0 = 末尾からの距離、という変換を既にやってくれるため、
    // ここでJS側で事前計算・加工する必要はない。
    const oto: OtoData = {
      offsetMs: parseFloat(res.headers.get('X-Oto-Left-Blank') || '0'),
      consonantMs: parseFloat(res.headers.get('X-Oto-Fixed-Range') || '0'),
      cutoffMs: parseFloat(res.headers.get('X-Oto-Right-Blank') || '0'),
      preutteranceMs: parseFloat(res.headers.get('X-Oto-Preutterance') || '0'),
      overlapMs: parseFloat(res.headers.get('X-Oto-Overlap') || '0')
    };

    const arrayBuf = await res.arrayBuffer();
    const pcmF32 = await decodeToPcmF32(arrayBuf);
    return { pcmF32, baseMidi, oto };
  } catch (err) {
    console.warn(`[voseCoreClient] サンプル取得/デコード失敗 alias='${alias}':`, err);
    return null;
  }
}

// ノートのピッチベンド(PBS/PBW/PBY)を、5msフレーム周期の絶対Hzカーブへ変換する。
function buildPitchCurveHz(note: any, frameCount: number, durationMs: number): number[] {
  const baseHz = 440 * Math.pow(2, (note.noteNum - 69) / 12);

  let bendSemitoneAt: (tMs: number) => number = () => 0;
  if (note.pbs && note.pbw && note.pby) {
    try {
      const rawPoints = parsePitchBend(note.pbs, note.pbw, note.pby);
      const points: PitchPoint[] = smoothPitchBendPoints(rawPoints);
      bendSemitoneAt = (tMs: number) => {
        if (points.length === 0) return 0;
        if (tMs <= points[0].timeMs) return points[0].semitone;
        for (let i = 0; i < points.length - 1; i++) {
          const a = points[i];
          const b = points[i + 1];
          if (tMs >= a.timeMs && tMs <= b.timeMs) {
            const ratio = b.timeMs > a.timeMs ? (tMs - a.timeMs) / (b.timeMs - a.timeMs) : 0;
            return a.semitone + (b.semitone - a.semitone) * ratio;
          }
        }
        return points[points.length - 1].semitone;
      };
    } catch (e) {
      // ピッチベンド解析失敗時はベースピッチのみで続行
    }
  }

  const curve: number[] = new Array(frameCount);
  for (let i = 0; i < frameCount; i++) {
    const tMs = (i / Math.max(1, frameCount - 1)) * durationMs;
    curve[i] = baseHz * Math.pow(2, bendSemitoneAt(tMs) / 12);
  }
  return curve;
}

function silentFrames(frameCount: number): number[] {
  return new Array(frameCount).fill(0);
}

interface PendingRender {
  resolve: (url: string | null) => void;
  reject: (err: any) => void;
  onProgress?: (pct: number) => void;
}

let worker: Worker | null = null;
let nextRequestId = 1;
const pending = new Map<number, PendingRender>();

/**
 * WORLDが出力したWAVを変更せずにBlob URLへ変換する。
 * 後段のデクリックやフィルタは声帯パルス・無声子音を誤って修正し得るため、
 * レンダリング経路では適用しない。必要なマスタリングは明示的なexport処理で行う
 */
function createWavBlobUrl(wavBuffer: ArrayBuffer): string {
  const blob = new Blob([wavBuffer], { type: 'audio/wav' });
  return URL.createObjectURL(blob);
}

function getWorker(): Worker {
  if (worker) return worker;
  worker = new Worker(new URL('./voseCoreWorker.ts', import.meta.url), { type: 'module' });
  worker.onmessage = (ev: MessageEvent<RenderResponseMsg>) => {
    const msg = ev.data;
    const p = pending.get(msg.requestId);
    if (!p) return;

    if (msg.type === 'progress') {
      p.onProgress?.(msg.percent);
    } else if (msg.type === 'done') {
      pending.delete(msg.requestId);
      if (msg.log) {
        const hasIssue = /failed|スキップ|error|warning|exception/i.test(msg.log);
        if (hasIssue) {
          console.warn('[voseCoreClient] レンダリングログ:\n' + msg.log);
        }
      }
      try {
        const cleanedWav = cleanWavArrayBuffer(msg.wav);
        const url = createWavBlobUrl(cleanedWav);
        p.resolve(url);
      } catch (e: any) {
        p.reject(new Error(`WAV Blob生成エラー: ${e?.message || e}`));
      }
    } else if (msg.type === 'error') {
      pending.delete(msg.requestId);
      p.reject(new Error(msg.message));
    }
  };
  worker.onerror = (e) => {
    console.error('[voseCoreClient] Worker error:', e.message);
    for (const [id, p] of pending) {
      p.reject(new Error(e.message || 'voseCoreWorker crashed'));
      pending.delete(id);
    }
    // 壊れたワーカーインスタンスを速やかに破棄して再利用を防止
    try { worker?.terminate(); } catch (_) {}
    worker = null;
  };
  return worker;
}

export let lastUsedEngine: 'wasm' | 'js-fallback' | null = null;

/**
 * C++ WebAssembly (vose_core.wasm / WORLDボコーダー) 専用レンダリング関数。
 * TD-PSOLAへの無言フォールバックを排除し、完全なWORLDボコーダー合成に一本化。
 */
export async function renderStudioCore(
  notes: any[],
  tempo: number,
  voicebank: string,
  onProgress?: (pct: number) => void
): Promise<string | null> {
  if (!notes || notes.length === 0) return null;

  console.log('[voseCoreClient] 🚀 C++ WebAssembly エンジン (vose_core.wasm / WORLDボコーダー) でレンダリングを開始します...');
  try {
    const result = await renderViaCore(notes, tempo, voicebank, onProgress);
    lastUsedEngine = 'wasm';
    console.log('[voseCoreClient] ✅ C++ WebAssembly エンジン (vose_core.wasm / WORLDボコーダー) での合成が正常に完了しました！');
    return result;
  } catch (err: any) {
    console.error('[voseCoreClient] ❌ C++ WebAssembly (vose_core.wasm) レンダリングエラー:', err);
    throw new Error(`C++ WORLDボコーダー合成エラー: ${err?.message || err}`);
  }
}

async function renderViaCore(
  notes: any[],
  tempo: number,
  voicebank: string,
  onProgress?: (pct: number) => void
): Promise<string | null> {
  const sortedNotes = [...notes].sort((a, b) => (a.tick || 0) - (b.tick || 0));
  if (sortedNotes.length === 0) return null;

  onProgress?.(2);

  // 1. タイムライン上のテンポ変更ポイントを収集し、正確な tick -> 秒 変換テーブルを構築
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

  // 2. 必要なサンプル（音素）の一覧を収集
  const uniqueSampleMap = new Map<string, { alias: string; prevLyric?: string; noteNum: number }>();
  for (let i = 0; i < sortedNotes.length; i++) {
    const n = sortedNotes[i];
    if (isRest(n.lyric)) continue;

    const lyric = n.lyric || 'あ';
    const prevNote = i > 0 ? sortedNotes[i - 1] : null;
    const isContinuous = prevNote && !isRest(prevNote.lyric) && ((n.tick || 0) - ((prevNote.tick || 0) + (prevNote.length || 480)) <= 240);
    const prevLyric = isContinuous ? prevNote.lyric : undefined;
    const noteNum = n.noteNum || 60;
    const key = `${voicebank}:${lyric}:${prevLyric || ''}:${noteNum}`;

    if (!uniqueSampleMap.has(key)) {
      uniqueSampleMap.set(key, { alias: lyric, prevLyric, noteNum });
    }
  }

  onProgress?.(5);

  // 3. サンプルを並行バッチで取得
  const sampleEntries = Array.from(uniqueSampleMap.entries());
  const rawSampleMap = new Map<string, FetchedRawSample | null>();
  const BATCH_SIZE = 8;
  for (let i = 0; i < sampleEntries.length; i += BATCH_SIZE) {
    const batch = sampleEntries.slice(i, i + BATCH_SIZE);
    await Promise.all(
      batch.map(async ([key, req]) => {
        const s = await fetchRawSample(voicebank, req.alias, req.prevLyric, req.noteNum);
        rawSampleMap.set(key, s);
      })
    );
    onProgress?.(Math.min(30, Math.round(5 + ((i + batch.length) / Math.max(1, sampleEntries.length)) * 25)));
  }

  const samples: WorkerSampleEntry[] = [];
  // OtoEntry.alias はC++側で固定64バイト。WASM側に渡すキーは常に短いASCII識別子(wasmKey)にする
  const cacheKeyToWasmKey = new Map<string, string>();
  let wasmKeySeq = 0;
  for (const [key, s] of rawSampleMap) {
    if (!s) continue;
    const wasmKey = `s${wasmKeySeq++}`;
    cacheKeyToWasmKey.set(key, wasmKey);
    const origAlias = uniqueSampleMap.get(key)?.alias || '';
    samples.push({ key: wasmKey, pcmF32: s.pcmF32.buffer.slice(0), oto: s.oto, origAlias });
  }

  // 4. NoteEvent列を正確なサンプル蓄積・タイムライン整合で構築する
  // C++ vose_core.wasm の note_samples_safe(p) は:
  //   note_samples = (p - 1) * 220.5 + 1  (44.1kHz, 5ms周期)
  // かつ連続有声ノート接続時に declick (最大88サンプル = 2ms) をオーバーラップ・減算する。
  // 各イベントごとに目標サンプル時刻との差分を累積して p を決定することで、
  // 5msの欠落やデクリックによる累積誤差を完全にゼロ化する。
  const workerNotes: WorkerNoteEntry[] = [];
  let cursorTick = 0;
  let accumulatedTimelineSamples = 0;
  let lastWasVoiced = false;

  const pushEvent = (
    key: string | null,
    note: any | null,
    targetEndTimeSec: number,
    durationMs: number
  ) => {
    const targetEndSample = Math.round(targetEndTimeSec * 44100);
    const neededAdvance = targetEndSample - accumulatedTimelineSamples;
    if (neededAdvance <= 0 && key === null) {
      return;
    }

    const isVoiced = key !== null;
    const declickEst = (isVoiced && lastWasVoiced) ? 88 : 0;
    const neededSamples = Math.max(0, neededAdvance + declickEst);

    // (p - 1) * 220.5 + 1 ≈ neededSamples
    const pMinus1 = Math.round(Math.max(1, neededSamples - 1) / 220.5);
    const p = Math.max(2, pMinus1 + 1);

    const actualSamples = (p - 1) * 220.5 + 1;
    const actualDeclick = (isVoiced && lastWasVoiced)
      ? Math.min(88, Math.floor(actualSamples / 8))
      : 0;
    const actualAdvance = actualSamples - actualDeclick;
    accumulatedTimelineSamples += actualAdvance;

    if (isVoiced && note) {
      workerNotes.push({
        key,
        pitchCurveHz: buildPitchCurveHz(note, p, durationMs),
        intensity: typeof note.intensity === 'number' ? note.intensity : 100,
        modulation: typeof note.modulation === 'number' ? note.modulation : 100
      });
      lastWasVoiced = true;
    } else {
      workerNotes.push({
        key: null,
        pitchCurveHz: silentFrames(p)
      });
      lastWasVoiced = false;
    }
  };

  for (let i = 0; i < sortedNotes.length; i++) {
    const n = sortedNotes[i];
    const startTick = Math.max(0, n.tick || 0);
    const length = Math.max(1, n.length || 480);
    const endTick = startTick + length;

    // 前のノートとの間にギャップ（無音）があれば無音イベントを挿入
    if (startTick > cursorTick) {
      const gapStartSec = tickToTimeSec(cursorTick);
      const gapEndSec = tickToTimeSec(startTick);
      const gapDurationMs = (gapEndSec - gapStartSec) * 1000;
      pushEvent(null, null, gapEndSec, gapDurationMs);
      cursorTick = startTick;
    }

    const noteStartSec = tickToTimeSec(startTick);
    const noteEndSec = tickToTimeSec(endTick);
    const noteDurationMs = (noteEndSec - noteStartSec) * 1000;

    if (isRest(n.lyric)) {
      pushEvent(null, null, noteEndSec, noteDurationMs);
    } else {
      const lyric = n.lyric || 'あ';
      const prevNote = i > 0 ? sortedNotes[i - 1] : null;
      const isContinuous = prevNote && !isRest(prevNote.lyric) && (startTick - ((prevNote.tick || 0) + (prevNote.length || 480)) <= 240);
      const prevLyric = isContinuous ? prevNote.lyric : undefined;
      const noteNum = n.noteNum || 60;
      const key = `${voicebank}:${lyric}:${prevLyric || ''}:${noteNum}`;

      const s = rawSampleMap.get(key);
      const wasmKey = cacheKeyToWasmKey.get(key);
      if (!s || !wasmKey) {
        // サンプル取得失敗時は無音で埋めてタイミングを保持
        pushEvent(null, null, noteEndSec, noteDurationMs);
      } else {
        pushEvent(wasmKey, n, noteEndSec, noteDurationMs);
      }
    }

    cursorTick = Math.max(cursorTick, endTick);
  }

  if (workerNotes.length === 0) return null;

  onProgress?.(35);

  const w = getWorker();
  const requestId = nextRequestId++;

  const resultPromise = new Promise<string | null>((resolve, reject) => {
    pending.set(requestId, { resolve, reject, onProgress: (pct: number) => onProgress?.(Math.round(35 + pct * 0.65)) });
  });

  const msg: RenderRequestMsg = {
    type: 'render',
    requestId,
    samples,
    notes: workerNotes,
    modeFlag: 0
  };

  const transferables = samples.map((s) => s.pcmF32);
  w.postMessage(msg, transferables);

  const url = await resultPromise;
  onProgress?.(100);
  return url;
}
