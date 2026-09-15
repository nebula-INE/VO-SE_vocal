/**
 * 高音質スタジオ・ボーカル明瞭化 DSPプロセッサー (Studio Vocal Clarity & Presence Engine)
 * 
 * 【音をハッキリと明瞭にする7段シグナルチェーン】
 * 1. 【Sub-bass Cut】70Hz HPF (Q=0.707): 超低域のモタつき・風圧ノイズをカット
 * 2. 【De-Mud (こもり解消)】320Hz Peaking (-3.5dB, Q=1.2): 
 *    モコモコした箱鳴り・濁りを除去し、声の輪郭をタイトに引き締め
 * 3. 【Vocal Core (声の芯・存在感)】2.8kHz Peaking (+3.5dB, Q=1.1):
 *    声のフォルマントとアタック感を強力に前へ押し出し、ハッキリとした芯を確立
 * 4. 【Articulation (子音・滑舌のキレ)】4.8kHz Peaking (+3.0dB, Q=1.2):
 *    サ行・タ行・カ行など子音の立ち上がりを鮮明にし、歌詞の聞き取りやすさを劇的向上
 * 5. 【Vocal Air (エアー・抜け感)】10.0kHz High-Shelf (+2.0dB, Q=0.707):
 *    こもり感を完全に払拭し、スタジオレコーディングのような透き通った抜け感を付加
 * 6. 【Clean High Cut】14.5kHz LPF (Q=0.707):
 *    可聴域の抜けと明るさは100%保持したまま、超高域の不要なヒスノイズのみをスマートにカット
 * 7. 【Smart Zero-Noise Gate】休符・ノート間の微小な背景息ノイズフロアを完全無音化
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
  private sDeMud = new BiquadStage();
  private sCore = new BiquadStage();
  private sArtic = new BiquadStage();
  private sAir = new BiquadStage();
  private sLpf = new BiquadStage();

  process(
    s: number,
    hpf: BiquadCoeffs,
    deMud: BiquadCoeffs,
    core: BiquadCoeffs,
    artic: BiquadCoeffs,
    air: BiquadCoeffs,
    lpf: BiquadCoeffs
  ): number {
    // 1. HPF (70Hz: 低域の不要なもたつきをカット)
    let y = this.sHpf.process(s, hpf);

    // 2. De-Mud (320Hz, -3.5dB: 箱鳴り・こもり感を解消し、クリアな抜けを確保)
    y = this.sDeMud.process(y, deMud);

    // 3. Vocal Core (2800Hz, +3.5dB: 声の芯とアタック感を強力に前へ)
    y = this.sCore.process(y, core);

    // 4. Articulation (4800Hz, +3.0dB: 子音・滑舌のキレを強調)
    y = this.sArtic.process(y, artic);

    // 5. Vocal Air (10000Hz, +2.0dB High-Shelf: 透き通った明るさとエアー感)
    y = this.sAir.process(y, air);

    // 6. LPF (14500Hz: 超高周波ヒスのみをカットし、ボーカル本来の輝きはフルに保つ)
    y = this.sLpf.process(y, lpf);

    return y;
  }
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

  // 音をハッキリ・クリアにするスタジオEQ設計
  const hpf = makeBiquad('hpf', 70, sampleRate, 0.707);
  const deMud = makeBiquad('peaking', 320, sampleRate, 1.2, -3.5); // こもり除去
  const core = makeBiquad('peaking', 2800, sampleRate, 1.1, 3.5);  // 声の芯・存在感
  const artic = makeBiquad('peaking', 4800, sampleRate, 1.2, 3.0); // 子音・滑舌
  const air = makeBiquad('highshelf', 10000, sampleRate, 0.707, 2.0); // 抜け・エアー感
  const lpf = makeBiquad('lpf', 14500, sampleRate, 0.707);         // 超高域ノイズカット

  const channels: VocalClarityChannel[] = [];
  for (let c = 0; c < numChannels; c++) {
    channels.push(new VocalClarityChannel());
  }

  // 1. インプレース IIR フィルタリング
  const inv32768 = 1.0 / 32768.0;
  for (let i = 0; i < numSamples; i++) {
    const ch = i % numChannels;
    const sNorm = pcm[i] * inv32768;
    const processed = channels[ch].process(sNorm, hpf, deMud, core, artic, air, lpf);
    const clamped = Math.max(-1.0, Math.min(1.0, processed));
    pcm[i] = Math.round(clamped * 32767.0);
  }

  // 2. ブリージング（吐息ノイズ）ゼロの超平滑ボーカルソフトゲート
  // ブロック境界で矩形波カットせず、時定数（アタック2ms、リリース12ms）で
  // 滑らかにゲインを減衰させることで、息継ぎや休符の頭・末尾で「スッ」「フッ」という
  // 突発的な吐息ノイズ（Breathing/Pumping）が発生する現象を完全に解消します。
  const attackAlpha = 1.0 - Math.exp(-1.0 / (sampleRate * 0.002));
  const releaseAlpha = 1.0 - Math.exp(-1.0 / (sampleRate * 0.012));
  const noiseFloorPcm = 180; // 約 -45dB
  let smoothGain = 1.0;

  for (let i = 0; i < numSamples; i += numChannels) {
    let maxAmp = 0;
    for (let c = 0; c < numChannels; c++) {
      const a = Math.abs(pcm[i + c]);
      if (a > maxAmp) maxAmp = a;
    }

    const targetGain = maxAmp < noiseFloorPcm ? 0.0 : 1.0;
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
