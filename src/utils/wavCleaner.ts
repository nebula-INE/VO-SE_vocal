/**
 * 高音質スタジオ・ボーカルマスタリング DSPプロセッサー
 * 
 * 【ギザギザ・金属バズ音を解消し、温かみと滑らかさ・太さを実現するシグナルチェーン】
 * 1. 【Sub-bass Cut】75Hz HPF (Q=0.707): 超低域のモタつき・DCドリフトをカット
 * 2. 【Vocal Warmth / Body (温かみ・芯)】280Hz Peaking (+1.0dB, Q=1.0): 
 *    声の胴鳴りとチェスト共鳴を豊かに保ち、ペラペラで乾いた質感を解消
 * 3. 【De-Harsh / Anti-Metallic (金属音・トゲ除去)】3400Hz Peaking (-2.5dB, Q=1.4):
 *    合成音声特有の耳に刺さる金属的ピーク・ブザー音（ノコギリ波のトゲ）を穏やかに除去
 * 4. 【Natural Presence (滑舌・明瞭度)】5200Hz Peaking (+0.8dB, Q=1.2):
 *    子音の聞き取りやすさを自然にサポートしつつ、刺々しさは出さない適正ゲイン
 * 5. 【Silk Top (高域の滑らかさ)】11.0kHz High-Shelf (-1.5dB, Q=0.707):
 *    高周波のチリチリした粗さを抑え、スタジオ録音のシルキーで耳に優しいトップエンドを実現
 * 6. 【Clean High Cut】15.0kHz LPF (Q=0.707):
 *    不要な高周波エイリアシング・ヒスのみをカット
 * 7. 【Analog Soft Saturation】デジタル波形の尖った角を滑らかに丸めるソフトニー飽和
 * 8. 【Smart Envelope Gate】ゼロクロス歪みゼロの超平滑ボーカルゲート（50msホールド）
 */

interface BiquadCoeffs {
  b0: number;
  b1: number;
  b2: number;
  a1: number;
  a2: number;
}

function makeBiquad(
  type: 'lpf' | 'peaking' | 'hpf' | 'highshelf' | 'lowshelf',
  f0: number,
  Fs: number,
  Q: number,
  gainDb: number = 0
): BiquadCoeffs {
  const w0 = (2 * Math.PI * f0) / Fs;
  const cosw0 = Math.cos(w0);
  const sinw0 = Math.sin(w0);
  const alpha = sinw0 / (2 * Q);

  let b0 = 0, b1 = 0, b2 = 0, a0 = 1, a1 = 0, a2 = 0;

  if (type === 'lpf') {
    b0 = (1 - cosw0) / 2;
    b1 = 1 - cosw0;
    b2 = (1 - cosw0) / 2;
    a0 = 1 + alpha;
    a1 = -2 * cosw0;
    a2 = 1 - alpha;
  } else if (type === 'hpf') {
    b0 = (1 + cosw0) / 2;
    b1 = -(1 + cosw0);
    b2 = (1 + cosw0) / 2;
    a0 = 1 + alpha;
    a1 = -2 * cosw0;
    a2 = 1 - alpha;
  } else if (type === 'peaking') {
    const A = Math.pow(10, gainDb / 40);
    b0 = 1 + alpha * A;
    b1 = -2 * cosw0;
    b2 = 1 - alpha * A;
    a0 = 1 + alpha / A;
    a1 = -2 * cosw0;
    a2 = 1 - alpha / A;
  } else if (type === 'highshelf') {
    const A = Math.pow(10, gainDb / 40);
    const aShelf = (sinw0 / 2) * Math.sqrt((A + 1 / A) * (1 / Q - 1) + 2);
    const twoSqrtAAlpha = 2 * Math.sqrt(A) * aShelf;
    b0 = A * ((A + 1) + (A - 1) * cosw0 + twoSqrtAAlpha);
    b1 = -2 * A * ((A - 1) + (A + 1) * cosw0);
    b2 = A * ((A + 1) + (A - 1) * cosw0 - twoSqrtAAlpha);
    a0 = (A + 1) - (A - 1) * cosw0 + twoSqrtAAlpha;
    a1 = 2 * ((A - 1) - (A + 1) * cosw0);
    a2 = (A + 1) - (A - 1) * cosw0 - twoSqrtAAlpha;
  }

  return {
    b0: b0 / a0,
    b1: b1 / a0,
    b2: b2 / a0,
    a1: a1 / a0,
    a2: a2 / a0,
  };
}

class BiquadStage {
  private x1 = 0;
  private x2 = 0;

  process(x: number, c: BiquadCoeffs): number {
    const y = c.b0 * x + this.x1;
    this.x1 = c.b1 * x - c.a1 * y + this.x2;
    this.x2 = c.b2 * x - c.a2 * y;
    return y;
  }
}

class VocalClarityChannel {
  private sHpf = new BiquadStage();
  private sWarmth = new BiquadStage();
  private sDeHarsh = new BiquadStage();
  private sPresence = new BiquadStage();
  private sAir = new BiquadStage();
  private sLpf = new BiquadStage();

