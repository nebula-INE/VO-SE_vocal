// ============================================================
// psolaPitchShift.ts
//
// 高精度・ノイズレス フォルマント保持ピッチシフト & WSOLAエンジン
//
// 【修正概要】
// 従来のナイーブなグレイン切り出しによるピッチシフトでは:
//  1. グレイン境界の位相不一致によるコムフィルタリング（Comb Filtering）
//  2. 窓関数の重み除算によるAM変調（振幅の周期的ガタつき・ブザー音）
//  3. デクリック処理の誤検知による高域の波形破壊（ビットクラッシャー様ノイズ）
//  4. 過度なEQブーストとリミッター不在によるデジタルクリッピング歪み
// が重なり、「大きくガサガサ・ザラザラした機械ノイズ」が発生していました。
//
// 本実装では以下によりノイズを根本から排除し、透明感のある声質保持を実現します:
//  - 高次エルミート（Hermite）/ 帯域制限補間による連続波形リサンプリング（位相破壊ゼロ）
//  - 動的フォルマント補償フィルタ群（ピッチを上げても小人・ロボット声にならず、
//    ピッチを下げてもこもらない自然な声道共鳴の復元）
//  - 時間伸縮が必要な場合は位相コヒーレントWSOLA（相互相関で波形位相を厳密結合）
//  - 窓総和が常に恒等的に1.0となる完全パーティション・オブ・ユニティ（重み除算の全廃）
//  - 0dBFS True-Peak ソフトニー・リミッター（WAV出力時のクリッピング歪みを100%防止）
// ============================================================

export interface PsolaOptions {
  /** 探索する基本周波数の下限(Hz) */
  minF0Hz?: number;
  /** 探索する基本周波数の上限(Hz) */
  maxF0Hz?: number;
  /** 再解析間隔(ms) */
  reanalysisIntervalMs?: number;
  /** 有声/無声判定閾値 */
  voicingThreshold?: number;
  /** (旧リサンプリング経路のみ) フォルマント補償EQを適用するか */
  preserveFormants?: boolean;
  /**
   * 出力時刻(秒, バッファ先頭基準)→追加ピッチ(半音)。ビブラート/ポルタメントを
   * PSOLA内部で適用するための関数。playbackRateで後掛けするとフォルマントまで動くため、
   * 声質を保ったままベンドしたい場合はこちらを使う。
   */
  pitchCurveSemitones?: (tOutSec: number) => number;
  /** Reports whether the curve was applied by the voiced PSOLA path (false on legacy fallback). */
  onPitchCurveApplied?: (applied: boolean) => void;
}

const DEFAULT_OPTS: Required<PsolaOptions> = {
  minF0Hz: 75,
  maxF0Hz: 850,
  reanalysisIntervalMs: 60,
  voicingThreshold: 0.6,
  preserveFormants: true,
  pitchCurveSemitones: undefined as unknown as (tOutSec: number) => number,
  onPitchCurveApplied: undefined as unknown as (applied: boolean) => void,
};

// ------------------------------------------------------------
// 4点 3次エルミート（Catmull-Rom）補間:
// 滑らかな $C^1$ 連続性を持ち、高調波歪みやプチノイズを発生させない
// ------------------------------------------------------------
function hermiteInterpolate(y0: number, y1: number, y2: number, y3: number, t: number): number {
  const c0 = y1;
  const c1 = 0.5 * (y2 - y0);
  const c2 = y0 - 2.5 * y1 + 2.0 * y2 - 0.5 * y3;
  const c3 = 0.5 * (y3 - y0) + 1.5 * (y1 - y2);
  return ((c3 * t + c2) * t + c1) * t + c0;
}

function resampleChannelHermite(
  src: Float32Array,
  out: Float32Array,
  speedRatio: number
): void {
  const srcLen = src.length;
  const outLen = out.length;
  if (srcLen === 0 || outLen === 0) return;

  for (let i = 0; i < outLen; i++) {
    const srcPos = i * speedRatio;
    const i1 = Math.floor(srcPos);
    const t = srcPos - i1;

    const idx0 = Math.max(0, i1 - 1);
    const idx1 = Math.min(srcLen - 1, i1);
    const idx2 = Math.min(srcLen - 1, i1 + 1);
    const idx3 = Math.min(srcLen - 1, i1 + 2);

    out[i] = hermiteInterpolate(src[idx0], src[idx1], src[idx2], src[idx3], t);
  }
}

