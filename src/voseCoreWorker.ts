// ============================================================
// voseCoreWorker.ts
//
// execute_render() はWORLD解析(Harvest/CheapTrick/D4C)を含む重い同期処理で、
// メインスレッドのJSから ccall() で直接呼ぶとブラウザタブが完全にフリーズし、
// 進捗表示すら更新できなくなる（「一生終わらない」ように見える原因）。
// ここでWASMモジュールの実行そのものをWeb Workerへ移し、メインスレッドは
// 応答性を保ったまま結果を待てるようにする。
//
// ★重要: このWorkerは type:'module' で生成される前提。モジュールWorker内では
// importScripts() が使えない(仕様上非対応・例外になる)ため、vose_core.js は
// 動的 import() で読み込む。そのためビルド側も -s EXPORT_ES6=1 で
// 正式なESモジュール(`export default createVoseCoreModule`)として
// 出力する必要がある(build-wasm.yml参照)。
//
// 注意: Web WorkerにはDOM(document)もWeb Audio APIも無いため、
// サンプルのfetch/decode/リサンプリングは引き続きメインスレッド
// (voseCoreClient.ts)側で行い、ここには「登録キー＋16bit PCM＋oto値」と
// 「NoteEvent構築に必要なプレーンな値」だけを渡す。
//
// [修正] 2点追加/修正:
//   1. set_oto_data() を呼ぶロジックが丸ごと欠けていたため追加。
//      (vose_core.h の OtoEntry は wasm32で632バイト、フィールドは
//       filename/cutoff/alias[64]/wav_path[512]/offset/consonant/blank/
//       preutterance/overlap。実際にレンダリングで参照されるのは
//       alias(map_time用のoffset/consonant/cutoff)・preutterance・overlap
//       のみで、filenameとwav_path[512]・blankは未使用。filenameは
//       ダングリングポインタを避けるため常に0(nullptr)を渡す)
//   2. 休符/ノート間の無音区間を NO_VOICE な NoteEvent (wav_path=0) として
//      明示的に渡せるようにした。execute_render_impl 側はノートを
//      絶対時刻ではなく pitch_length(5msフレーム数)の単純連結として
//      扱うため、無音区間を渡し忘れると曲全体のタイミングがズレる。
//   3. [バグ修正] 元の実装は allocatedPtrs (notesPtr/pitchCurvePtr/
//      wavPathPtr等)の解放処理が `if (progressFnPtr)` の中に入っており、
//      addFunction()呼び出し前に例外が起きた場合にメモリリークしていた。
//      解放処理をprogressFnPtrの有無から独立させた。
// ============================================================

interface VoseCoreModule {
  ccall: (name: string, retType: string | null, argTypes: string[], args: unknown[]) => unknown;
  setValue: (ptr: number, value: number, type: string) => void;
  stringToUTF8: (str: string, outPtr: number, maxBytes: number) => void;
  lengthBytesUTF8: (str: string) => number;
  _malloc: (size: number) => number;
  _free: (ptr: number) => void;
  addFunction: (fn: (...args: number[]) => number | void, signature: string) => number;
  removeFunction: (ptr: number) => void;
  HEAPU8: Uint8Array;
  HEAPF64: Float64Array;
  FS: {
    readFile: (path: string, opts?: { encoding?: string }) => Uint8Array;
    unlink?: (path: string) => void;
  };
}

// @ts-ignore
declare const self: DedicatedWorkerGlobalScope;

self.onerror = (e) => {
  console.error('[Worker Error]', e.message, e.filename, e.lineno);
};

// ------------------------------------------------------------
// NoteEvent構造体レイアウト (vose_core.h より。wasm32=ポインタ4バイト前提。
// 全フィールドが4バイト境界に収まるため、doubleを直接メンバに持たない
// この構造体には隠れたパディングが一切無い。合計44バイト)
// ------------------------------------------------------------
const NOTE_EVENT_SIZE = 44;
const OFF_WAV_PATH = 0;
const OFF_PITCH_CURVE = 4;
const OFF_PITCH_LENGTH = 8;
const OFF_GENDER_CURVE = 12;
const OFF_TENSION_CURVE = 16;
const OFF_BREATH_CURVE = 20;
const OFF_VIBRATO_DEPTH_CURVE = 24;
const OFF_VIBRATO_RATE_CURVE = 28;
const OFF_VIBRATO_CURVE_LENGTH = 32;
const OFF_PORTAMENTO_OFFSETS = 36;
const OFF_PORTAMENTO_LENGTH = 40;