  process(
    s: number,
    hpf: BiquadCoeffs,
    warmth: BiquadCoeffs,
    deHarsh: BiquadCoeffs,
    presence: BiquadCoeffs,
    air: BiquadCoeffs,
    lpf: BiquadCoeffs
  ): number {
    // 1. HPF (75Hz: サブベースの超低域ゴロゴロ・DCドリフト除去)
    let y = this.sHpf.process(s, hpf);

    // 2. Vocal Warmth / Body (280Hz, +1.0dB: 胸声・胴鳴りの温かみを維持し、ペラペラ感を解消)
    y = this.sWarmth.process(y, warmth);

    // 3. De-Harsh (3400Hz, -2.5dB: 合成音声特有の金属的共鳴・ブザー音・トゲトゲしたピークを除去)
    y = this.sDeHarsh.process(y, deHarsh);

    // 4. Natural Presence (5200Hz, +0.8dB: 子音と発音の明瞭度を自然にサポート)
    y = this.sPresence.process(y, presence);

    // 5. Silk Top (11000Hz, -1.5dB High-Shelf: 刺々しい高域のチリチリ感を抑えシルキーに)
    y = this.sAir.process(y, air);

    // 6. LPF (15000Hz: 超高周波エイリアシング・ヒスノイズ除去)
    y = this.sLpf.process(y, lpf);

    return y;
  }
}

/**
 * デジタル合成音声の鋭利なインパルス・ノコギリ波ピークを滑らかに丸め、
 * アナログテープや真空管を通したような温かみと滑らかさを与えるソフトニー飽和
 */
function smoothWaveform(x: number): number {
  const absX = Math.abs(x);
  if (absX <= 0.55) return x; // 振幅が中程度以下は完全リニア（歪みゼロ・原音透明度維持）
  const sign = x < 0 ? -1 : 1;
  const excess = absX - 0.55;
  const compressed = 0.55 + 0.45 * Math.tanh(excess / 0.45);
  return sign * compressed;
}

/**
 * WAV バッファをインプレースに処理し、こもり感を一掃してハッキリとしたクリアな歌声を出力
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

  // 自然で温かみのあるボーカルサウンドに整えるスタジオEQ設計
  const hpf = makeBiquad('hpf', 75, sampleRate, 0.707);
  const warmth = makeBiquad('peaking', 280, sampleRate, 1.0, 1.0);     // 胴鳴り・温かみ (+1.0dB)
  const deHarsh = makeBiquad('peaking', 3400, sampleRate, 1.4, -2.5);  // 金属音・ギザギザトゲ除去 (-2.5dB)
  const presence = makeBiquad('peaking', 5200, sampleRate, 1.2, 0.8);  // 自然な発音明瞭度 (+0.8dB)
  const air = makeBiquad('highshelf', 11000, sampleRate, 0.707, -1.5); // 高域シルキートーン (-1.5dB)
  const lpf = makeBiquad('lpf', 15000, sampleRate, 0.707);            // 超高周波カット

  const channels: VocalClarityChannel[] = [];
  for (let c = 0; c < numChannels; c++) {
    channels.push(new VocalClarityChannel());
  }

  // 1. インプレース IIR フィルタリング & アナログ波形スムージング
  const inv32768 = 1.0 / 32768.0;
  for (let i = 0; i < numSamples; i++) {
    const ch = i % numChannels;
    const sNorm = pcm[i] * inv32768;
    const processed = channels[ch].process(sNorm, hpf, warmth, deHarsh, presence, air, lpf);
    const smoothed = smoothWaveform(processed);
    const clamped = Math.max(-1.0, Math.min(1.0, smoothed));
    pcm[i] = Math.round(clamped * 32767.0);
  }

  // 2. エンベロープ追従型・ゼロクロス歪みゼロの超平滑ボーカルソフトゲート
  // 単一サンプルの瞬時振幅ではなく、ピーク追従エンベロープとホールド時間（50ms）
  // を採用することで、波形のゼロ交差（ゼロクロス）時にゲインが勝手に閉じて
  // チクチク・プチプチした矩形波歪み（クロスオーバー歪み）が発生する現象を完全に防止します。
  const attackAlpha = 1.0 - Math.exp(-1.0 / (sampleRate * 0.004));
  const releaseAlpha = 1.0 - Math.exp(-1.0 / (sampleRate * 0.040));
  const envDecayAlpha = 1.0 - Math.exp(-1.0 / (sampleRate * 0.030));
  const holdSamples = Math.round(sampleRate * 0.050); // 50ms ホールド
  const noiseFloorPcm = 160; // 約 -46dB (休符の微小ノイズフロア)

  let envelope = 0.0;
  let holdCounter = 0;
  let smoothGain = 1.0;

  for (let i = 0; i < numSamples; i += numChannels) {
    let maxAmp = 0;
    for (let c = 0; c < numChannels; c++) {
      const a = Math.abs(pcm[i + c]);
      if (a > maxAmp) maxAmp = a;
    }

    // ピーク追従エンベロープ計算 (立ち上がりは即時、立下がりは緩やか)
    if (maxAmp > envelope) {
      envelope = maxAmp;
    } else {
      envelope += envDecayAlpha * (maxAmp - envelope);
    }

    // 有声判定とホールド制御
    if (envelope >= noiseFloorPcm) {
      holdCounter = holdSamples;
    } else if (holdCounter > 0) {
      holdCounter--;
    }

    const targetGain = holdCounter > 0 ? 1.0 : 0.0;
    const alpha = targetGain > smoothGain ? attackAlpha : releaseAlpha;
    smoothGain += alpha * (targetGain - smoothGain);

    if (smoothGain < 0.0005) {
      smoothGain = 0.0;
    }

    if (smoothGain <= 0.0) {
      for (let c = 0; c < numChannels; c++) {
        pcm[i + c] = 0;
      }
    } else if (smoothGain < 0.999) {
      for (let c = 0; c < numChannels; c++) {
        pcm[i + c] = Math.round(pcm[i + c] * smoothGain);
      }
    }
  }

  return wavBuffer;
}