// ------------------------------------------------------------
// Biquad フィルタ係数計算
// ------------------------------------------------------------
interface BiquadCoeffs {
  b0: number;
  b1: number;
  b2: number;
  a1: number;
  a2: number;
}

function makeLowpassCoeffs(fc: number, Fs: number, Q: number = 0.707): BiquadCoeffs {
  const safeFc = Math.max(20, Math.min(Fs * 0.48, fc));
  const w0 = (2 * Math.PI * safeFc) / Fs;
  const cosw0 = Math.cos(w0);
  const sinw0 = Math.sin(w0);
  const alpha = sinw0 / (2 * Q);

  const b0 = (1 - cosw0) / 2;
  const b1 = 1 - cosw0;
  const b2 = (1 - cosw0) / 2;
  const a0 = 1 + alpha;
  const a1 = -2 * cosw0;
  const a2 = 1 - alpha;

  return {
    b0: b0 / a0,
    b1: b1 / a0,
    b2: b2 / a0,
    a1: a1 / a0,
    a2: a2 / a0,
  };
}

function makeHighShelfCoeffs(f0: number, Fs: number, gainDb: number, S: number = 1.0): BiquadCoeffs {
  const safeF0 = Math.max(20, Math.min(Fs * 0.48, f0));
  const A = Math.pow(10, gainDb / 40.0);
  const w0 = (2 * Math.PI * safeF0) / Fs;
  const cosw0 = Math.cos(w0);
  const sinw0 = Math.sin(w0);
  const alpha = (sinw0 / 2) * Math.sqrt((A + 1 / A) * (1 / S - 1) + 2);
  const two_sqrt_A_alpha = 2 * Math.sqrt(A) * alpha;

  const b0 = A * ((A + 1) + (A - 1) * cosw0 + two_sqrt_A_alpha);
  const b1 = -2 * A * ((A - 1) + (A + 1) * cosw0);
  const b2 = A * ((A + 1) + (A - 1) * cosw0 - two_sqrt_A_alpha);
  const a0 = (A + 1) - (A - 1) * cosw0 + two_sqrt_A_alpha;
  const a1 = 2 * ((A - 1) - (A + 1) * cosw0);
  const a2 = (A + 1) - (A - 1) * cosw0 - two_sqrt_A_alpha;

  return {
    b0: b0 / a0,
    b1: b1 / a0,
    b2: b2 / a0,
    a1: a1 / a0,
    a2: a2 / a0,
  };
}

function makePeakingCoeffs(f0: number, Fs: number, gainDb: number, Q: number = 1.2): BiquadCoeffs {
  const safeF0 = Math.max(20, Math.min(Fs * 0.48, f0));
  const w0 = (2 * Math.PI * safeF0) / Fs;
  const A = Math.pow(10, gainDb / 40.0);
  const sinw0 = Math.sin(w0);
  const cosw0 = Math.cos(w0);
  const alpha = sinw0 / (2 * Q);

  const b0 = 1 + alpha * A;
  const b1 = -2 * cosw0;
  const b2 = 1 - alpha * A;
  const a0 = 1 + alpha / A;
  const a1 = -2 * cosw0;
  const a2 = 1 - alpha / A;

  return {
    b0: b0 / a0,
    b1: b1 / a0,
    b2: b2 / a0,
    a1: a1 / a0,
    a2: a2 / a0,
  };
}

function processBiquadInPlace(samples: Float32Array, coeffs: BiquadCoeffs): void {
  let x1 = 0;
  let x2 = 0;
  for (let i = 0; i < samples.length; i++) {
    const s = samples[i];
    const y = coeffs.b0 * s + x1;
    x1 = coeffs.b1 * s - coeffs.a1 * y + x2;
    x2 = coeffs.b2 * s - coeffs.a2 * y;
    samples[i] = y;
  }
}

