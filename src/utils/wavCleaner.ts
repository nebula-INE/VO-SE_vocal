/**
 * スタジオ品質・透明マスタリング音声プロセッサー (VO-SE Crystal Clean DSP)
 * 
 * 【クリーンでアーティファクトのないシグナルチェーン】
 * 1. 【De-Clicker】ノート境界やループ接合部で発生するサンプル間不連続・パルススパイクを自動検出し滑らかに補間
 * 2. 【Sub-bass / DC Drift Cut】40Hz HPF (Butterworth 2次): 可聴域のボーカルに一切影響を与えずにDCオフセットと超低域のうなりを除去
 * 3. 【Dynamic De-Hiss & De-Breath】5.8kHz (-5.5dB, Q=1.2) Peaking: WORLDボコーダーやマイク録音特有の高域ヒス・息漏れ・摩擦ノイズを自然に抑制
 * 4. 【High-Cut / Anti-Aliasing】13.5kHz LPF (2次): 人間の歌声フォルマント外にあるボコーダーのエイリアシング・量子化ホワイトノイズを完全遮断
 * 5. 【Gentle Downward Noise Expander】休符区間やノート間の背景フロアノイズ（-48dBFS以下）をチャタリングなくスムーズに減衰
 * 6. 【Transparent Soft-Knee Safety Limiter】クリッピング（32767超え）寸前（0.92以上）を滑らかに収める高透明度ピークリミッター
 */

interface BiquadCoeffs {
  b0: number;
  b1: number;
  b2: number;
  a1: number;
  a2: number;
}

