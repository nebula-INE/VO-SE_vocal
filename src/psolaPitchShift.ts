// psolaPitchShift.ts
//
// 簡易 TD-PSOLA (Time-Domain Pitch-Synchronous OverLap-Add) による
// フォルマント保持ピッチシフト。
//
// これまでの wasmEngine.ts は AudioBufferSourceNode.playbackRate だけで
// ピッチを作っていたため、音高を上げるほどフォルマント(声道共鳴=声質)まで
// 一緒に引き伸ばされ「別人の声」に聞こえていた。
//
// この実装は:
//  1. 元サンプルの基本周期(ピッチ周期)を自己相関で推定
//  2. その周期を単位に「グレイン」(2周期分・Hann窓)を切り出す
//  3. グレインの中身(=フォルマント/声質)は一切変えずに、
//     グレインを配置する間隔だけを目標ピッチ比に合わせて詰め直す(OLA)
// ことで、ピッチだけを変え声質を保持する。
//
// [修正] 実機レンダリングで大量のノイズバーストが出た問題への対応:
//   1. 無声区間(s/k/t等の摩擦音・破裂音のような、周期性の無いノイズ的な
//      音)にまでピッチ同期処理をかけると、ノイズを無理やり「周期波形」
//      として扱うことになり、ブンブンという耳障りなバズ音になる。
//      → 自己相関のスコア(周期性の強さ)が閾値未満の区間は「無声」と
//        判定し、その区間だけはグレイン処理をせず単純な線形補間コピー
//        (時間伸縮のみ、ピッチは変えない)に切り替える。
//   2. オーバーラップ加算の正規化(out[i]/weight[i])が、グレインの
//      重なりが薄い場所(特にピッチを下げて周期間隔が広がった時)で
//      weightがほぼ0に近づき、除算で異常増幅してスパイクを作っていた。
//      → (a) グレイン長を「解析周期」と「合成周期(伸縮後の間隔)」の
//            大きい方に合わせて、間隔が広がってもグレイン同士が
//            必ず重なるようにした(隙間そのものを無くす)
//        (b) それでも重なりが薄い場所は、割る数に下限(MIN_WEIGHT)を
//            設けて異常増幅を防止した
//
// オフライン(OfflineAudioContext)でのバウンス処理を前提にしており、
// リアルタイム制約は無いためグレイン単位のループ処理で十分実用速度。

export interface PsolaOptions {
  /** 探索する基本周波数の下限(Hz)。低い声/低音ノート向けに広めに */
  minF0Hz?: number;
  /** 探索する基本周波数の上限(Hz) */
  maxF0Hz?: number;
  /** 基本周期を再推定する間隔(ms)。ピッチの時間変化(ビブラート等)に追従するため */
  reanalysisIntervalMs?: number;
  /**
   * 有声/無声の判定閾値(正規化自己相関スコア、0〜1)。
   * これ未満は「周期性なし=無声」とみなしPSOLAを適用しない。
   */
  voicingThreshold?: number;
}

const DEFAULT_OPTS: Required<PsolaOptions> = {
  minF0Hz: 70,
  maxF0Hz: 800,
  reanalysisIntervalMs: 80,
  voicingThreshold: 0.35,
};

// オーバーラップ加算の正規化で割る数の下限。
// これより薄い重なりの場所は「異常増幅」を避けるため、この値で割る
// (=音量が少し下がるだけで済み、スパイクにはならない)。
const MIN_NORMALIZE_WEIGHT = 0.3;