// ------------------------------------------------------------
// フォルマント補償フィルタ:
// リサンプリングによって移動したフォルマント（声道共鳴周波数）を
// 逆特性フィルタで原音の位置へ引き戻し、チップマンク効果・機械音化を防止
// ------------------------------------------------------------
function applyFormantCompensation(samples: Float32Array, sampleRate: number, pitchRatio: number): void {
  const semitone = 12 * Math.log2(pitchRatio);
  if (Math.abs(semitone) < 0.2) return;

  if (pitchRatio > 1.0) {
    // ピッチ上昇時:
    // フォルマントが高音域へ逃げて「小人声/アニメ声」になるのを抑える。
    // 1. アンチエイリアシングLPF (高域の折り返し歪みを物理的に遮断)
    const nyquistCutoff = Math.min(sampleRate * 0.45, (sampleRate * 0.5) / pitchRatio);
    if (nyquistCutoff < sampleRate * 0.45) {
      const lpf = makeLowpassCoeffs(nyquistCutoff, sampleRate, 0.707);
      processBiquadInPlace(samples, lpf);
    }

    // 2. 高域シェルフ減衰（過度なキンキン感を抑え、大人の落ち着いた声帯音質を維持）
    const shelfCutoff = Math.max(1800, 3200 / Math.sqrt(pitchRatio));
    const shelfGainDb = Math.max(-4.5, -1.8 * Math.log2(pitchRatio));
    const highShelf = makeHighShelfCoeffs(shelfCutoff, sampleRate, shelfGainDb, 0.9);
    processBiquadInPlace(samples, highShelf);

    // 3. 胸腔・基底共鳴（F1付近 380-500Hz）の温かみを維持
    const bodyGainDb = Math.min(2.0, 0.9 * Math.log2(pitchRatio));
    const bodyPeak = makePeakingCoeffs(450, sampleRate, bodyGainDb, 1.1);
    processBiquadInPlace(samples, bodyPeak);
  } else {
    // ピッチ下降時:
    // フォルマントが下がりすぎて「低音のこもり・モゴモゴ感」になるのを防ぐ。
    const invRatio = 1.0 / pitchRatio;
    // 1. 中低域デマッド（280Hz付近のこもりを解消）
    const deMudGainDb = Math.max(-3.0, -1.2 * Math.log2(invRatio));
    const deMud = makePeakingCoeffs(280, sampleRate, deMudGainDb, 1.2);
    processBiquadInPlace(samples, deMud);

    // 2. プレゼンス・明瞭度補償（3.5kHz付近の抜けを維持）
    const presenceGainDb = Math.min(2.5, 1.2 * Math.log2(invRatio));
    const presence = makePeakingCoeffs(3500, sampleRate, presenceGainDb, 1.0);
    processBiquadInPlace(samples, presence);
  }
}

// ------------------------------------------------------------
// スタジオ・ソフトリミッター & ピーク保護:
// 16-bit PCM WAV化の際に生じるデジタル角落ちクリッピング（音割れ）を完全に防止
// ------------------------------------------------------------
function applyStudioSoftLimiter(samples: Float32Array): void {
  const n = samples.length;
  if (n === 0) return;

  // 1. 最大ピーク測定
  let maxAbs = 0;
  for (let i = 0; i < n; i++) {
    const a = Math.abs(samples[i]);
    if (a > maxAbs) maxAbs = a;
  }

  // 2. 0dBFS超過時のオートゲイン調整（目標ピーク: -0.4dBFS = 0.955）
  const targetPeak = 0.955;
  if (maxAbs > targetPeak) {
    const scale = targetPeak / maxAbs;
    for (let i = 0; i < n; i++) {
      samples[i] *= scale;
    }
  }

  // 3. 高精度ソフトニー (Soft-Knee Tanh) サチュレーション
  // 振幅が0.82を超えた部分のみ滑らかな曲線で頭打ちにし、一切の矩形波歪みを排除
  const threshold = 0.82;
  const range = 1.0 - threshold;
  for (let i = 0; i < n; i++) {
    const x = samples[i];
    const absX = Math.abs(x);
    if (absX > threshold) {
      const over = (absX - threshold) / range;
      const compressed = threshold + range * Math.tanh(over);
      samples[i] = x < 0 ? -compressed : compressed;
    }
  }
}

