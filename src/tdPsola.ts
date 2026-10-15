// ============================================================
// tdPsola.ts
//
// フォルマントを保ったままピッチだけを変えるための軽量 TD-PSOLA。
//
// 背景:
//   AudioBufferSourceNode.playbackRate でピッチを変えると、スペクトル包絡
//   (=フォルマント=声色)も一緒に伸縮してしまい、UTAU音源の原音から
//   離れた高さ/低さのノートで別人の声に聞こえる。
//   PSOLA は「1周期ぶんの波形(グレイン)」を切り出して置き直す方式なので、
//   各グレインのスペクトル包絡がそのまま残り、声色が変わらない。
//
// 設計 (以前のPSOLAが固まった原因への対策):
//   - 重い基本周波数解析は「サンプル(WAV)ごとに1回だけ」行いキャッシュする
//     (analyzePitchTrack)。ノートごとにやるのは O(N) のマーク配置と
//     オーバーラップ加算だけ。
//   - 時間軸は変えない(長さ不変)。長さの調整はループ/切り出し側の責務。
//   - 無声区間(子音・息)はピッチ変更せずそのまま通す。
//
// このファイルは Web Audio に依存しない純粋な Float32Array 処理なので
// Node でも単体テストできる。
// ============================================================

export interface PitchTrack {
  sampleRate: number;
  /** フレーム間隔 (サンプル数) */
  hop: number;
  /** 各フレームの周期 (サンプル、小数)。0 = 無声 */
  periods: Float32Array;
}

export interface PitchTrackOptions {
  minF0?: number;
  maxF0?: number;
  /** 正規化自己相関がこの値未満なら無声とみなす */
  voicedThreshold?: number;
}

export interface PitchMarks {
  pos: Int32Array;
  voiced: Uint8Array;
  /** その位置での元波形の周期 (サンプル)。無声は unvoicedPeriod */
  period: Float32Array;
}