// [デクリック] 局所的な振幅の急変(前後数十サンプルの平均振幅に対して
// 異常に大きいサンプル間ジャンプが連続する区間)を検出し、その区間だけを
// 直前/直後の波形から線形補間で置き換える。声質・音量そのものには影響を
// 与えず、PSOLAの周期境界や有声/無声切り替え点でごく短時間(数十サンプル
// 程度)だけ生じうる破綻を除去するための後段の安全網。
function declickBuffer(out: Float32Array, sampleRate: number): void {
  const n = out.length;
  if (n < 8) return;

  const localWindow = Math.max(8, Math.round(sampleRate * 0.003)); // ~3ms
  const jumpThresholdFactor = 6; // 局所平均振幅の何倍のジャンプを異常とみなすか
  const minAbsJump = 0.02; // 無音に近い区間で過検出しないための下限(フルスケール比)
  const maxBurstSamples = Math.round(sampleRate * 0.003); // 1バーストの最大幅(~3ms)
  const mergeGapSamples = Math.round(sampleRate * 0.0005); // 近接した異常点は1つのバーストにまとめる

  // 局所平均振幅(簡易移動平均)を計算
  const localAbsAvg = new Float32Array(n);
  let runningSum = 0;
  const half = Math.floor(localWindow / 2);
  for (let i = 0; i < n; i++) {
    runningSum += Math.abs(out[i]);
    if (i >= localWindow) runningSum -= Math.abs(out[i - localWindow]);
    const count = Math.min(i + 1, localWindow);
    localAbsAvg[Math.max(0, i - half)] = runningSum / count;
  }

  // 異常なサンプル間ジャンプを検出
  const flagged = new Uint8Array(n);
  for (let i = 1; i < n; i++) {
    const jump = Math.abs(out[i] - out[i - 1]);
    const threshold = Math.max(minAbsJump, localAbsAvg[i] * jumpThresholdFactor);
    if (jump > threshold) {
      flagged[i - 1] = 1;
      flagged[i] = 1;
    }
  }

  // 連続した異常点を1つのバーストにまとめ、短い区間だけ線形補間で置き換える。
  // 幅が広すぎる場合(maxBurstSamples超)は誤検出の可能性が高いので触らない
  // (本当に大きな正当な音量変化を誤って潰さないための安全策)。
  let i = 0;
  while (i < n) {
    if (!flagged[i]) { i++; continue; }
    let j = i;
    while (j < n && (flagged[j] || (j + mergeGapSamples < n && flagged.slice(j, j + mergeGapSamples).some((v) => v)))) {
      j++;
    }
    const burstStart = Math.max(0, i - 1);
    const burstEnd = Math.min(n - 1, j);
    const burstLen = burstEnd - burstStart;

    if (burstLen > 0 && burstLen <= maxBurstSamples) {
      const a = out[burstStart];
      const b = out[burstEnd];
      for (let k = burstStart + 1; k < burstEnd; k++) {
        const t = (k - burstStart) / burstLen;
        out[k] = a + (b - a) * t;
      }
    }
    i = j + 1;
  }
}

function hannWindow(length: number): Float32Array {
  const w = new Float32Array(length);
  if (length <= 1) {
    w.fill(1);
    return w;
  }
  for (let i = 0; i < length; i++) {
    w[i] = 0.5 - 0.5 * Math.cos((2 * Math.PI * i) / (length - 1));
  }
  return w;
}

interface PeriodEstimate {
  period: number;
  /** 正規化自己相関のピークスコア(0〜1程度)。周期性の強さの目安。 */
  score: number;
}

/**
 * 正規化自己相関による基本周期(サンプル数)推定。
 * 無声区間やノイズ区間ではスコアが低くなるため、呼び出し側で
 * voicingThresholdと比較して有声/無声を判定する。
 */