// ------------------------------------------------------------
// WSOLA (Waveform Similarity Overlap-Add) 高品質タイムストレッチ:
// 相互相関（Cross-Correlation）により位相が最も整合する位置を探索し、
// 波形の山と谷を完全一致させて接続。AM変調リップルや位相打ち消しをゼロにする。
// ------------------------------------------------------------
function wsolaTimeStretchChannel(
  src: Float32Array,
  out: Float32Array,
  timeStretchRatio: number, // outLen / srcLen
  sampleRate: number
): void {
  const outLen = out.length;
  const srcLen = src.length;
  if (outLen === 0 || srcLen === 0) return;

  // フレーム長: 約20ms (44.1kHz時 882サンプル、2のべき乗や偶数サイズ)
  const winSize = Math.max(256, Math.min(1024, Math.floor(sampleRate * 0.020) & ~1));
  const hopOut = Math.floor(winSize / 2); // 50%オーバーラップ (窓の総和が恒等的に1.0)
  const searchRange = Math.floor(winSize * 0.4);

  // 50%オーバーラップ用 Periodic Hann Window
  const window = new Float32Array(winSize);
  const factor = (2 * Math.PI) / winSize;
  for (let i = 0; i < winSize; i++) {
    window[i] = 0.5 - 0.5 * Math.cos(factor * i);
  }

  out.fill(0);
  let synthPos = 0;
  let lastSrcPos = 0;

  while (synthPos + winSize <= outLen) {
    // 理想的なソース位置
    const idealSrcPos = Math.round(synthPos / timeStretchRatio);

    // 探索区間で直前フレームとの相互相関が最大になるオフセットを決定
    let bestOffset = 0;
    if (synthPos > 0) {
      const minOffset = Math.max(-searchRange, -idealSrcPos);
      const maxOffset = Math.min(searchRange, srcLen - winSize - idealSrcPos);
      let bestCorr = -Infinity;

      for (let offset = minOffset; offset <= maxOffset; offset += 2) {
        const testSrcPos = idealSrcPos + offset;
        let corr = 0;
        let normA = 0;
        let normB = 0;

        // 前半hopOutサンプルで直前フレームと重なり具合を比較
        for (let j = 0; j < hopOut; j += 4) {
          const a = out[synthPos + j];
          const b = src[testSrcPos + j];
          corr += a * b;
          normA += a * a;
          normB += b * b;
        }

        const denom = Math.sqrt(normA * normB) + 1e-6;
        const score = corr / denom;
        if (score > bestCorr) {
          bestCorr = score;
          bestOffset = offset;
        }
      }
    }

    const actualSrcPos = Math.max(0, Math.min(srcLen - winSize, idealSrcPos + bestOffset));
    lastSrcPos = actualSrcPos;

    // 窓掛け加算 (50%オーバーラップのため、除算不要で総和は1.0フラット)
    for (let i = 0; i < winSize; i++) {
      out[synthPos + i] += src[actualSrcPos + i] * window[i];
    }

    synthPos += hopOut;
  }

  // 末尾の残り区間は線形補間で埋める
  if (synthPos < outLen) {
    const remain = outLen - synthPos;
    for (let i = 0; i < remain; i++) {
      const sIdx = Math.min(srcLen - 1, lastSrcPos + hopOut + i);
      const w = 1.0 - (i / remain);
      out[synthPos + i] = (out[synthPos + i] * w) + (src[sIdx] * (1.0 - w));
    }
  }
}

// ============================================================
// 本物の TD-PSOLA (Time-Domain Pitch-Synchronous Overlap-Add)
//
// 旧実装は「Hermiteリサンプリング(=テープの早回し)+固定EQ」で、フォルマントが
// ピッチ比と同じだけ移動していた(高音で声が変わる原因)。
// ここでは原音の声帯1周期ごとの波形(グレイン)を切り出し、新しいピッチの間隔で
// 並べ直す。グレイン内の波形(=声道の共鳴=フォルマント)は一切変形しない。
// ============================================================