// ------------------------------------------------------------
// OtoEntry構造体レイアウト (vose_core.h より。wasm32前提)
//   struct OtoEntry {
//     const char* filename;   // 4B  (offset 0)
//     double      cutoff;     // 8B  (4Bパディング後、offset 8)
//     char        alias[64];  //     (offset 16)
//     char        wav_path[512]; //  (offset 80)
//     double      offset;     //     (offset 592, 592は8の倍数なのでパディング無し)
//     double      consonant;  //     (offset 600)
//     double      blank;      //     (offset 608, ※未使用フィールド)
//     double      preutterance; //   (offset 616)
//     double      overlap;    //     (offset 624)
//   };                        // 合計632バイト
//
// レンダリング側(map_time/execute_render_impl)が実際に参照するのは
// alias(mapキー)・offset・consonant・cutoff・preutterance・overlapのみ。
// filenameとwav_path[512]は未使用、blankも未使用(cutoffが実際の
// 右ブランク/カットオフとして使われる)。filenameはダングリング
// ポインタを避けるため常に0(nullptr)を渡す。
// ------------------------------------------------------------
const OTO_ENTRY_SIZE = 632;
const OFF_OTO_FILENAME = 0;
const OFF_OTO_CUTOFF = 8;
const OFF_OTO_ALIAS = 16;
const OTO_ALIAS_MAX_BYTES = 64;
const OFF_OTO_WAV_PATH = 80;
const OTO_WAV_PATH_MAX_BYTES = 512;
const OFF_OTO_OFFSET = 592;
const OFF_OTO_CONSONANT = 600;
const OFF_OTO_BLANK = 608;
const OFF_OTO_PREUTTERANCE = 616;
const OFF_OTO_OVERLAP = 624;

let modPromise: Promise<VoseCoreModule> | null = null;

// C++側の fprintf(stderr/stdout, ...) をここで横取りして直近N行を保持する。
// Safari等でWeb Workerのコンソール出力を追うのが難しい環境でも、
// レンダリング失敗時にこのログをエラーメッセージへ含めて
// メインスレッド(アプリの通常のログ表示)へ届けるため。
const MAX_CAPTURED_LINES = 30;
const capturedLog: string[] = [];
function captureLine(line: string): void {
  capturedLog.push(line);
  if (capturedLog.length > MAX_CAPTURED_LINES) capturedLog.shift();
}
function getCapturedLogText(): string {
  return capturedLog.join('\n');
}

async function getModule(): Promise<VoseCoreModule> {
  if (modPromise) return modPromise;
  modPromise = (async () => {
    const wasmJsUrl = new URL('/wasm/vose_core.js', self.location.origin).href;
    const mod = await import(/* @vite-ignore */ wasmJsUrl);
    const createVoseCoreModule = mod.default || mod;
    return await (createVoseCoreModule as any)({
      locateFile: (path: string) => (path.endsWith('.wasm') ? '/wasm/vose_core.wasm' : path),
      print: (text: string) => {
        captureLine(text);
        console.log('[vose_core stdout]', text);
      },
      printErr: (text: string) => {
        captureLine(text);
        console.error('[vose_core stderr]', text);
      }
    });
  })();
  return modPromise;
}

function allocDoubleArray(mod: VoseCoreModule, values: number[] | null): number {
  if (!values || values.length === 0) return 0;
  const ptr = mod._malloc(values.length * 8);
  mod.HEAPF64.set(Float64Array.from(values), ptr / 8);
  return ptr;
}

function allocCString(mod: VoseCoreModule, str: string): number {
  const bytes = mod.lengthBytesUTF8(str) + 1;
  const ptr = mod._malloc(bytes);
  mod.stringToUTF8(str, ptr, bytes);
  return ptr;
}

// 固定長バッファ(alias[64]/wav_path[512]等)へ書き込む。
// maxBytes(null終端込み)を超える場合はstringToUTF8側で自動的に
// 末尾が切り詰められる(uto側の実装がmaxBytesWriteで打ち切るため安全)。
function writeFixedString(mod: VoseCoreModule, str: string, outPtr: number, maxBytes: number): void {
  mod.stringToUTF8(str, outPtr, maxBytes);
}

export interface OtoData {
  offsetMs: number; // 左ブランク
  consonantMs: number; // 子音固定範囲
  cutoffMs: number; // 右ブランク/カットオフ (負なら末尾からの距離。oto.ini生値をそのまま渡す)
  preutteranceMs: number;
  overlapMs: number;
}

export interface WorkerSampleEntry {
  key: string; // load_embedded_resourceのphoneme = oto.aliasとして使う
  pcm16: ArrayBuffer; // Int16Array の実体をTransferableで受け取る
  oto: OtoData;
}