// ------------------------------------------------------------
// 1. 基本周波数(周期)トラックの解析 — サンプルごとに1回だけ呼ぶ
// ------------------------------------------------------------
export function analyzePitchTrack(
  x: Float32Array,
  sampleRate: number,
  opts: PitchTrackOptions = {}
): PitchTrack {
  const minF0 = opts.minF0 ?? 70;
  const maxF0 = opts.maxF0 ?? 900;
  const thr = opts.voicedThreshold ?? 0.55;

  const hop = Math.max(1, Math.round(sampleRate * 0.005));
  const nFrames = Math.floor(x.length / hop) + 1;
  const periods = new Float32Array(nFrames);

  // 粗探索用に ~11kHz へ間引き (箱型平均)
  const D = Math.max(1, Math.round(sampleRate / 11025));
  const srD = sampleRate / D;
  const nD = Math.floor(x.length / D);
  const xd = new Float32Array(nD);
  for (let i = 0; i < nD; i++) {
    let s = 0;
    for (let k = 0; k < D; k++) s += x[i * D + k];
    xd[i] = s / D;
  }

  const winD = Math.round(srD * 0.035);
  const minLagD = Math.max(2, Math.floor(srD / maxF0));
  const maxLagD = Math.ceil(srD / minF0);
  if (nD < winD + maxLagD + 2) {
    return { sampleRate, hop, periods }; // 短すぎる: 全フレーム無声
  }

  const nccf = new Float32Array(maxLagD + 2);

  for (let f = 0; f < nFrames; f++) {
    const cD = Math.round((f * hop) / D);
    let s = cD - (winD >> 1);
    s = Math.max(0, Math.min(nD - winD - maxLagD - 1, s));

    let e0 = 0;
    for (let i = 0; i < winD; i++) e0 += xd[s + i] * xd[s + i];
    if (e0 / winD < 1e-7) continue; // 無音

    let e1 = 0;
    for (let i = 0; i < winD; i++) e1 += xd[s + minLagD + i] * xd[s + minLagD + i];

    let maxR = -1;
    for (let lag = minLagD; lag <= maxLagD; lag++) {
      let num = 0;
      for (let i = 0; i < winD; i++) num += xd[s + i] * xd[s + i + lag];
      nccf[lag] = num / Math.sqrt(e0 * e1 + 1e-12);
      if (nccf[lag] > maxR) maxR = nccf[lag];
      // e1 をスライドさせる
      e1 += xd[s + lag + winD] * xd[s + lag + winD] - xd[s + lag] * xd[s + lag];
    }
    if (maxR < thr) continue;

    // オクターブ下(サブハーモニック)を避けるため、最大の0.88倍以上で最初の局所最大を採る
    let lagD = -1;
    for (let lag = minLagD + 1; lag < maxLagD; lag++) {
      if (nccf[lag] >= 0.88 * maxR && nccf[lag] >= nccf[lag - 1] && nccf[lag] >= nccf[lag + 1]) {
        lagD = lag;
        break;
      }
    }
    if (lagD < 0) continue;

    // 原サンプルレートで精密化 + 放物線補間
    const W = Math.round(sampleRate * 0.03);
    const c = f * hop;
    let s2 = c - (W >> 1);
    const center = lagD * D;
    const lo = Math.max(2, center - D - 1);
    const hi = center + D + 1;
    s2 = Math.max(0, Math.min(x.length - W - hi - 2, s2));
    if (s2 < 0) continue;

    let f0e = 0;
    for (let i = 0; i < W; i++) f0e += x[s2 + i] * x[s2 + i];
    let bestL = center;
    let bestR = -2;
    const rr: Record<number, number> = {};
    for (let l = lo - 1; l <= hi + 1; l++) {
      let num = 0;
      let e = 0;
      for (let i = 0; i < W; i++) {
        const v = x[s2 + i + l];
        num += x[s2 + i] * v;
        e += v * v;
      }
      const r = num / Math.sqrt(f0e * e + 1e-12);
      rr[l] = r;
      if (l >= lo && l <= hi && r > bestR) {
        bestR = r;
        bestL = l;
      }
    }
    let frac = 0;
    const a = rr[bestL - 1], b = rr[bestL], cR = rr[bestL + 1];
    if (a !== undefined && cR !== undefined) {
      const den = a - 2 * b + cR;
      if (Math.abs(den) > 1e-9) frac = Math.max(-0.5, Math.min(0.5, (0.5 * (a - cR)) / den));
    }
    periods[f] = bestL + frac;
  }

  // オクターブ飛び(2倍/半分)の外れ値を近傍中央値で補正
  const fixed = Float32Array.from(periods);
  for (let f = 0; f < nFrames; f++) {
    if (periods[f] <= 0) continue;
    const nb: number[] = [];
    for (let k = -3; k <= 3; k++) {
      const g = f + k;
      if (k !== 0 && g >= 0 && g < nFrames && periods[g] > 0) nb.push(periods[g]);
    }
    if (nb.length < 2) continue;
    nb.sort((p, q) => p - q);
    const med = nb[nb.length >> 1];
    const ratio = periods[f] / med;
    if (ratio > 1.6 || ratio < 0.625) fixed[f] = med;
  }

  return { sampleRate, hop, periods: fixed };
}

/** 元サンプル上の位置 srcIdx での周期(サンプル)。無声/範囲外は 0 */
export function periodAtSourceIndex(track: PitchTrack, srcIdx: number): number {
  const f = Math.round(srcIdx / track.hop);
  if (f < 0 || f >= track.periods.length) return 0;
  return track.periods[f];
}