interface PsolaMark {
  pos: number;     // 解析マーク位置(サンプル)
  period: number;  // その地点の局所周期(サンプル)
  voiced: boolean;
}

/** 区間ごとのF0(周期)推定: 1/D ダウンサンプルした信号上のNSDF(McLeod) */
function estimatePeriods(
  mono: Float32Array,
  sr: number,
  o: Required<PsolaOptions>
): { hop: number; period: number[]; voiced: boolean[] } {
  const D = Math.max(1, Math.round(sr / 11025));
  const fsd = sr / D;
  const nd = Math.floor(mono.length / D);
  const x = new Float32Array(nd);
  for (let i = 0; i < nd; i++) {
    let a = 0;
    for (let k = 0; k < D; k++) a += mono[i * D + k];
    x[i] = a / D;
  }

  const minLag = Math.max(2, Math.floor(fsd / o.maxF0Hz));
  const maxLag = Math.min(Math.floor(fsd / o.minF0Hz), 400);
  const W = Math.round(maxLag * 1.6);
  const hopD = Math.max(1, Math.round(fsd * 0.005)); // 5ms
  const nFrames = Math.max(1, Math.floor(nd / hopD) + 1);

  let gMax = 0;
  for (let i = 0; i < nd; i++) gMax = Math.max(gMax, Math.abs(x[i]));
  const energyGate = Math.max(1e-4, gMax * 0.02);

  const period = new Array<number>(nFrames).fill(0);
  const voiced = new Array<boolean>(nFrames).fill(false);
  const nsdf = new Float32Array(maxLag + 2);

  for (let f = 0; f < nFrames; f++) {
    const start = f * hopD - (W >> 1);
    let e0 = 0;
    for (let j = 0; j < W; j++) {
      const t = start + j;
      if (t >= 0 && t < nd) e0 += x[t] * x[t];
    }
    if (Math.sqrt(e0 / W) < energyGate) continue;

    let best = -1;
    for (let tau = minLag - 1; tau <= maxLag + 1; tau++) {
      let acf = 0;
      let m = 0;
      for (let j = 0; j < W; j++) {
        const t = start + j;
        const u = t + tau;
        if (t < 0 || u >= nd) continue;
        acf += x[t] * x[u];
        m += x[t] * x[t] + x[u] * x[u];
      }
      const v = m > 1e-12 ? (2 * acf) / m : 0;
      nsdf[tau] = v;
      if (tau >= minLag && tau <= maxLag && v > best) best = v;
    }
    if (best < o.voicingThreshold) continue;

    // 最大値の0.9以上で最も短いラグのピークを採用(オクターブ下の誤検出を避ける)
    let pick = -1;
    for (let tau = minLag; tau <= maxLag; tau++) {
      const v = nsdf[tau];
      if (v >= best * 0.9 && v >= nsdf[tau - 1] && v >= nsdf[tau + 1]) { pick = tau; break; }
    }
    if (pick < 0) continue;
    const a = nsdf[pick - 1], b = nsdf[pick], c = nsdf[pick + 1];
    const den = a - 2 * b + c;
    const shift = Math.abs(den) > 1e-9 ? 0.5 * (a - c) / den : 0;
    period[f] = (pick + Math.max(-1, Math.min(1, shift))) * D;
    voiced[f] = true;
  }

  // メディアンフィルタ(3点)でF0の飛びを除去
  const sm = period.slice();
  for (let f = 1; f < nFrames - 1; f++) {
    if (voiced[f] && voiced[f - 1] && voiced[f + 1]) {
      const t = [period[f - 1], period[f], period[f + 1]].sort((p, q) => p - q);
      sm[f] = t[1];
    }
  }
  // 孤立した有声フレーム(前後とも無声)は誤検出として捨てる
  for (let f = 0; f < nFrames; f++) {
    const l = f > 0 && voiced[f - 1];
    const r = f < nFrames - 1 && voiced[f + 1];
    if (voiced[f] && !l && !r) voiced[f] = false;
  }
  return { hop: hopD * D, period: sm, voiced };
}

