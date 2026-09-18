/**
 * スタジオ品質・透明マスタリング音声プロセッサー
 * 
 * 【クリーンでアーティファクトのないシグナルチェーン】
 * 1. 【Sub-bass / DC Drift Cut】35Hz HPF (Q=0.707): 可聴域のボーカルに一切影響を与えずにDCオフセットと超低域のうなりを除去
 * 2. 【De-Click Micro-Fade】曲頭・曲末の5msコサイン・マイクロフェードによる再生開始・終了時のクリック音防止
 * 3. 【Transparent Safety Limiter】クリッピング（32767超え）寸前（0.95以上）のみ穏やかに抑える高透明度ピークリミッター
 * ※ チャタリングや音の途切れ・息のブツ切りを引き起こす「ノイズゲート」や、中域を飽和させる「過剰な歪み・サチュレーション」は完全撤廃。
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

class BiquadFilter {
  private x1 = 0;
  private x2 = 0;

  process(x: number, c: BiquadCoeffs): number {
    const y = c.b0 * x + this.x1;
    this.x1 = c.b1 * x - c.a1 * y + this.x2;
    this.x2 = c.b2 * x - c.a2 * y;
    return y;
  }
}

/**
 * WAV バッファをインプレースに処理し、余分なノイズゲートや過剰歪みを排した純粋でクリアな音声を出力
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

  // 1. サブベース/DCドリフト除去 (35Hz HPF, Q=0.707)
  const hpfCoeffs = makeBiquadHpf(35, sampleRate, 0.707);
  const hpfFilters = Array.from({ length: numChannels }, () => new BiquadFilter());

  // 曲頭・曲末のデクリック・フェード（5ms）
  const fadeFrames = Math.min(Math.floor(sampleRate * 0.005), Math.floor(totalFrames / 4));

  const inv32768 = 1.0 / 32768.0;

  for (let f = 0; f < totalFrames; f++) {
    // 曲頭フェードイン (5ms)
    let edgeGain = 1.0;
    if (f < fadeFrames && fadeFrames > 0) {
      edgeGain = 0.5 * (1.0 - Math.cos((Math.PI * f) / fadeFrames));
    } else if (f >= totalFrames - fadeFrames && fadeFrames > 0) {
      // 曲末フェードアウト (5ms)
      const rem = totalFrames - 1 - f;
      edgeGain = 0.5 * (1.0 - Math.cos((Math.PI * rem) / fadeFrames));
    }

    for (let c = 0; c < numChannels; c++) {
      const idx = f * numChannels + c;
      const sNorm = pcm[idx] * inv32768;

      // HPF処理
      let y = hpfFilters[c].process(sNorm, hpfCoeffs);

      // 端点フェード適用
      y *= edgeGain;

      // 0.95以上の極端なピークのみ透明にソフトリミッティング（クリッピング防止）
      const absY = Math.abs(y);
      if (absY > 0.95) {
        const sign = y < 0 ? -1 : 1;
        const excess = absY - 0.95;
        y = sign * (0.95 + 0.05 * Math.tanh(excess / 0.05));
      }

      pcm[idx] = Math.max(-32768, Math.min(32767, Math.round(y * 32767.0)));
    }
  }

  return wavBuffer;
}