function estimatePeriod(
  data: Float32Array,
  sampleRate: number,
  centerSample: number,
  windowSamples: number,
  minF0: number,
  maxF0: number,
  fallbackPeriod: number
): PeriodEstimate {
  const minPeriod = Math.max(2, Math.floor(sampleRate / maxF0));
  const maxPeriod = Math.max(minPeriod + 1, Math.floor(sampleRate / minF0));

  const start = Math.max(0, centerSample - Math.floor(windowSamples / 2));
  const end = Math.min(data.length, start + windowSamples);
  const n = end - start;
  if (n < maxPeriod * 2) {
    return { period: fallbackPeriod, score: 0 };
  }

  let bestPeriod = fallbackPeriod;
  let bestScore = 0;

  for (let period = minPeriod; period <= maxPeriod; period++) {
    let sum = 0;
    let normA = 0;
    let normB = 0;
    const count = n - period;
    for (let i = 0; i < count; i++) {
      const a = data[start + i];
      const b = data[start + i + period];
      sum += a * b;
      normA += a * a;
      normB += b * b;
    }
    const denom = Math.sqrt(normA * normB) + 1e-9;
    const score = sum / denom;
    if (score > bestScore) {
      bestScore = score;
      bestPeriod = period;
    }
  }

  return { period: bestPeriod, score: bestScore };
}

/**
 * TD-PSOLAでピッチと出力長(再生に使う実時間)を同時に変更し、
 * フォルマント(声質)を保持した AudioBuffer を返す。
 * 無声(周期性の無い)区間は自動検出し、ピッチ同期処理を適用せず
 * 単純な時間伸縮(線形補間)のみを行う。
 *
 * @param ctx                AudioContext / OfflineAudioContext (createBuffer用)
 * @param srcBuffer          元サンプル(必要な範囲を事前に切り出しておく)
 * @param pitchRatio         目標ピッチ比 (例: 1オクターブ上なら 2.0, 半音なら 2^(1/12))
 * @param targetLengthSamples 出力の長さ(サンプル数)。省略時は入力と同じ長さ
 */