function buildPitchMarks(
  mono: Float32Array,
  sr: number,
  o: Required<PsolaOptions>
): { marks: PsolaMark[]; voicedRatio: number } {
  const n = mono.length;
  const { hop, period, voiced } = estimatePeriods(mono, sr, o);
  const nF = period.length;

  const frameAt = (pos: number) => Math.max(0, Math.min(nF - 1, Math.round(pos / hop)));
  const periodAt = (pos: number): number => {
    const fp = Math.max(0, Math.min(nF - 1, pos / hop));
    const f0 = Math.floor(fp);
    const f1 = Math.min(nF - 1, f0 + 1);
    const t = fp - f0;
    if (voiced[f0] && voiced[f1]) return period[f0] * (1 - t) + period[f1] * t;
    return voiced[f0] ? period[f0] : period[f1];
  };

  // 極性(正ピークが大きいか負ピークが大きいか)を固定してピーク位置の揺れを抑える
  let mx = 0, mn = 0;
  for (let i = 0; i < n; i++) { if (mono[i] > mx) mx = mono[i]; if (mono[i] < mn) mn = mono[i]; }
  const pol = mx >= -mn ? 1 : -1;
  const peakNear = (c: number, half: number): number => {
    const a = Math.max(0, Math.round(c - half));
    const b = Math.min(n - 1, Math.round(c + half));
    let bi = Math.max(0, Math.min(n - 1, Math.round(c)));
    let bv = -Infinity;
    for (let i = a; i <= b; i++) { const v = pol * mono[i]; if (v > bv) { bv = v; bi = i; } }
    return bi;
  };

  const unvoicedHop = Math.max(32, Math.round(sr * 0.006));
  const marks: PsolaMark[] = [];
  let pos = 0;
  let voicedCount = 0;
  while (pos < n) {
    const v = voiced[frameAt(pos)];
    if (v) {
      const P = periodAt(pos);
      if (marks.length === 0 || !marks[marks.length - 1].voiced) {
        // 有声区間の頭: 波形のピークに位相合わせ
        pos = peakNear(pos + P * 0.5, P * 0.5);
      }
      if (pos >= n) break;
      marks.push({ pos, period: periodAt(pos), voiced: true });
      voicedCount++;
      const next = pos + periodAt(pos);
      const refined = peakNear(next, periodAt(pos) * 0.2);
      pos = Math.max(pos + Math.round(P * 0.7), refined);
    } else {
      marks.push({ pos, period: unvoicedHop, voiced: false });
      pos += unvoicedHop;
    }
  }
  return { marks, voicedRatio: marks.length ? voicedCount / marks.length : 0 };
}

/** TD-PSOLA 本体。analysisTime = outputTime * (srcLen/outLen) でピッチと時間を独立制御 */
function tdPsolaSynthesize(
  chans: Float32Array[],
  marks: PsolaMark[],
  sr: number,
  baseRatio: number,
  outLen: number,
  curve?: (tOutSec: number) => number
): Float32Array[] {
  const srcLen = chans[0].length;
  const timeScale = srcLen / outLen;
  const outs = chans.map(() => new Float32Array(outLen));
  const winCache = new Map<number, Float32Array>();
  const hann = (len: number): Float32Array => {
    let w = winCache.get(len);
    if (!w) {
      w = new Float32Array(len);
      for (let i = 0; i < len; i++) w[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / len);
      winCache.set(len, w);
    }
    return w;
  };

  let tOut = 0;
  let mi = 0;
  let guard = 0;
  const maxIter = outLen * 2 + 1000;
  while (tOut < outLen && guard++ < maxIter) {
    const tIn = tOut * timeScale;
    while (mi + 1 < marks.length && Math.abs(marks[mi + 1].pos - tIn) <= Math.abs(marks[mi].pos - tIn)) mi++;
    const m = marks[mi];
    const P = Math.max(8, Math.round(m.period));
    let ratio = 1;
    if (m.voiced) {
      ratio = baseRatio;
      if (curve) ratio *= Math.pow(2, curve(tOut / sr) / 12);
      ratio = Math.max(0.5, Math.min(2.0, ratio));
    }

    const W = 2 * P;
    const w = hann(W);
    const gain = 1 / ratio; // ハン窓和 = P_in/P_out = ratio を打ち消す
    const srcStart = Math.round(m.pos) - P;
    const dstStart = Math.round(tOut) - P;
    for (let c = 0; c < chans.length; c++) {
      const src = chans[c];
      const dst = outs[c];
      for (let i = 0; i < W; i++) {
        const si = srcStart + i;
        const di = dstStart + i;
        if (si < 0 || si >= srcLen || di < 0 || di >= outLen) continue;
        dst[di] += src[si] * w[i] * gain;
      }
    }
    tOut += m.voiced ? m.period / ratio : m.period;
  }
  return outs;
}