// ------------------------------------------------------------
// 2. ピッチマーク配置 — ノートのセグメントごと (O(N))
// ------------------------------------------------------------
export function placeMarks(
  x: Float32Array,
  periodAt: (i: number) => number,
  unvoicedPeriod: number
): PitchMarks {
  const N = x.length;
  const pos: number[] = [];
  const voiced: number[] = [];
  const per: number[] = [];
  if (N < 4) {
    return { pos: new Int32Array(0), voiced: new Uint8Array(0), period: new Float32Array(0) };
  }

  // 軽いローパス (5tap) したものでピーク探索し、支配的な極性に揃える
  const xs = new Float32Array(N);
  for (let i = 0; i < N; i++) {
    const a = x[Math.max(0, i - 2)], b = x[Math.max(0, i - 1)], c = x[i];
    const d = x[Math.min(N - 1, i + 1)], e = x[Math.min(N - 1, i + 2)];
    xs[i] = (a + 2 * b + 3 * c + 2 * d + e) / 9;
  }
  let mx = 0, mn = 0;
  for (let i = 0; i < N; i++) {
    if (xs[i] > mx) mx = xs[i];
    if (xs[i] < mn) mn = xs[i];
  }
  const pol = mx >= -mn ? 1 : -1;

  const argmax = (lo: number, hi: number): number => {
    let best = lo, bv = -Infinity;
    for (let i = lo; i <= hi; i++) {
      const v = pol * xs[i];
      if (v > bv) { bv = v; best = i; }
    }
    return best;
  };

  const p0 = periodAt(0);
  let cur = p0 > 0 ? argmax(0, Math.min(N - 1, Math.ceil(p0))) : 0;
  pos.push(cur);
  voiced.push(p0 > 0 ? 1 : 0);
  per.push(p0 > 0 ? p0 : unvoicedPeriod);

  for (;;) {
    const p = periodAt(cur);
    let next: number;
    if (p > 0) {
      const expected = cur + p;
      const lo = Math.max(cur + 1, Math.round(expected - 0.3 * p));
      const hi = Math.min(N - 1, Math.round(expected + 0.3 * p));
      if (lo > hi) break;
      next = argmax(lo, hi);
    } else {
      next = cur + Math.max(2, Math.round(unvoicedPeriod));
      if (next >= N) break;
    }
    if (next <= cur) break;
    pos.push(next);
    voiced.push(p > 0 ? 1 : 0);
    per.push(p > 0 ? p : unvoicedPeriod);
    cur = next;
  }

  return { pos: Int32Array.from(pos), voiced: Uint8Array.from(voiced), period: Float32Array.from(per) };
}

// ------------------------------------------------------------
// 3. 合成 (オーバーラップ加算)
//    ratio: ピッチ倍率 (>1 で高く)。位置 t(サンプル) の関数でもよい。
//    時間軸は不変 (出力長 = 入力長)。
// ------------------------------------------------------------
export function psolaRender(
  chans: Float32Array[],
  marks: PitchMarks,
  ratio: number | ((outIdx: number) => number)
): Float32Array[] {
  const N = chans[0].length;
  const out = chans.map(() => new Float32Array(N));
  const M = marks.pos.length;
  if (M === 0) {
    chans.forEach((c, i) => out[i].set(c));
    return out;
  }
  const wsum = new Float32Array(N);
  const ratioAt = typeof ratio === 'function' ? ratio : () => ratio;

  let t = 0;
  let j = 0;
  while (t < N) {
    while (j + 1 < M && Math.abs(marks.pos[j + 1] - t) <= Math.abs(marks.pos[j] - t)) j++;
    const m = marks.pos[j];
    const p = marks.period[j];
    const half = Math.max(2, Math.round(p));
    const ti = Math.round(t);
    for (let k = -half; k <= half; k++) {
      const src = m + k;
      const dst = ti + k;
      if (src < 0 || src >= N || dst < 0 || dst >= N) continue;
      const w = 0.5 * (1 + Math.cos((Math.PI * k) / (half + 1)));
      wsum[dst] += w;
      for (let c = 0; c < chans.length; c++) out[c][dst] += chans[c][src] * w;
    }
    const r = marks.voiced[j] ? Math.max(0.2, Math.min(5, ratioAt(ti))) : 1;
    t += p / r;
  }

  for (let i = 0; i < N; i++) {
    const w = wsum[i];
    if (w > 1e-3) {
      const inv = 1 / w;
      for (let c = 0; c < out.length; c++) out[c][i] *= inv;
    }
  }
  return out;
}