export function psolaPitchAndTimeShiftBuffer(
  ctx: BaseAudioContext,
  srcBuffer: AudioBuffer,
  pitchRatio: number,
  targetLengthSamples?: number,
  opts: PsolaOptions = {}
): AudioBuffer {
  const o = { ...DEFAULT_OPTS, ...opts };
  const sampleRate = srcBuffer.sampleRate;
  const outLen = targetLengthSamples ?? srcBuffer.length;

  // ピッチ比がほぼ1、かつ長さもほぼ同じならPSOLA処理自体をスキップ
  if (Math.abs(pitchRatio - 1) < 0.003 && outLen === srcBuffer.length) {
    return srcBuffer;
  }

  const numCh = srcBuffer.numberOfChannels;
  const outBuffer = ctx.createBuffer(numCh, outLen, sampleRate);
  // 解析マークは入力の再生速度(=時間伸縮比)に合わせて進める。
  // timeRatio > 1 なら「元の音より長く伸ばす」= 解析マークは出力より遅く進む。
  const timeRatio = outLen / srcBuffer.length;

  const reanalysisHop = Math.max(
    64,
    Math.floor((o.reanalysisIntervalMs / 1000) * sampleRate)
  );
  const minPeriod = Math.max(2, Math.floor(sampleRate / o.maxF0Hz));
  const defaultPeriod = Math.floor(sampleRate / 220); // 見つからなければA3付近を仮定

  for (let ch = 0; ch < numCh; ch++) {
    const src = srcBuffer.getChannelData(ch);
    const out = new Float32Array(outLen);
    const weight = new Float32Array(outLen);

    let period = defaultPeriod;
    let isVoiced = false;
    let lastReanalysisMark = -Infinity;
    let synthMark = 0;

    while (synthMark < outLen) {
      // 出力上の位置(synthMark)を、時間伸縮比(timeRatio)を使って元波形上の
      // 対応位置(analysisMark)に写像する。timeRatio=1なら従来通り同じ位置。
      const analysisMark = Math.min(
        src.length - 1,
        Math.round(synthMark / timeRatio)
      );

      if (analysisMark - lastReanalysisMark >= reanalysisHop || lastReanalysisMark < 0) {
        const est = estimatePeriod(
          src,
          sampleRate,
          analysisMark,
          reanalysisHop * 3,
          o.minF0Hz,
          o.maxF0Hz,
          period
        );
        period = est.period;
        isVoiced = est.score >= o.voicingThreshold;
        lastReanalysisMark = analysisMark;
      }

      // 合成マークの進み幅 = 元周期を「時間伸縮」と「ピッチ比」の両方で調整。
      const synthPeriod = Math.max(
        minPeriod,
        Math.round((period * timeRatio) / pitchRatio)
      );

      if (isVoiced) {
        // グレイン長は「解析周期」と「合成周期」の大きい方に合わせる。
        // こうしないと、ピッチを下げて間隔(synthPeriod)が広がった時に
        // 元の周期(period)基準の狭いグレインだけでは隙間ができてしまい、
        // 正規化(weightが薄い場所での除算)が異常増幅を起こす原因になる。
        const grainHalf = Math.max(period, synthPeriod);
        const grainLen = grainHalf * 2;
        const window = hannWindow(grainLen);
        const grainStart = analysisMark - grainHalf;

        for (let i = 0; i < grainLen; i++) {
          const srcIdx = grainStart + i;
          if (srcIdx < 0 || srcIdx >= src.length) continue;
          const outIdx = synthMark - grainHalf + i;
          if (outIdx < 0 || outIdx >= outLen) continue;
          const w = window[i];
          out[outIdx] += src[srcIdx] * w;
          weight[outIdx] += w;
        }
      } else {
        // 無声区間: 周期性が無いノイズ的な音にPSOLAをかけるとブンブンいう
        // バズ音になるため、ピッチ同期グレイン処理はせず、単純な時間伸縮
        // (線形補間コピー、ピッチは変えない)だけを行う。
        for (let i = 0; i < synthPeriod; i++) {
          const outIdx = synthMark + i;
          if (outIdx < 0 || outIdx >= outLen) continue;
          const srcPosF = (synthMark + i) / timeRatio;
          const i0 = Math.floor(srcPosF);
          const frac = srcPosF - i0;
          const s0 = i0 >= 0 && i0 < src.length ? src[i0] : 0;
          const s1 = i0 + 1 >= 0 && i0 + 1 < src.length ? src[i0 + 1] : 0;
          out[outIdx] += s0 + (s1 - s0) * frac;
          weight[outIdx] += 1;
        }
      }

      synthMark += synthPeriod;
    }

    // オーバーラップ加算の正規化(窓の重なりで音量が変動しないように)。
    // 重なりが薄い場所は下限(MIN_NORMALIZE_WEIGHT)で割ることで、
    // 異常増幅(スパイク/ノイズバースト)を防ぐ。
    for (let i = 0; i < outLen; i++) {
      if (weight[i] <= 0) continue;
      const divisor = Math.max(weight[i], MIN_NORMALIZE_WEIGHT);
      out[i] = out[i] / divisor;
    }

    // [デクリック] 子音→母音のような非周期→周期の切り替わり地点は、
    // たった1グレインだけ内容が周囲と整合しない「短時間の暴れ」が
    // 残ることがある(数十サンプル=1ms未満)。声質・音量には影響しない
    // ごく短い区間だけを検出し、前後の波形から線形補間で穴埋めする。
    declickBuffer(out, sampleRate);

    outBuffer.copyToChannel(out, ch);
  }

  return outBuffer;
}

/**
 * ピッチだけを変え、長さは入力と同じに保つ簡易版。
 * ノート単位のベースピッチは psolaPitchAndTimeShiftBuffer で焼き込み、
 * こちらはビブラート/ピッチベンドなど小さい揺れの補正に使う想定
 * (揺れ幅が小さければ formant のズレも知覚できないレベルに収まる)。
 */
export function psolaPitchShiftBuffer(
  ctx: BaseAudioContext,
  srcBuffer: AudioBuffer,
  pitchRatio: number,
  opts: PsolaOptions = {}
): AudioBuffer {
  return psolaPitchAndTimeShiftBuffer(
    ctx,
    srcBuffer,
    pitchRatio,
    srcBuffer.length,
    opts
  );
}
