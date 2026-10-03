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
//   - NoteEvent carries an absolute start_time_ms plus optional per-note
//     preutterance/overlap overrides. The C++ core owns final timeline placement.
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
  matchedAlias: string;
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
    const matchedAlias = decodeURIComponent(res.headers.get('X-Alias-Matched') || alias);
    const oto: OtoData = {
      offsetMs: parseFloat(res.headers.get('X-Oto-Left-Blank') || '0'),
      consonantMs: parseFloat(res.headers.get('X-Oto-Fixed-Range') || '0'),
      cutoffMs: parseFloat(res.headers.get('X-Oto-Right-Blank') || '0'),
      preutteranceMs: parseFloat(res.headers.get('X-Oto-Preutterance') || '0'),
      overlapMs: parseFloat(res.headers.get('X-Oto-Overlap') || '0')
    };

    const arrayBuf = await res.arrayBuffer();
    const pcmF32 = await decodeToPcmF32(arrayBuf);
    return { pcmF32, baseMidi, oto, matchedAlias };
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
        if (tMs <= points[0].offsetMs) return points[0].semitone;
        for (let i = 0; i < points.length - 1; i++) {
          const a = points[i];
          const b = points[i + 1];
          if (tMs >= a.offsetMs && tMs <= b.offsetMs) {
            const ratio = b.offsetMs > a.offsetMs ? (tMs - a.offsetMs) / (b.offsetMs - a.offsetMs) : 0;
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
    if (msg.type === 'error') {
      if (msg.requestId && pending.has(msg.requestId)) {
        const p = pending.get(msg.requestId)!;
        pending.delete(msg.requestId);
        p.reject(new Error(msg.message));
      } else {
        for (const [id, p] of pending) {
          p.reject(new Error(msg.message));
          pending.delete(id);
        }
      }
      return;
    }

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
 * 万が一WASM環境で問題が発生した場合も、Web Audioオフラインエンジンへ安全にフォールバック。
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
    if (result) {
      lastUsedEngine = 'wasm';
      console.log('[voseCoreClient] ✅ C++ WebAssembly エンジン (vose_core.wasm / WORLDボコーダー) での合成が正常に完了しました！');
      return result;
    }
  } catch (err: any) {
    console.warn('[voseCoreClient] ⚠️ C++ WebAssembly (vose_core.wasm) レンダリングが失敗したため、Web Audio オフラインエンジンへフォールバックします:', err);
  }

  // Graceful fallback to wasmEngine.ts renderStudioOffline
  console.log('[voseCoreClient] 🔄 Web Audio オフラインエンジンでレンダリングを実行中...');
  lastUsedEngine = 'js-fallback';
  return await renderStudioOffline(notes, tempo, voicebank, onProgress);
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

  function getTrailingVowelFromLyric(lyric?: string): string {
    const s = String(lyric || '').trim().toLowerCase();
    const m = s.match(/[aiueo]$/);
    if (m) return m[0];
    if (s.endsWith('ん') || s.endsWith('n')) return 'n';
    const kana: Record<string, string> = {
      'あ':'a','か':'a','が':'a','さ':'a','ざ':'a','た':'a','だ':'a','な':'a','は':'a','ば':'a','ぱ':'a','ま':'a','や':'a','ら':'a','わ':'a',
      'い':'i','き':'i','ぎ':'i','し':'i','じ':'i','ち':'i','ぢ':'i','に':'i','ひ':'i','び':'i','ぴ':'i','み':'i','り':'i',
      'う':'u','く':'u','ぐ':'u','す':'u','ず':'u','つ':'u','づ':'u','ぬ':'u','ふ':'u','ぶ':'u','ぷ':'u','む':'u','ゆ':'u','る':'u',
      'え':'e','け':'e','げ':'e','せ':'e','ぜ':'e','て':'e','で':'e','ね':'e','へ':'e','べ':'e','ぺ':'e','め':'e','れ':'e',
      'お':'o','こ':'o','ご':'o','そ':'o','ぞ':'o','と':'o','ど':'o','の':'o','ほ':'o','ぼ':'o','ぽ':'o','も':'o','よ':'o','ろ':'o','を':'o'
    };
    return kana[s.slice(-1)] || '';
  }

  function getInitialConsonant(note: any): string {
    const phonemes = note?.phonemes;
    const values = Array.isArray(phonemes) ? phonemes : (typeof phonemes === 'string' ? phonemes.trim().split(/\s+/) : []);

    const kanaConsonant: Record<string, string> = {
      'か':'k','き':'k','く':'k','け':'k','こ':'k','が':'g','ぎ':'g','ぐ':'g','げ':'g','ご':'g',
      'さ':'s','し':'sh','す':'s','せ':'s','そ':'s','ざ':'z','じ':'j','ず':'z','ぜ':'z','ぞ':'z',
      'た':'t','ち':'ch','つ':'ts','て':'t','と':'t','だ':'d','ぢ':'j','づ':'z','で':'d','ど':'d',
      'な':'n','に':'n','ぬ':'n','ね':'n','の':'n','は':'h','ひ':'h','ふ':'f','へ':'h','ほ':'h',
      'ば':'b','び':'b','ぶ':'b','べ':'b','ぼ':'b','ぱ':'p','ぴ':'p','ぷ':'p','ぺ':'p','ぽ':'p',
      'ま':'m','み':'m','む':'m','め':'m','も':'m','や':'y','ゆ':'y','よ':'y','ら':'r','り':'r','る':'r','れ':'r','ろ':'r',
      'わ':'w','を':'w','ん':'n'
    };

    const romanConsonant = (value: string): string => {
      const p = value.replace(/[^a-z]/g, '').toLowerCase();
      if (!p || /^(?:[aiueo]+)$/.test(p)) return '';
      const match = p.match(/^(?:ch|sh|ts|zh|jh|dz|ky|gy|ny|hy|by|py|my|ry|ty|dy|sy|zy|fy|kw|gw|[bcdfghjklmnpqrstvwxyz])/);
      return match?.[0] || '';
    };

    for (const value of values) {
      const p = String(value || '').trim().toLowerCase();
      if (!p) continue;
      if (/^(sil|pau|br|r|休|休符|・)$/.test(p)) return '';
      if (kanaConsonant[p]) return kanaConsonant[p];
      const roman = romanConsonant(p);
      if (roman) return roman;
    }

    const lyric = String(note?.lyric || '').trim().toLowerCase();
    if (kanaConsonant[lyric.slice(-1)]) return kanaConsonant[lyric.slice(-1)];

    // Romanized CV lyrics such as "ka", "shi", "tsu" are common in UTAU
    // voicebanks. Derive the consonant from the syllable rather than requiring
    // a separate phoneme array.
    return romanConsonant(lyric);
  }

  function isVcvMatchedAlias(alias: string, prevVowel: string): boolean {
    const normalized = String(alias || '').trim().toLowerCase();
    if (!normalized || !prevVowel) return false;
    if (normalized === prevVowel) return false;
    return normalized.startsWith(prevVowel + ' ') ||
      normalized.startsWith(prevVowel + '_') ||
      new RegExp(`^${prevVowel}[^a-z]`).test(normalized);
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
  // VCVを優先し、直接VC aliasが存在する場合だけCVVC遷移候補として取得する。
  const cvvcRequests = new Map<string, { alias: string; noteNum: number }>();
  for (let i = 1; i < sortedNotes.length; i++) {
    const prev = sortedNotes[i - 1];
    const n = sortedNotes[i];
    if (isRest(prev.lyric) || isRest(n.lyric)) continue;
    const gap = (n.tick || 0) - ((prev.tick || 0) + (prev.length || 480));
    if (gap > 240) continue;
    const prevVowel = getTrailingVowelFromLyric(prev.lyric);
    const consonant = getInitialConsonant(n);
    if (!prevVowel || !consonant) continue;
    const alias = prevVowel + ' ' + consonant;
    const key = voicebank + ':' + alias + ':DIRECT:' + (n.noteNum || 60);
    cvvcRequests.set(key, { alias, noteNum: n.noteNum || 60 });
  }
  const sampleEntries = Array.from(uniqueSampleMap.entries());
  const cvvcSampleEntries = Array.from(cvvcRequests.entries());
  const rawSampleMap = new Map<string, FetchedRawSample | null>();
  const BATCH_SIZE = 8;
  const allSampleEntries = [...sampleEntries, ...cvvcSampleEntries];
  for (let i = 0; i < allSampleEntries.length; i += BATCH_SIZE) {
    const batch = allSampleEntries.slice(i, i + BATCH_SIZE);
    await Promise.all(
      batch.map(async ([key, req]) => {
        const directVc = cvvcRequests.has(key);
        const s = await fetchRawSample(voicebank, req.alias, directVc ? undefined : (req as any).prevLyric, req.noteNum);
        rawSampleMap.set(key, s);
      })
    );
    onProgress?.(Math.min(30, Math.round(5 + ((i + batch.length) / Math.max(1, allSampleEntries.length)) * 25)));
  }

  const samples: WorkerSampleEntry[] = [];
  // OtoEntry.alias はC++側で固定64バイト。WASM側に渡すキーは常に短いASCII識別子(wasmKey)にする
  const cacheKeyToWasmKey = new Map<string, string>();
  let wasmKeySeq = 0;
  for (const [key, s] of rawSampleMap) {
    if (!s) continue;
    const wasmKey = `s${wasmKeySeq++}`;
    cacheKeyToWasmKey.set(key, wasmKey);
    const origAlias = uniqueSampleMap.get(key)?.alias || cvvcRequests.get(key)?.alias || s.matchedAlias;
    samples.push({ key: wasmKey, pcmF32: s.pcmF32.buffer.slice(0), oto: s.oto, origAlias });
  }

  // 4. Build NoteEvents with their real musical positions.
  // The C++ core now owns the absolute timeline, so gaps no longer need to be
  // converted into artificial sequential padding to preserve timing.
  const workerNotes: WorkerNoteEntry[] = [];

  const pushEvent = (
    key: string | null,
    note: any | null,
    startTimeSec: number,
    durationMs: number
  ) => {
    const p = Math.max(
      2,
      Math.round(Math.max(1, durationMs) / PITCH_FRAME_PERIOD_MS) + 1
    );
    const startTimeMs = Math.max(0, startTimeSec * 1000.0);

    if (key !== null && note) {
      // NoteEvent の dataclass 既定値は 0.0 なので、値が存在するだけでは
      // UST の明示指定とは限らない。明示フラグを最優先し、後方互換として
      // 正の上書き値だけは従来のWebデータからも受け付ける。
      const preValue = typeof note.pre_utterance === 'number'
        ? Number(note.pre_utterance) : NaN;
      const overlapValue = typeof note.overlap === 'number'
        ? Number(note.overlap) : NaN;
      const hasPre = note._ust_preutterance_explicit === true ||
        (Number.isFinite(preValue) && preValue > 0);
      const hasOverlap = note._ust_overlap_explicit === true ||
        (Number.isFinite(overlapValue) && overlapValue > 0);

      workerNotes.push({
        key,
        pitchCurveHz: buildPitchCurveHz(note, p, durationMs),
        intensity: typeof note.intensity === 'number' ? note.intensity : 100,
        modulation: typeof note.modulation === 'number' ? note.modulation : 0,
        startTimeMs,
        preutteranceMs: hasPre ? preValue : -1,
        overlapMs: hasOverlap ? overlapValue : -1
      });
    } else {
      workerNotes.push({
        key: null,
        pitchCurveHz: silentFrames(p),
        startTimeMs,
        preutteranceMs: -1,
        overlapMs: -1
      });
    }
  };

  let cursorTick = 0;
  for (let i = 0; i < sortedNotes.length; i++) {
    const n = sortedNotes[i];
    const startTick = Math.max(0, n.tick || 0);
    const length = Math.max(1, n.length || 480);
    const endTick = startTick + length;

    if (startTick > cursorTick) {
      const gapStartSec = tickToTimeSec(cursorTick);
      const gapEndSec = tickToTimeSec(startTick);
      const gapDurationMs = (gapEndSec - gapStartSec) * 1000;
      pushEvent(null, null, gapStartSec, gapDurationMs);
    }

    const noteStartSec = tickToTimeSec(startTick);
    const noteEndSec = tickToTimeSec(endTick);
    const noteDurationMs = (noteEndSec - noteStartSec) * 1000;

    if (isRest(n.lyric)) {
      pushEvent(null, null, noteStartSec, noteDurationMs);
    } else {
      const lyric = n.lyric || 'あ';
      const prevNote = i > 0 ? sortedNotes[i - 1] : null;
      const isContinuous = prevNote && !isRest(prevNote.lyric) &&
        (startTick - ((prevNote.tick || 0) + (prevNote.length || 480)) <= 240);
      const prevLyric = isContinuous ? prevNote.lyric : undefined;
      const noteNum = n.noteNum || 60;
      const key = `${voicebank}:${lyric}:${prevLyric || ''}:${noteNum}`;

      const s = rawSampleMap.get(key);
      const wasmKey = cacheKeyToWasmKey.get(key);
      if (!s || !wasmKey) {
        pushEvent(null, null, noteStartSec, noteDurationMs);
      } else {
        const prevForCvvc = i > 0 ? sortedNotes[i - 1] : null;
        const continuousForCvvc = !!prevForCvvc && !isRest(prevForCvvc.lyric) &&
          (startTick - ((prevForCvvc.tick || 0) + (prevForCvvc.length || 480)) <= 240);
        const prevVowelForCvvc = continuousForCvvc ? getTrailingVowelFromLyric(prevForCvvc?.lyric) : '';
        const consonantForCvvc = getInitialConsonant(n);
        const vcAlias = prevVowelForCvvc && consonantForCvvc
          ? prevVowelForCvvc + ' ' + consonantForCvvc
          : '';
        const vcKey = vcAlias ? voicebank + ':' + vcAlias + ':DIRECT:' + noteNum : '';
        const vcSample = vcKey ? rawSampleMap.get(vcKey) : null;
        const rawPreMs = Number(n.pre_utterance);
        const hasExplicitPre = n._ust_preutterance_explicit === true;
        // Web timeline notes commonly carry the dataclass default 0.0 even
        // when UST did not explicitly specify PreUtterance. In that case use
        // the following CV's OTO preutterance, matching CVVC's standard timing.
        const preMs = !hasExplicitPre && (!Number.isFinite(rawPreMs) || rawPreMs <= 0)
          ? Number(s.oto.preutteranceMs)
          : rawPreMs;
        const vcMatchesExactly = !!vcSample &&
          vcSample.matchedAlias.trim().toLowerCase() === vcAlias.toLowerCase();
        // VCV has priority when the requested previous-vowel + lyric alias
        // actually exists. CVVC is only a fallback for banks that do not
        // provide the corresponding VCV transition.
        const vcvMatched = continuousForCvvc &&
          !!s.matchedAlias &&
          isVcvMatchedAlias(s.matchedAlias, prevVowelForCvvc);
        
        if (continuousForCvvc && !vcvMatched && vcMatchesExactly && Number.isFinite(preMs) && preMs > 0) {
          const vcWasmKey = cacheKeyToWasmKey.get(vcKey);
          if (vcWasmKey) {
            const vcStartSec = Math.max(0, noteStartSec - preMs / 1000.0);
            pushEvent(vcWasmKey, n, vcStartSec, preMs);
            // The VC already occupies the preutterance window. Do not apply
            // the same preutterance a second time to the following CV.
            const cvNote = { ...n, pre_utterance: 0, overlap: 0 };
            pushEvent(wasmKey, cvNote, noteStartSec, noteDurationMs);
          } else {
            pushEvent(wasmKey, n, noteStartSec, noteDurationMs);
          }
        } else {
          pushEvent(wasmKey, n, noteStartSec, noteDurationMs);
        }
      }
    }

    cursorTick = Math.max(cursorTick, endTick);
  }

  if (workerNotes.length === 0) return null;

  onProgress?.(35);

  const w = getWorker();
  const requestId = nextRequestId++;

  let lastReportedPct = 35;
  const reportCoreProgress = (pct: number) => {
    const mapped = Math.max(35, Math.min(100, Math.round(35 + pct * 0.65)));
    // Worker初期化中の段階通知とC++側の2%通知が前後しても、
    // UIの進捗が後戻りしないように単調増加にする。
    lastReportedPct = Math.max(lastReportedPct, mapped);
    onProgress?.(lastReportedPct);
  };

  const resultPromise = new Promise<string | null>((resolve, reject) => {
    pending.set(requestId, { resolve, reject, onProgress: reportCoreProgress });
  });

  const msg: RenderRequestMsg = {
    type: 'render',
    requestId,
    samples,
    notes: workerNotes,
    modeFlag: 0
  };

  const transferables = samples.map((s) => s.pcmF32);
  try {
    w.postMessage(msg, transferables);
  } catch (err: any) {
    pending.delete(requestId);
    throw new Error('WASM Workerへのレンダリング要求送信に失敗しました: ' + (err?.message || err));
  }

  // WORLD解析 + 合成は音源数やノート数によって25秒を超えることがある。
  // 25秒で強制的にJSフォールバックへ落とすと、正常なWebレンダリングまで
  // 「35%で止まった」ように見えるため、十分な猶予を確保する。
  const timeoutMs = 180000;
  let timerId: any = null;
  const timeoutPromise = new Promise<never>((_, reject) => {
    timerId = setTimeout(() => {
      pending.delete(requestId);
      reject(new Error('WASMレンダリング処理がタイムアウトしました'));
    }, timeoutMs);
  });

  let url: string | null = null;
  try {
    url = await Promise.race([resultPromise, timeoutPromise]);
  } finally {
    if (timerId) clearTimeout(timerId);
  }

  onProgress?.(100);
  return url;
}