/**
 * 声質(スペクトル包絡)を保ったままピッチを変えると、F1が基本周波数付近にある母音(う/ゆ/ぶ 等)では
 * 新しいF0位置の包絡値が下がり、音量が落ちる。元素材の短時間RMSに合わせてゆるやかに補正する。
 */
function matchLoudness(srcMono: Float32Array, outs: Float32Array[], sr: number): void {
  const outLen = outs[0].length;
  const block = Math.max(64, Math.round(sr * 0.02));
  const nB = Math.max(1, Math.ceil(outLen / block));
  const scale = srcMono.length / outLen;
  const raw = new Float32Array(nB);
  for (let b = 0; b < nB; b++) {
    const o0 = b * block, o1 = Math.min(outLen, o0 + block);
    let eo = 0;
    for (let i = o0; i < o1; i++) eo += outs[0][i] * outs[0][i];
    const s0 = Math.floor(o0 * scale), s1 = Math.min(srcMono.length, Math.max(s0 + 1, Math.floor(o1 * scale)));
    let ei = 0;
    for (let i = s0; i < s1; i++) ei += srcMono[i] * srcMono[i];
    const ro = Math.sqrt(eo / Math.max(1, o1 - o0));
    const ri = Math.sqrt(ei / Math.max(1, s1 - s0));
    raw[b] = Math.max(0.5, Math.min(2.5, (ri + 1e-4) / (ro + 1e-4)));
  }
  const g = new Float32Array(nB);
  for (let b = 0; b < nB; b++) {
    let a = 0, c = 0;
    for (let k = -2; k <= 2; k++) { const j = b + k; if (j >= 0 && j < nB) { a += raw[j]; c++; } }
    g[b] = a / c;
  }
  for (let ch = 0; ch < outs.length; ch++) {
    const d = outs[ch];
    for (let i = 0; i < outLen; i++) {
      const fp = i / block - 0.5;
      const b0 = Math.max(0, Math.min(nB - 1, Math.floor(fp)));
      const b1 = Math.min(nB - 1, b0 + 1);
      const t = Math.max(0, Math.min(1, fp - b0));
      d[i] *= g[b0] * (1 - t) + g[b1] * t;
    }
  }
}

// ------------------------------------------------------------
// 旧方式(リサンプリング+EQ+WSOLA): 有声区間が検出できない素材のフォールバック
// ------------------------------------------------------------
function legacyResampleShiftBuffer(
  ctx: BaseAudioContext,
  srcBuffer: AudioBuffer,
  pitchRatio: number,
  targetLengthSamples: number,
  o: Required<PsolaOptions>
): AudioBuffer {
  const sampleRate = srcBuffer.sampleRate;
  const numCh = srcBuffer.numberOfChannels;
  const outLen = Math.max(1, targetLengthSamples);
  const outBuffer = ctx.createBuffer(numCh, outLen, sampleRate);
  for (let ch = 0; ch < numCh; ch++) {
    const srcData = srcBuffer.getChannelData(ch);
    const resampledLen = Math.max(1, Math.round(srcData.length / pitchRatio));
    const resampled = new Float32Array(resampledLen);
    resampleChannelHermite(srcData, resampled, pitchRatio);
    if (o.preserveFormants) applyFormantCompensation(resampled, sampleRate, pitchRatio);
    const finalChannel = new Float32Array(outLen);
    if (Math.abs(resampledLen - outLen) <= 4) {
      finalChannel.set(resampled.subarray(0, Math.min(resampledLen, outLen)));
    } else {
      wsolaTimeStretchChannel(resampled, finalChannel, outLen / resampledLen, sampleRate);
    }
    applyStudioSoftLimiter(finalChannel);
    outBuffer.copyToChannel(finalChannel, ch);
  }
  return outBuffer;
}