export interface WorkerNoteEntry {
  // nullの場合は無声区間(休符/ノート間ギャップ)として扱う。
  // その場合 wav_path=0(nullptr) を渡し、pitch_curveの値は無視される
  // (pitchCurveHz.lengthだけがpitch_length=フレーム数として使われる)。
  key: string | null;
  pitchCurveHz: number[];
}

export interface RenderRequestMsg {
  type: 'render';
  requestId: number;
  samples: WorkerSampleEntry[];
  notes: WorkerNoteEntry[];
  modeFlag: number;
}

export type RenderResponseMsg =
  | { type: 'progress'; requestId: number; percent: number }
  | { type: 'done'; requestId: number; wav: ArrayBuffer }
  | { type: 'error'; requestId: number; message: string };

self.onmessage = async (ev: MessageEvent<RenderRequestMsg>) => {
  console.log('[Worker] received message:', ev.data.type);
  const msg = ev.data;
  if (!msg || msg.type !== 'render') return;
  const { requestId, samples, notes, modeFlag } = msg;

  let progressFnPtr = 0;
  const allocatedPtrs: number[] = [];

  try {
    console.log('[Worker] waiting for getModule()...');
    const mod = await getModule();
    console.log('[Worker] getModule() resolved');

    // 1. サンプルをWASM側へ登録する(PCM)
    for (const s of samples) {
      const view = new Int16Array(s.pcm16);
      const pcmPtr = mod._malloc(view.length * 2);
      mod.HEAPU8.set(new Uint8Array(view.buffer, view.byteOffset, view.byteLength), pcmPtr);
      mod.ccall('load_embedded_resource', null, ['string', 'number', 'number'], [s.key, pcmPtr, view.length]);
      mod._free(pcmPtr);
    }

    // 2. oto.iniデータをWASM側へ登録する(set_oto_data)
    //    ※これが無いと execute_render_impl は kDefaultOto (全フィールド0)
    //      でレンダリングしてしまい、preutterance/overlap/consonant/cutoffが
    //      一切効かなくなる。
    if (samples.length > 0) {
      const otoPtr = mod._malloc(samples.length * OTO_ENTRY_SIZE);
      allocatedPtrs.push(otoPtr);

      for (let i = 0; i < samples.length; i++) {
        const { key, oto } = samples[i];
        const base = otoPtr + i * OTO_ENTRY_SIZE;

        // filenameは未使用フィールド。ダングリングポインタを避けるため0固定。
        mod.setValue(base + OFF_OTO_FILENAME, 0, 'i32');
        mod.setValue(base + OFF_OTO_CUTOFF, oto.cutoffMs, 'double');
        writeFixedString(mod, key, base + OFF_OTO_ALIAS, OTO_ALIAS_MAX_BYTES);
        writeFixedString(mod, key, base + OFF_OTO_WAV_PATH, OTO_WAV_PATH_MAX_BYTES);
        mod.setValue(base + OFF_OTO_OFFSET, oto.offsetMs, 'double');
        mod.setValue(base + OFF_OTO_CONSONANT, oto.consonantMs, 'double');
        mod.setValue(base + OFF_OTO_BLANK, 0, 'double'); // 未使用フィールド
        mod.setValue(base + OFF_OTO_PREUTTERANCE, oto.preutteranceMs, 'double');
        mod.setValue(base + OFF_OTO_OVERLAP, oto.overlapMs, 'double');
      }

      mod.ccall('set_oto_data', null, ['number', 'number'], [otoPtr, samples.length]);
      // set_oto_data内部は g_oto_db[entries[i].alias] = entries[i] と値コピー
      // するため、呼び出し後すぐ解放してよい。
      mod._free(otoPtr);
      allocatedPtrs.pop(); // 上で解放済みなのでfinallyでの二重freeを防ぐ
    }

    // 3. NoteEvent配列を構築する(休符/ギャップは key=null で無声ノートとして渡す)
    const notesPtr = mod._malloc(notes.length * NOTE_EVENT_SIZE);
    allocatedPtrs.push(notesPtr);

    for (let i = 0; i < notes.length; i++) {
      const { key, pitchCurveHz } = notes[i];
      const base = notesPtr + i * NOTE_EVENT_SIZE;

      const isVoiced = key !== null;
      const pitchCurvePtr = isVoiced ? allocDoubleArray(mod, pitchCurveHz) : 0;
      if (pitchCurvePtr) allocatedPtrs.push(pitchCurvePtr);
      const wavPathPtr = isVoiced ? allocCString(mod, key as string) : 0;
      if (wavPathPtr) allocatedPtrs.push(wavPathPtr);

      mod.setValue(base + OFF_WAV_PATH, wavPathPtr, 'i32');
      mod.setValue(base + OFF_PITCH_CURVE, pitchCurvePtr, 'i32');
      mod.setValue(base + OFF_PITCH_LENGTH, pitchCurveHz.length, 'i32');
      mod.setValue(base + OFF_GENDER_CURVE, 0, 'i32');
      mod.setValue(base + OFF_TENSION_CURVE, 0, 'i32');
      mod.setValue(base + OFF_BREATH_CURVE, 0, 'i32');
      mod.setValue(base + OFF_VIBRATO_DEPTH_CURVE, 0, 'i32');
      mod.setValue(base + OFF_VIBRATO_RATE_CURVE, 0, 'i32');
      mod.setValue(base + OFF_VIBRATO_CURVE_LENGTH, 0, 'i32');
      mod.setValue(base + OFF_PORTAMENTO_OFFSETS, 0, 'i32');
      mod.setValue(base + OFF_PORTAMENTO_LENGTH, 0, 'i32');
    }

    // 4. レンダリング実行 (execute_render_cancelable で進捗をメインスレッドへ
    //    中継する。ProgressCallback = void(*)(int) をJS関数から生成する)
    const outputPath = '/vose_output.wav';
    progressFnPtr = mod.addFunction((percent: number) => {
      const resp: RenderResponseMsg = { type: 'progress', requestId, percent };
      (self as unknown as Worker).postMessage(resp);
    }, 'vi');

    mod.ccall(
      'execute_render_cancelable',
      null,
      ['number', 'number', 'string', 'number', 'number', 'number'],
      [notesPtr, notes.length, outputPath, modeFlag, progressFnPtr, 0] // cancel_cb=0(nullptr)=キャンセル無し
    );

    let wavBytes: Uint8Array;
    try {
      wavBytes = mod.FS.readFile(outputPath, { encoding: 'binary' }) as Uint8Array;
    } catch (readErr) {
      // vose_core.cpp側の設計: 1ノートでも例外が出たら
      // failed_during_synth=true でWAVを書き出さずreturnする
      // (「例外発生時はレンダリングを中断（wavwriteしない）」)。
      // そのため出力ファイルが存在しない=中断されたケースがほとんど。
      // 実際の失敗理由は fprintf(stderr, "[Render] Worker thread failed: %s") で
      // 出力されているはずなので、捕まえたログを添えてエラーにする。
      const log = getCapturedLogText();
      throw new Error(
        `WAV出力が見つかりません(レンダリングが中断された可能性が高いです)。` +
        (log ? `\n--- vose_core ログ ---\n${log}` : '(vose_coreからのログ出力なし)')
      );
    }
    const wavCopy = new Uint8Array(wavBytes); // WASMヒープ外へコピー
    try { mod.FS.unlink?.(outputPath); } catch (e) { /* ignore */ }

    const resp: RenderResponseMsg = { type: 'done', requestId, wav: wavCopy.buffer };
    (self as unknown as Worker).postMessage(resp, [wavCopy.buffer]);
  } catch (err: any) {
    console.error('[Worker] Error caught:', err);
    const baseMsg = err?.message || String(err);
    // 既に上でログを埋め込んだメッセージ(WAV出力が見つかりません...)は
    // 二重に付与しない。それ以外の一般的な例外(WASM側のAbort等)には
    // 捕まえたログをここで添える。
    const alreadyHasLog = typeof baseMsg === 'string' && baseMsg.includes('--- vose_core ログ ---');
    const log = alreadyHasLog ? '' : getCapturedLogText();
    const message = log ? `${baseMsg}\n--- vose_core ログ ---\n${log}` : baseMsg;
    const resp: RenderResponseMsg = { type: 'error', requestId, message };
    (self as unknown as Worker).postMessage(resp);
  } finally {
    // [修正] 以前はこのブロック全体が `if (progressFnPtr)` の中にあり、
    // addFunction()呼び出し前に例外が発生するとNoteEvent/oto用に確保した
    // メモリが一切解放されずリークしていた。解放処理は常に実行する。
    try {
      const mod = await getModule();
      if (progressFnPtr) {
        try { mod.removeFunction(progressFnPtr); } catch (e) { /* ignore */ }
      }
      for (const ptr of allocatedPtrs) {
        try { mod._free(ptr); } catch (e) { /* ignore */ }
      }
    } catch (e) { /* ignore */ }
  }
};