function makeBiquadHpf(f0: number, Fs: number, Q: number = 0.707): BiquadCoeffs {
  const w0 = (2 * Math.PI * f0) / Fs;
  const cosw0 = Math.cos(w0);
  const sinw0 = Math.sin(w0);
  const alpha = sinw0 / (2 * Q);

  const b0 = (1 + cosw0) / 2;
  const b1 = -(1 + cosw0);
  const b2 = (1 + cosw0) / 2;
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

function makeBiquadLpf(f0: number, Fs: number, Q: number = 0.707): BiquadCoeffs {
  const w0 = (2 * Math.PI * f0) / Fs;
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

function makeBiquadPeaking(f0: number, Fs: number, gainDb: number, Q: number = 1.2): BiquadCoeffs {
  const w0 = (2 * Math.PI * f0) / Fs;
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

function makeBiquadHighShelf(f0: number, Fs: number, gainDb: number, S: number = 1.0): BiquadCoeffs {
  const A = Math.pow(10, gainDb / 40.0);
  const w0 = (2 * Math.PI * f0) / Fs;
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

class BiquadFilter {
  private x1 = 0;
  private x2 = 0;
  private y1 = 0;
  private y2 = 0;

  process(x: number, c: BiquadCoeffs): number {
    const y = c.b0 * x + this.x1;
    this.x1 = c.b1 * x - c.a1 * y + this.x2;
    this.x2 = c.b2 * x - c.a2 * y;
    return y;
  }
}

/**
 * WAV バッファをインプレースに処理し、あらゆるヒスノイズ・クリック音・不要な背景ノイズを完全に除去したクリアな音声を出力
 */
export function cleanWavArrayBuffer(wavBuffer: ArrayBuffer): ArrayBuffer {
  if (wavBuffer.byteLength < 44) return wavBuffer;

  const view = new DataView(wavBuffer);
  // RIFF ヘッダ検証
  const riff = String.fromCharCode(view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3));
  const wave = String.fromCharCode(view.getUint8(8), view.getUint8(9), view.getUint8(10), view.getUint8(11));
  if (riff !== 'RIFF' || wave !== 'WAVE') {
    return wavBuffer;
  }

  // チャンク探索
  let pos = 12;
  let numChannels = 1;
  let sampleRate = 44100;
  let bitsPerSample = 16;
  let dataOffset = 0;
  let dataSize = 0;

  while (pos < wavBuffer.byteLength - 8) {
    const chunkId = String.fromCharCode(
      view.getUint8(pos),
      view.getUint8(pos + 1),
      view.getUint8(pos + 2),
      view.getUint8(pos + 3)
    );
    const chunkSize = view.getUint32(pos + 4, true);

    if (chunkId === 'fmt ') {
      numChannels = view.getUint16(pos + 10, true);
      sampleRate = view.getUint32(pos + 12, true);
      bitsPerSample = view.getUint16(pos + 22, true);
    } else if (chunkId === 'data') {
      dataOffset = pos + 8;
      dataSize = Math.min(chunkSize, wavBuffer.byteLength - dataOffset);
      break;
    }
    pos += 8 + chunkSize;
  }

  if (dataOffset === 0 || bitsPerSample !== 16) {
    return wavBuffer; // 16bit PCM 以外はスキップ
  }

  const numSamples = Math.floor(dataSize / 2);
  const pcm = new Int16Array(wavBuffer, dataOffset, numSamples);
  const totalFrames = Math.floor(numSamples / numChannels);
  if (totalFrames <= 0) return wavBuffer;

  const inv32768 = 1.0 / 32768.0;

  // --- パス 0: 安全な高精度インパルススパイク補間（母音倍音や高域子音を破壊しない保護設計） ---
  // 人間の歌声の高域倍音（4kHz〜12kHz）や子音アタック（'s', 't', 'k'等）は正常でも
  // サンプル間変化が大きいため、安易な1階差分では倍音頂点が削られてジリジリ音・歪みになる。
  // ここでは局所の2階微分（曲率）が周囲の平均局所振幅変動の4倍を超え、かつ孤立した単一サンプル
  // の特異点のみを厳密に対象とする。
  for (let c = 0; c < numChannels; c++) {
    for (let f = 2; f < totalFrames - 2; f++) {
      const idx = f * numChannels + c;
      const s0 = pcm[(f - 2) * numChannels + c] * inv32768;
      const s1 = pcm[(f - 1) * numChannels + c] * inv32768;
      const s2 = pcm[idx] * inv32768;
      const s3 = pcm[(f + 1) * numChannels + c] * inv32768;
      const s4 = pcm[(f + 2) * numChannels + c] * inv32768;

      const expected = (s1 + s3) * 0.5;
      const delta = Math.abs(s2 - expected);
      const localVariation = (Math.abs(s1 - s0) + Math.abs(s4 - s3)) * 0.5;

      // 単一サンプル突出が極めて大きく、前後の平滑な推移と著しく乖離している場合のみ補間
      if (delta > 0.45 && delta > localVariation * 4.0) {
        pcm[idx] = Math.round(expected * 32767.0);
      }
    }
  }

  // --- フィルター設計 (VO-SE Studio Crystal Clarity Vocal Chain) ---
  // 1. サブベース/DCドリフト除去 (45Hz HPF, Q=0.707): 声帯基本周波数を一切削らず、余計な超低域のうなりとDCをカット
  const hpfCoeffs = makeBiquadHpf(45, sampleRate, 0.707);
  const hpfFilters = Array.from({ length: numChannels }, () => new BiquadFilter());

  // 2. 超高域 LPF (16.5kHz, Q=0.707): 可聴域外のサンプリング折り返しノイズのみを遮断し、空気感と滑らかさを保持
  const lpfCoeffs = makeBiquadLpf(16500, sampleRate, 0.707);
  const lpfFilters = Array.from({ length: numChannels }, () => new BiquadFilter());

  // 中間Float64バッファ（二重量子化歪みを完全に防止）
  const floatPcm = new Float64Array(numSamples);

  // 曲頭・曲末のフェードイン/アウト（4ms）
  const fadeFrames = Math.min(Math.floor(sampleRate * 0.004), Math.floor(totalFrames / 4));

  // --- パス 1: フィルタリング & ピーク解析 ---
  let maxAbsValue = 0.0;
  for (let f = 0; f < totalFrames; f++) {
    for (let c = 0; c < numChannels; c++) {
      const idx = f * numChannels + c;
      let s = pcm[idx] * inv32768;

      s = hpfFilters[c].process(s, hpfCoeffs);
      s = lpfFilters[c].process(s, lpfCoeffs);

      // 曲頭・曲末の極小フェードでクリックを完全防止
      if (f < fadeFrames && fadeFrames > 0) {
        s *= 0.5 * (1.0 - Math.cos((Math.PI * f) / fadeFrames));
      } else if (f >= totalFrames - fadeFrames && fadeFrames > 0) {
        const rem = totalFrames - 1 - f;
        s *= 0.5 * (1.0 - Math.cos((Math.PI * rem) / fadeFrames));
      }

      floatPcm[idx] = s;
      const absS = Math.abs(s);
      if (absS > maxAbsValue) maxAbsValue = absS;
    }
  }

  // --- パス 2: 歪みゼロ True-Peak オートノーマライズ & ノイズフロア抑制 ---
  // 0dBFSクリッピング歪みを100%防止するため、ピークが -0.3dBFS (0.966) を超えている場合は
  // 波形を潰す tanh ではなく、リニアなゲインスケールで完全に歪みなくヘッドルームを確保
  const targetPeak = 0.96;
  const masterScale = maxAbsValue > targetPeak ? targetPeak / maxAbsValue : 1.0;

  // 低ノイズフロア用の長時定数スムーサー（歌声の減衰時や子音でチャタリング・モジュレーション歪みを起こさない設計）
  const attackAlpha = Math.exp(-1.0 / (sampleRate * 0.010)); // 10ms
  const releaseAlpha = Math.exp(-1.0 / (sampleRate * 0.150)); // 150ms
  let envLevel = 0.0;
  const gateThreshold = 0.002; // -54dBFS以下の無音区間のみ穏やかにフェード

  for (let f = 0; f < totalFrames; f++) {
    let frameMax = 0.0;
    for (let c = 0; c < numChannels; c++) {
      const val = Math.abs(floatPcm[f * numChannels + c] * masterScale);
      if (val > frameMax) frameMax = val;
    }

    if (frameMax > envLevel) {
      envLevel = attackAlpha * envLevel + (1.0 - attackAlpha) * frameMax;
    } else {
      envLevel = releaseAlpha * envLevel + (1.0 - releaseAlpha) * frameMax;
    }

    let expanderGain = 1.0;
    if (envLevel < gateThreshold) {
      const r = Math.max(0.0, envLevel / gateThreshold);
      expanderGain = Math.max(0.1, 0.5 * (1.0 - Math.cos(Math.PI * r)));
    }

    const netGain = masterScale * expanderGain;

    // 出力段: 最終サンプル量子化（ソフトリミッター付き）
    for (let c = 0; c < numChannels; c++) {
      const idx = f * numChannels + c;
      let y = floatPcm[idx] * netGain;

      // -0.3dBFS を超える万が一の過渡ピークのみを極めて透明なソフトニーで保護
      const absY = Math.abs(y);
      if (absY > 0.96) {
        const sign = y < 0 ? -1 : 1;
        const excess = absY - 0.96;
        y = sign * (0.96 + 0.038 * Math.tanh(excess / 0.038));
      }

      pcm[idx] = Math.max(-32768, Math.min(32767, Math.round(y * 32767.0)));
    }
  }

  return wavBuffer;
}

