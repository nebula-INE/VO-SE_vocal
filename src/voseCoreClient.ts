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
  'r', 'r_', '息', 'br', 'pau', 'sil', '吸', '', ' ', '　', '休', '・', '-', 'ー', '~'
]);

function isRest(lyric?: string): boolean {
  if (!lyric) return true;
  const l = lyric.trim().toLowerCase();
  return REST_LYRICS_SET.has(l);
}

interface FetchedRawSample {
  pcm16: Int16Array;
  baseMidi: number;
  oto: OtoData;
}

let sharedDecodeCtx: AudioContext | null = null;

async function decodeToPcm16(arrayBuf: ArrayBuffer): Promise<Int16Array> {
  const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
  if (!sharedDecodeCtx || sharedDecodeCtx.state === 'closed') {
    sharedDecodeCtx = new AudioCtx({ sampleRate: CORE_SAMPLE_RATE });
  }
  const audioBuffer = await sharedDecodeCtx.decodeAudioData(arrayBuf.slice(0));
  const float32 = audioBuffer.getChannelData(0);
  const int16 = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return int16;
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
    const pcm16 = await decodeToPcm16(arrayBuf);
    return { pcm16, baseMidi, oto };
  } catch (err) {
    console.warn(`[voseCoreClient] サンプル取得/デコード失敗 alias='${alias}':`, err);
    return null;
  }
}

// ノートのピッチベンド(PBS/PBW/PBY)を、5msフレーム周期の絶対Hzカーブへ変換する。
function buildPitchCurveHz(note: any, durationMs: number): number[] {
  const baseHz = 440 * Math.pow(2, (note.noteNum - 69) / 12);
  const frameCount = Math.max(1, Math.round(durationMs / PITCH_FRAME_PERIOD_MS));

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
    const tMs = i * PITCH_FRAME_PERIOD_MS;
    curve[i] = baseHz * Math.pow(2, bendSemitoneAt(tMs) / 12);
  }
  return curve;
}

function silentFrames(durationMs: number): number[] {
  const frameCount = Math.max(1, Math.round(durationMs / PITCH_FRAME_PERIOD_MS));
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
      const blob = new Blob([msg.wav], { type: 'audio/wav' });
      p.resolve(URL.createObjectURL(blob));
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
  };
  return worker;
}

/**
 * renderWasm(=wasmEngine.tsのrenderStudioOffline)と同一シグネチャの
 * ドロップイン代替。本物のvose_core WASMコアで合成し、失敗時は
 * 自動的にJS実装(PSOLA版)へフォールバックする。
 */
export async function renderStudioCore(
  notes: any[],
  tempo: number,
  voicebank: string,
  onProgress?: (pct: number) => void
): Promise<string | null> {
  if (!notes || notes.length === 0) return null;

  try {
    return await renderViaCore(notes, tempo, voicebank, onProgress);
  } catch (err) {
    console.warn('[voseCoreClient] vose_core WASM経由のレンダリングに失敗。JS実装(PSOLA版)にフォールバックします:', err);
    return await renderStudioOffline(notes, tempo, voicebank, onProgress);
  }
}

async function renderViaCore(
  notes: any[],
  tempo: number,
  voicebank: string,
  onProgress?: (pct: number) => void
): Promise<string | null> {
  const sortedNotes = [...notes].sort((a, b) => (a.tick || 0) - (b.tick || 0));
  const tickDurationSec = 60 / (tempo * 480);

  onProgress?.(2);

  interface NoteInfo {
    note: any;
    startTick: number;
    endTick: number;
    durationMs: number;
    cacheKey: string | null; // null = 休符
  }
  const noteInfos: NoteInfo[] = [];
  const uniqueSampleMap = new Map<string, { alias: string; prevLyric?: string; noteNum: number }>();

  for (let i = 0; i < sortedNotes.length; i++) {
    const n = sortedNotes[i];
    const startTick = n.tick || 0;
    const endTick = startTick + (n.length || 480);
    const durationMs = (n.length || 480) * tickDurationSec * 1000;

    if (isRest(n.lyric)) {
      noteInfos.push({ note: n, startTick, endTick, durationMs, cacheKey: null });
      continue;
    }

    const lyric = n.lyric || 'あ';
    const prevNote = i > 0 ? sortedNotes[i - 1] : null;
    const isContinuous = prevNote && (n.tick - (prevNote.tick + prevNote.length) <= 240);
    const prevLyric = isContinuous ? prevNote.lyric : undefined;
    const noteNum = n.noteNum || 60;
    const key = `${voicebank}:${lyric}:${prevLyric || ''}:${noteNum}`;

    if (!uniqueSampleMap.has(key)) {
      uniqueSampleMap.set(key, { alias: lyric, prevLyric, noteNum });
    }
    noteInfos.push({ note: n, startTick, endTick, durationMs, cacheKey: key });
  }

  if (noteInfos.length === 0) return null;

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
    onProgress?.(Math.min(30, Math.round(5 + ((i + batch.length) / sampleEntries.length) * 25)));
  }

  const samples: WorkerSampleEntry[] = [];
  // [修正] OtoEntry.alias はC++側で固定64バイト。今のcacheKey
  // (`voicebank:歌詞:直前歌詞:noteNum`)はボイスバンク名に日本語を含み、
  // UTF-8で簡単に64バイトを超えてしまう。load_embedded_resource側は
  // 文字列長の制限が無いため、set_oto_data側だけ切り詰められて
  // 両者のキーが一致しなくなる(oto.iniが引けない/最悪未定義動作)おそれが
  // あった。WASM側に渡すキーは常に短いASCII識別子(wasmKey)に分離し、
  // 元のcacheKeyはJS側のサンプルキャッシュだけに使う。
  const cacheKeyToWasmKey = new Map<string, string>();
  let wasmKeySeq = 0;
  for (const [key, s] of rawSampleMap) {
    if (!s) continue;
    const wasmKey = `s${wasmKeySeq++}`;
    cacheKeyToWasmKey.set(key, wasmKey);
    samples.push({ key: wasmKey, pcm16: s.pcm16.buffer.slice(0), oto: s.oto });
  }

  // NoteEvent列を「絶対時刻を持たない連結列」として構築する。
  // ノート間・曲頭にギャップがあれば無声(key=null)ノートで明示的に埋める。
  const workerNotes: WorkerNoteEntry[] = [];
  let cursorTick = 0;

  const pushSilence = (durationMs: number) => {
    if (durationMs <= 0) return;
    workerNotes.push({ key: null, pitchCurveHz: silentFrames(durationMs) });
  };

  for (const info of noteInfos) {
    if (info.startTick > cursorTick) {
      const gapTicks = info.startTick - cursorTick;
      pushSilence(gapTicks * tickDurationSec * 1000);
    }

    if (info.cacheKey === null) {
      pushSilence(info.durationMs);
    } else {
      const s = rawSampleMap.get(info.cacheKey);
      const wasmKey = cacheKeyToWasmKey.get(info.cacheKey);
      if (!s || !wasmKey) {
        // サンプル取得失敗: 無音で埋めてタイミングだけは崩さない
        pushSilence(info.durationMs);
      } else {
        workerNotes.push({
          key: wasmKey,
          pitchCurveHz: buildPitchCurveHz(info.note, info.durationMs)
        });
      }
    }
    cursorTick = Math.max(cursorTick, info.endTick);
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

  const transferables = samples.map((s) => s.pcm16);
  w.postMessage(msg, transferables);

  const url = await resultPromise;
  onProgress?.(100);
  return url;
}