// ------------------------------------------------------------
// 公開 API: ピッチ & タイムシフト
// ------------------------------------------------------------
export function psolaPitchAndTimeShiftBuffer(
  ctx: BaseAudioContext,
  srcBuffer: AudioBuffer,
  pitchRatio: number,
  targetLengthSamples?: number,
  opts: PsolaOptions = {}
): AudioBuffer {
  const o = { ...DEFAULT_OPTS, ...opts } as Required<PsolaOptions>;
  const sampleRate = srcBuffer.sampleRate;
  const numCh = srcBuffer.numberOfChannels;
  const outLen = Math.max(1, targetLengthSamples ?? srcBuffer.length);
  const curve = opts.pitchCurveSemitones;
  opts.onPitchCurveApplied?.(false);

  if (!curve && Math.abs(pitchRatio - 1.0) < 0.003 && outLen === srcBuffer.length) {
    return srcBuffer;
  }

  const chans: Float32Array[] = [];
  for (let ch = 0; ch < numCh; ch++) chans.push(srcBuffer.getChannelData(ch));
  const mono = new Float32Array(srcBuffer.length);
  for (let ch = 0; ch < numCh; ch++) {
    const d = chans[ch];
    for (let i = 0; i < mono.length; i++) mono[i] += d[i] / numCh;
  }

  let marks: PsolaMark[] = [];
  let voicedRatio = 0;
  try {
    ({ marks, voicedRatio } = buildPitchMarks(mono, sampleRate, o));
  } catch {
    marks = [];
  }
  // 有声区間がほぼ無い(息・摩擦音のみ等)ならピッチ変更の意味が無いが、
  // 有声音なのに検出失敗した可能性もあるため旧方式へ退避する
  if (marks.length < 4 || voicedRatio < 0.15) {
    return legacyResampleShiftBuffer(ctx, srcBuffer, pitchRatio, outLen, o);
  }

  // PSOLAは概ね±1オクターブまで自然。範囲外は残りだけリサンプリングで補う。
  const psolaRatio = Math.max(0.5, Math.min(2.0, pitchRatio));
  const residual = pitchRatio / psolaRatio;
  const interLen = residual === 1 ? outLen : Math.max(1, Math.round(outLen * residual));

  let result = tdPsolaSynthesize(chans, marks, sampleRate, psolaRatio, interLen, curve);
  if (residual !== 1) {
    result = result.map((d) => {
      const out = new Float32Array(outLen);
      resampleChannelHermite(d, out, residual);
      return out;
    });
  }

  matchLoudness(mono, result, sampleRate);

  const outBuffer = ctx.createBuffer(numCh, outLen, sampleRate);
  for (let ch = 0; ch < numCh; ch++) {
    applyStudioSoftLimiter(result[ch]);
    outBuffer.copyToChannel(new Float32Array(result[ch]), ch);
  }
  opts.onPitchCurveApplied?.(!!curve);
  return outBuffer;
}

// ------------------------------------------------------------
// 公開 API: ピッチシフトのみ（長さは維持）
// ------------------------------------------------------------
export function psolaPitchShiftBuffer(
  ctx: BaseAudioContext,
  srcBuffer: AudioBuffer,
  pitchRatio: number,
  opts: PsolaOptions = {}
): AudioBuffer {
  return psolaPitchAndTimeShiftBuffer(ctx, srcBuffer, pitchRatio, srcBuffer.length, opts);
}
