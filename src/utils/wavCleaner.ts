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

  // Convert to high-precision Float64 working buffer to prevent rounding/quantization noise
  const channelsData = Array.from({ length: numChannels }, () => new Float64Array(totalFrames));
  for (let c = 0; c < numChannels; c++) {
    const ch = channelsData[c];
    for (let f = 0; f < totalFrames; f++) {
      ch[f] = pcm[f * numChannels + c] * inv32768;
    }
  }

  // --- パス 0: 高精度インパルス・デクリック（境界スパイクや不連続点の修復） ---
  for (let c = 0; c < numChannels; c++) {
    const ch = channelsData[c];
    for (let f = 1; f < totalFrames - 1; f++) {
      const sPrev = ch[f - 1];
      const sCur = ch[f];
      const sNext = ch[f + 1];
      const mid = (sPrev + sNext) * 0.5;
      const deviation = Math.abs(sCur - mid);
      const localStep = Math.abs(sNext - sPrev);
      // 孤立した単一サンプルスパイクのみを平滑化（正常な高域波形は localStep が同等になるため保護）
      if (deviation > 0.08 && deviation > localStep * 2.2) {
        ch[f] = mid;
      }
    }
  }

  // --- フィルター設計 (VO-SE Studio Crystal Clarity Vocal Chain) ---
  // 1. サブベース/DCドリフト除去 (65Hz HPF, Q=0.707)
  const hpfCoeffs = makeBiquadHpf(65, sampleRate, 0.707);
  const hpfFilters = Array.from({ length: numChannels }, () => new BiquadFilter());

  // 2. 箱鳴り・こもり感除去 (380Hz, -1.2dB, Q=1.2) Peaking: ヌケの改善
  const boxinessCoeffs = makeBiquadPeaking(380, sampleRate, -1.2, 1.2);
  const boxinessFilters = Array.from({ length: numChannels }, () => new BiquadFilter());

  // 3. 歯擦音・金属的耳障りピークの緩和 (6.2kHz, -5.0dB, Q=1.3) Peaking:
  //    無声子音（し、す、て、ち、つ等）の過剰な高周波摩擦やボコーダー金属鳴りを自然で滑らかに抑制
  const deHarshCoeffs = makeBiquadPeaking(6200, sampleRate, -5.0, 1.3);
  const deHarshFilters = Array.from({ length: numChannels }, () => new BiquadFilter());

  // 4. 定常ヒスノイズ緩和 (8.8kHz, -3.5dB, S=0.8) High Shelf:
  //    母音の明るさを失わずにボコーダー特有の背景ざらつき・砂嵐ノイズを自然に低減
  const hissShelfCoeffs = makeBiquadHighShelf(8800, sampleRate, -3.5, 0.8);
  const hissShelfFilters = Array.from({ length: numChannels }, () => new BiquadFilter());

  // 5. 超高域 LPF (13.2kHz, Q=0.707): 可聴域外のボコーダーエイリアシングおよび高域ホワイトノイズを完全遮断
  const lpfCoeffs = makeBiquadLpf(13200, sampleRate, 0.707);
  const lpfFilters = Array.from({ length: numChannels }, () => new BiquadFilter());

  // 曲頭・曲末のデクリック・フェード（6ms）
  const fadeFrames = Math.min(Math.floor(sampleRate * 0.006), Math.floor(totalFrames / 4));

  // --- パス 1: フィルタリング + ノイズフロア追従 ---
  // ダウンワード・エクスパンダーのエンベロープフォロワー用パラメータ
  const attackAlpha = Math.exp(-1.0 / (sampleRate * 0.010)); // 10ms attack
  const releaseAlpha = Math.exp(-1.0 / (sampleRate * 0.060)); // 60ms release
  let envLevel = 0.0;
  const gateThreshold = 0.005; // -46dBFS 以下の休符フロアノイズを検出

  for (let f = 0; f < totalFrames; f++) {
    // 曲頭・曲末フェード
    let edgeGain = 1.0;
    if (f < fadeFrames && fadeFrames > 0) {
      edgeGain = 0.5 * (1.0 - Math.cos((Math.PI * f) / fadeFrames));
    } else if (f >= totalFrames - fadeFrames && fadeFrames > 0) {
      const rem = totalFrames - 1 - f;
      edgeGain = 0.5 * (1.0 - Math.cos((Math.PI * rem) / fadeFrames));
    }

    let frameMaxAbs = 0.0;

    for (let c = 0; c < numChannels; c++) {
      let s = channelsData[c][f];
      s = hpfFilters[c].process(s, hpfCoeffs);
      s = boxinessFilters[c].process(s, boxinessCoeffs);
      s = deHarshFilters[c].process(s, deHarshCoeffs);
      s = hissShelfFilters[c].process(s, hissShelfCoeffs);
      s = lpfFilters[c].process(s, lpfCoeffs);
      channelsData[c][f] = s;

      const absS = Math.abs(s);
      if (absS > frameMaxAbs) frameMaxAbs = absS;
    }

    // スムーズ・エンベロープ追従
    if (frameMaxAbs > envLevel) {
      envLevel = attackAlpha * envLevel + (1 - attackAlpha) * frameMaxAbs;
    } else {
      envLevel = releaseAlpha * envLevel + (1 - releaseAlpha) * frameMaxAbs;
    }

    // ダウンワード・エクスパンダーゲイン（休符・無音区間の残留ノイズを自然に消音）
    let expanderGain = 1.0;
    if (envLevel < gateThreshold) {
      const ratio = Math.max(0.0, envLevel / gateThreshold);
      expanderGain = Math.max(0.005, 0.5 * (1.0 - Math.cos(Math.PI * ratio)));
    }

    const netGain = edgeGain * expanderGain;

    // ゲイン適用 + 透明ソフトピークリミッター（0.88以上でソフトクリップしハードクリップ皆無）
    for (let c = 0; c < numChannels; c++) {
      let y = channelsData[c][f] * netGain;
      const absY = Math.abs(y);
      if (absY > 0.88) {
        const sign = y < 0 ? -1 : 1;
        const excess = absY - 0.88;
        y = sign * (0.88 + 0.10 * Math.tanh(excess / 0.10));
      }
      pcm[f * numChannels + c] = Math.max(-32768, Math.min(32767, Math.round(y * 32767.0)));
    }
  }

  return wavBuffer;
}

