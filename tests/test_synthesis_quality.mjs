import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { registerHooks } from 'node:module';
import { cleanWavArrayBuffer } from '../assets/src/utils/wavCleaner.ts';
import {
  generateUtauPortamentoPoints,
  smoothPitchBendPoints,
  parsePitchBend,
  serializePitchBend,
  hasExplicitUstPortamento,
  applyAutoTuneToNotes,
  sampleSemitoneAt,
  appendNextNotePreBitePoints,
} from '../assets/src/utils/pitchCurve.ts';

registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === './pitchCurve' && context.parentURL?.includes('formatConverter.ts')) {
      return nextResolve('./pitchCurve.ts', context);
    }
    return nextResolve(specifier, context);
  },
});

const SR = 44100;
const FRAME_MS = 5.0;

class FormantResonator {
  constructor(f, bw, g, fs) {
    this.gain = g;
    const r = Math.exp((-Math.PI * bw) / fs);
    const theta = (2 * Math.PI * f) / fs;
    this.a1 = 2 * r * Math.cos(theta);
    this.a2 = -r * r;
    this.b0 = (1 - r * r) * Math.sin(theta);
    this.y1 = 0;
    this.y2 = 0;
  }
  process(x) {
    const y0 = this.b0 * x + this.a1 * this.y1 + this.a2 * this.y2;
    this.y2 = this.y1;
    this.y1 = y0;
    return y0 * this.gain;
  }
}

function makeSeededRand(seed) {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return (s / 4294967296) * 2 - 1;
  };
}

function makeVocalPcm({
  lyric = 'あ',
  durationSec = 1.2,
  baseHz = 261.6256,
  consonantDur = 0,
  consonantKind = 'none',
  humanAir = 0.35,
  seed = 1,
}) {
  const N = Math.floor(durationSec * SR);
  const vMap = {
    a: [
      { f: 780, bw: 80, g: 1.0 },
      { f: 1250, bw: 95, g: 0.75 },
      { f: 2650, bw: 120, g: 0.45 },
      { f: 3700, bw: 170, g: 0.28 },
      { f: 4600, bw: 240, g: 0.15 },
    ],
    i: [
      { f: 270, bw: 45, g: 1.0 },
      { f: 2350, bw: 90, g: 0.7 },
      { f: 3050, bw: 120, g: 0.4 },
      { f: 3850, bw: 170, g: 0.25 },
      { f: 4700, bw: 240, g: 0.15 },
    ],
    u: [
      { f: 340, bw: 55, g: 1.0 },
      { f: 1200, bw: 80, g: 0.6 },
      { f: 2450, bw: 110, g: 0.35 },
      { f: 3650, bw: 160, g: 0.22 },
      { f: 4600, bw: 240, g: 0.14 },
    ],
    e: [
      { f: 490, bw: 60, g: 1.0 },
      { f: 1920, bw: 85, g: 0.68 },
      { f: 2650, bw: 115, g: 0.42 },
      { f: 3750, bw: 165, g: 0.25 },
      { f: 4700, bw: 240, g: 0.15 },
    ],
    o: [
      { f: 460, bw: 60, g: 1.0 },
      { f: 850, bw: 75, g: 0.7 },
      { f: 2550, bw: 110, g: 0.38 },
      { f: 3600, bw: 160, g: 0.22 },
      { f: 4600, bw: 240, g: 0.14 },
    ],
  };
  const vKey =
    lyric === 'い'
      ? 'i'
      : lyric === 'う'
        ? 'u'
        : lyric === 'え'
          ? 'e'
          : lyric === 'お'
            ? 'o'
            : 'a';
  const res = vMap[vKey].map((f) => new FormantResonator(f.f, f.bw, f.g, SR));
  const fricRes = new FormantResonator(6200, 1800, 0.45, SR);
  const burstRes = new FormantResonator(2600, 900, 0.5, SR);
  const airRes = new FormantResonator(6500, 3500, 0.35, SR);
  const rand = makeSeededRand(seed);
  const out = new Float32Array(N);
  const cSamp = Math.floor(consonantDur * SR);
  let ph = 0;
  let dcX1 = 0;
  let dcY1 = 0;

  for (let i = 0; i < N; i++) {
    const t = i / SR;
    const curF =
      baseHz *
      (1 +
        0.002 *
          Math.min(1, Math.max(0, (t - 0.15) / 0.25)) *
          Math.sin(2 * Math.PI * 5.2 * t));
    ph += curF / SR;
    if (ph >= 1) ph -= 1;
    const gl =
      ph < 0.6
        ? (Math.PI / 1.2) * Math.sin((Math.PI * ph) / 0.6)
        : ph < 0.85
          ? -(Math.PI / 0.5) * Math.sin((Math.PI * (ph - 0.6)) / 0.25)
          : 0;
    const air = humanAir > 0 ? airRes.process(rand()) * humanAir : 0;
    let s = 0;
    for (const r of res) s += r.process(gl + air * 0.4);
    s += air;
    const yDc = s - dcX1 + 0.995 * dcY1;
    dcX1 = s;
    dcY1 = yDc;
    s = yDc;

    if (i < cSamp) {
      const p = i / cSamp;
      if (consonantKind === 'fric_s') {
        s =
          s * p +
          fricRes.process(rand() * 0.7 + gl * 0.2) *
            Math.sin((1 - p) * Math.PI * 0.5) *
            0.45;
      } else if (consonantKind === 'stop_k') {
        s =
          p < 0.35
            ? s * 0.05
            : s * Math.min(1, (p - 0.35) * 2.5) +
              burstRes.process(rand() * 0.5 + gl * 0.5) *
                Math.exp(-(p - 0.35) * 12) *
                0.35;
      }
    }
    let env = 1;
    if (i < 661) env = 0.5 * (1 - Math.cos((Math.PI * i) / 661));
    else if (i > N - 1543)
      env = 0.5 * (1 + Math.cos((Math.PI * (i - (N - 1543))) / 1543));
    out[i] = s * env;
  }

  let mx = 1e-6;
  for (let i = 0; i < N; i++) mx = Math.max(mx, Math.abs(out[i]));
  for (let i = 0; i < N; i++) out[i] *= 0.75 / mx;
  return out;
}

function buildWebPitchCurve(midi, prevMidi, durMs, frameCount) {
  const baseHz = 440 * Math.pow(2, (midi - 69) / 12);
  let pts = [];
  if (prevMidi !== null && prevMidi !== midi) {
    pts = smoothPitchBendPoints(
      generateUtauPortamentoPoints(midi, prevMidi, durMs, {
        portamentoMs: 105,
        vibratoDepthSemitones: 0,
      })
    );
  } else if (prevMidi === null && durMs >= 160) {
    pts = smoothPitchBendPoints(
      generateUtauPortamentoPoints(midi, null, durMs, {
        vibratoDepthSemitones: 0,
      })
    );
  }

  const c = new Array(frameCount);
  for (let i = 0; i < frameCount; i++) {
    const tMs = (i / Math.max(1, frameCount - 1)) * durMs;
    let semi = 0;
    if (pts.length > 0) {
      if (tMs <= pts[0].offsetMs) semi = pts[0].semitone;
      else if (tMs >= pts[pts.length - 1].offsetMs)
        semi = pts[pts.length - 1].semitone;
      else {
        for (let k = 0; k < pts.length - 1; k++) {
          if (tMs >= pts[k].offsetMs && tMs <= pts[k + 1].offsetMs) {
            const r =
              pts[k + 1].offsetMs > pts[k].offsetMs
                ? (tMs - pts[k].offsetMs) /
                  (pts[k + 1].offsetMs - pts[k].offsetMs)
                : 0;
            semi =
              pts[k].semitone + (pts[k + 1].semitone - pts[k].semitone) * r;
            break;
          }
        }
      }
    }
    c[i] = baseHz * Math.pow(2, semi / 12);
  }
  if (c.length >= 5) {
    const sm = c.slice();
    sm[1] = 0.25 * c[0] + 0.5 * c[1] + 0.25 * c[2];
    for (let i = 2; i < c.length - 2; i++) {
      sm[i] =
        0.06 * c[i - 2] +
        0.24 * c[i - 1] +
        0.4 * c[i] +
        0.24 * c[i + 1] +
        0.06 * c[i + 2];
    }
    sm[c.length - 2] =
      0.25 * c[c.length - 3] +
      0.5 * c[c.length - 2] +
      0.25 * c[c.length - 1];
    return sm;
  }
  return c;
}

function analyzeWav(wavBuf) {
  const view = new DataView(wavBuf);
  const sr = view.getUint32(24, true);
  let pos = 12;
  let dOff = 0;
  let dSz = 0;
  while (pos < wavBuf.byteLength - 8) {
    const id = String.fromCharCode(
      view.getUint8(pos),
      view.getUint8(pos + 1),
      view.getUint8(pos + 2),
      view.getUint8(pos + 3)
    );
    const sz = view.getUint32(pos + 4, true);
    if (id === 'data') {
      dOff = pos + 8;
      dSz = sz;
      break;
    }
    pos += 8 + sz;
  }
  const pcm = new Int16Array(wavBuf, dOff, dSz / 2);
  const s = new Float32Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) s[i] = pcm[i] / 32768;

  const diffs = new Float32Array(Math.max(0, s.length - 1));
  let sum = 0;
  let sq = 0;
  for (let i = 0; i < diffs.length; i++) {
    const d = Math.abs(s[i + 1] - s[i]);
    diffs[i] = d;
    sum += d;
    sq += d * d;
  }
  const mean = sum / Math.max(1, diffs.length);
  const std = Math.sqrt(Math.max(0, sq / Math.max(1, diffs.length) - mean * mean));
  const thresh = mean + 12 * std;
  const jumps = [];
  for (let i = 0; i < diffs.length; i++) {
    if (diffs[i] > thresh) jumps.push(i);
  }

  const win = Math.round(0.05 * sr);
  const clusters = [];
  let l = 0;
  for (let r = 0; r < jumps.length; r++) {
    while (jumps[r] - jumps[l] > win) l++;
    if (r - l + 1 >= 2) {
      if (!clusters.length || jumps[l] > clusters[clusters.length - 1].e) {
        clusters.push({ s: jumps[l], e: jumps[r] });
      } else {
        clusters[clusters.length - 1].e = Math.max(
          clusters[clusters.length - 1].e,
          jumps[r]
        );
      }
    }
  }

  const hop = Math.round(0.005 * sr);
  const silences = [];
  let inSil = false;
  let ss = 0;
  for (let f = 0; f + hop <= s.length; f += hop) {
    let q = 0;
    for (let k = 0; k < hop; k++) q += s[f + k] * s[f + k];
    const quiet = Math.sqrt(q / hop) < 0.0005;
    if (!inSil && quiet) {
      inSil = true;
      ss = f;
    } else if (inSil && !quiet) {
      inSil = false;
      const durMs = ((f - ss) / sr) * 1000;
      if (durMs >= 35) {
        let onsetPeak = 0;
        for (let k = f; k < Math.min(s.length, f + hop); k++) {
          onsetPeak = Math.max(onsetPeak, Math.abs(s[k]));
        }
        silences.push({ startSec: ss / sr, durMs, onsetPeak });
      }
    }
  }

  function yin(frame) {
    const N = frame.length;
    const tMin = Math.floor(sr / 550);
    const tMax = Math.ceil(sr / 150);
    const d = new Float64Array(tMax + 2);
    const c = new Float64Array(tMax + 2);
    for (let t = 1; t <= tMax + 1; t++) {
      let acc = 0;
      for (let j = 0; j < N - tMax - 2; j++) {
        const df = frame[j] - frame[j + t];
        acc += df * df;
      }
      d[t] = acc;
    }
    c[0] = 1;
    let rs = 0;
    for (let t = 1; t <= tMax + 1; t++) {
      rs += d[t];
      c[t] = rs > 0 ? (d[t] * t) / rs : 1;
    }
    let bt = -1;
    for (let t = tMin; t <= tMax; t++) {
      if (c[t] < 0.15) {
        while (t + 1 <= tMax && c[t + 1] < c[t]) t++;
        bt = t;
        break;
      }
    }
    if (bt < 0) return 0;
    if (bt > 1 && bt < tMax + 1) {
      const y0 = c[bt - 1];
      const y1 = c[bt];
      const y2 = c[bt + 1];
      const den = 2 * (2 * y1 - y2 - y0);
      bt +=
        Math.abs(den) > 1e-12
          ? Math.max(-0.5, Math.min(0.5, (y0 - y2) / den))
          : 0;
    }
    return sr / bt;
  }

  const f0Contour = [];
  for (let p = 0; p + 1024 <= s.length; p += Math.round(0.01 * sr)) {
    let q = 0;
    for (let k = 0; k < 1024; k++) q += s[p + k] * s[p + k];
    const f0 =
      Math.sqrt(q / 1024) < 0.015 ? 0 : yin(s.subarray(p, p + 1024));
    f0Contour.push({ t: p / sr, f0 });
  }
  let steps = 0;
  let flat = 0;
  for (let i = 1; i < f0Contour.length; i++) {
    const a = f0Contour[i - 1].f0;
    const b = f0Contour[i].f0;
    if (a > 60 && b > 60) {
      steps++;
      if (Math.abs(1200 * Math.log2(b / a)) < 2.0) flat++;
    }
  }

  // Sustained vibrato check on final note (7.68..8.18s, midi 67 = 392Hz)
  const sustF0 = f0Contour
    .filter((p) => p.t >= 7.68 && p.t <= 8.18 && p.f0 > 60)
    .map((p) => 1200 * Math.log2(p.f0 / 391.9954))
    .filter((c) => Math.abs(c) < 200);
  let vibRateHz = 0;
  let vibDepthCents = 0;
  if (sustF0.length >= 20) {
    const meanC = sustF0.reduce((a, b) => a + b, 0) / sustF0.length;
    const detrend = sustF0.map((v) => v - meanC);
    const stdC = Math.sqrt(
      detrend.reduce((a, b) => a + b * b, 0) / detrend.length
    );
    vibDepthCents = stdC * Math.SQRT2;
    let bestPwr = 0;
    for (let hz = 3.0; hz <= 8.5; hz += 0.2) {
      let re = 0;
      let im = 0;
      for (let k = 0; k < detrend.length; k++) {
        const ang = 2 * Math.PI * hz * (k * 0.01);
        re += detrend[k] * Math.cos(ang);
        im -= detrend[k] * Math.sin(ang);
      }
      const pwr = re * re + im * im;
      if (pwr > bestPwr) {
        bestPwr = pwr;
        vibRateHz = hz;
      }
    }
  }

  function fft(re, im) {
    const n = re.length;
    for (let i = 1, j = 0; i < n; i++) {
      let b = n >> 1;
      for (; j & b; b >>= 1) j ^= b;
      j ^= b;
      if (i < j) {
        [re[i], re[j]] = [re[j], re[i]];
        [im[i], im[j]] = [im[j], im[i]];
      }
    }
    for (let len = 2; len <= n; len <<= 1) {
      const a = (-2 * Math.PI) / len;
      const w0r = Math.cos(a);
      const w0i = Math.sin(a);
      for (let i = 0; i < n; i += len) {
        let wr = 1;
        let wi = 0;
        for (let j = 0; j < len / 2; j++) {
          const ur = re[i + j];
          const ui = im[i + j];
          const vr = re[i + j + len / 2] * wr - im[i + j + len / 2] * wi;
          const vi = re[i + j + len / 2] * wi + im[i + j + len / 2] * wr;
          re[i + j] = ur + vr;
          im[i + j] = ui + vi;
          re[i + j + len / 2] = ur - vr;
          im[i + j + len / 2] = ui - vi;
          const nwr = wr * w0r - wi * w0i;
          wi = wr * w0i + wi * w0r;
          wr = nwr;
        }
      }
    }
  }

  const N = 4096;
  const psd = new Float64Array(N / 2 + 1);
  const hann = new Float64Array(N);
  for (let i = 0; i < N; i++) {
    hann[i] = 0.5 * (1 - Math.cos((2 * Math.PI * i) / (N - 1)));
  }
  let nf = 0;
  const re = new Float64Array(N);
  const im = new Float64Array(N);
  for (let p = 0; p + N <= s.length; p += N / 2) {
    let q = 0;
    for (let i = 0; i < N; i++) q += s[p + i] * s[p + i];
    if (Math.sqrt(q / N) < 0.02) continue;
    for (let i = 0; i < N; i++) {
      re[i] = s[p + i] * hann[i];
      im[i] = 0;
    }
    fft(re, im);
    for (let k = 0; k <= N / 2; k++) psd[k] += re[k] * re[k] + im[k] * im[k];
    nf++;
  }
  const bdb = (lo, hi) => {
    let acc = 0;
    let c = 0;
    for (let k = 0; k <= N / 2; k++) {
      const f = (k * sr) / N;
      if (f >= lo && f <= hi) {
        acc += psd[k] / Math.max(1, nf);
        c++;
      }
    }
    return 10 * Math.log10(Math.max(1e-20, acc / Math.max(1, c)));
  };
  const b1_3 = bdb(1000, 3000);
  return {
    jumps: jumps.length,
    clusters: clusters.length,
    silences,
    flatPct: steps > 0 ? (flat / steps) * 100 : 100,
    vibDepthCents,
    vibRateHz,
    db4_8: bdb(4000, 8000) - b1_3,
    db8_12: bdb(8000, 12000) - b1_3,
    db16k: bdb(15500, 16500) - b1_3,
  };
}

test('Deployed WASM loader files are synchronized between public/ and assets/src/', () => {
  const pubJs = fs.readFileSync('public/wasm/vose_core.js');
  const srcJs = fs.readFileSync('assets/src/wasm/vose_core.js');
  const pubWasm = fs.readFileSync('public/wasm/vose_core.wasm');

  assert.equal(Buffer.compare(pubJs, srcJs), 0);
  assert.ok(pubWasm.byteLength > 100000);
});

test('Web pitch curve generates portamento, overshoot, phrase-entry scoop, and avoids CVVC double-vibrato', () => {
  const jumpPts = generateUtauPortamentoPoints(64, 60, 350, {
    portamentoMs: 105,
    vibratoDepthSemitones: 0,
  });
  const maxPositive = Math.max(...jumpPts.map((p) => p.semitone));
  assert.ok(maxPositive >= 0.08 && maxPositive <= 0.2, `unexpected overshoot peak ${maxPositive}`);
  assert.equal(jumpPts[jumpPts.length - 1].semitone, 0);

  const scoopPts = generateUtauPortamentoPoints(60, null, 450, {
    vibratoDepthSemitones: 0,
  });
  assert.equal(scoopPts[0].semitone, -0.3);
  assert.equal(scoopPts[scoopPts.length - 1].semitone, 0);

  const clientSrc = fs.readFileSync('assets/src/voseCoreClient.ts', 'utf8');
  assert.match(clientSrc, /vibratoDepthSemitones:\s*0/);
  assert.match(clientSrc, /const cvPrev = hasExplicitUstPortamento\(n\) \? effectivePrev : \{ noteNum \};/);
});

test('Compiled WASM vocal synthesis passes boundary continuity, F0 smoothness, and spectral tilt benchmarks', async () => {
  const wasmBuf = fs.readFileSync('public/wasm/vose_core.wasm');
  const nodeDesc = Object.getOwnPropertyDescriptor(process.versions, 'node');
  const hadWindow = 'window' in globalThis;
  const prevWindow = globalThis.window;

  let mod;
  try {
    delete process.versions.node;
    globalThis.window = {};
    const modFactory = (
      await import(pathToFileURL(path.resolve('assets/src/wasm/vose_core.js')).href)
    ).default;
    mod = await modFactory({
      instantiateWasm(info, recv) {
        WebAssembly.instantiate(wasmBuf, info).then((r) => recv(r.instance));
        return {};
      },
      print: () => {},
      printErr: () => {},
    });
  } finally {
    if (nodeDesc) Object.defineProperty(process.versions, 'node', nodeDesc);
    if (hadWindow) globalThis.window = prevWindow;
    else delete globalThis.window;
  }

  assert.equal(typeof mod._load_embedded_resource_f32, 'function');
  assert.equal(typeof mod._execute_render_cancelable, 'function');
  assert.equal(typeof mod._set_oto_data, 'function');
  assert.equal(typeof mod._get_engine_version, 'function');

  assert.equal(typeof mod._get_note_event_abi, 'function');
  assert.equal(typeof mod._get_oto_entry_abi, 'function');
  assert.equal(Number((mod._get_note_event_abi() >> 48n) & 0xffffn), 88);
  assert.equal(Number((mod._get_oto_entry_abi() >> 48n) & 0xffffn), 632);

  const defs = [
    { key: 'a', alias: 'あ', pcm: makeVocalPcm({ lyric: 'あ', seed: 101 }), oto: { offset: 20, consonant: 25, cutoff: 80, pre: 10, ovl: 5 } },
    { key: 'i', alias: 'い', pcm: makeVocalPcm({ lyric: 'い', seed: 102 }), oto: { offset: 20, consonant: 25, cutoff: 80, pre: 10, ovl: 5 } },
    { key: 'u', alias: 'う', pcm: makeVocalPcm({ lyric: 'う', seed: 103 }), oto: { offset: 20, consonant: 25, cutoff: 80, pre: 10, ovl: 5 } },
    { key: 'e', alias: 'え', pcm: makeVocalPcm({ lyric: 'え', seed: 104 }), oto: { offset: 20, consonant: 25, cutoff: 80, pre: 10, ovl: 5 } },
    { key: 'o', alias: 'お', pcm: makeVocalPcm({ lyric: 'お', seed: 105 }), oto: { offset: 20, consonant: 25, cutoff: 80, pre: 10, ovl: 5 } },
    { key: 'ka', alias: 'か', pcm: makeVocalPcm({ lyric: 'か', consonantDur: 0.045, consonantKind: 'stop_k', seed: 106 }), oto: { offset: 20, consonant: 80, cutoff: 80, pre: 35, ovl: 15 } },
    { key: 'sa', alias: 'さ', pcm: makeVocalPcm({ lyric: 'さ', consonantDur: 0.07, consonantKind: 'fric_s', seed: 107 }), oto: { offset: 20, consonant: 80, cutoff: 80, pre: 35, ovl: 15 } },
    { key: 'ka0', alias: 'か0', pcm: makeVocalPcm({ lyric: 'か', consonantDur: 0.045, consonantKind: 'stop_k', seed: 108 }), oto: { offset: 20, consonant: 80, cutoff: 80, pre: 35, ovl: 0 } },
  ];

  const OTO_SIZE = 632;
  const otoPtr = mod._malloc(defs.length * OTO_SIZE);
  mod.HEAPU8.fill(0, otoPtr, otoPtr + defs.length * OTO_SIZE);
  for (let i = 0; i < defs.length; i++) {
    const s = defs[i];
    const kp = mod._malloc(mod.lengthBytesUTF8(s.key) + 1);
    mod.stringToUTF8(s.key, kp, mod.lengthBytesUTF8(s.key) + 1);
    const pp = mod._malloc(s.pcm.length * 4);
    mod.HEAPF32.set(s.pcm, pp / 4);
    mod._load_embedded_resource_f32(kp, pp, s.pcm.length);
    mod._free(pp);
    mod._free(kp);

    const b = otoPtr + i * OTO_SIZE;
    mod.setValue(b + 8, s.oto.cutoff, 'double');
    mod.stringToUTF8(s.key, b + 16, 64);
    mod.stringToUTF8(s.alias, b + 80, 512);
    mod.setValue(b + 592, s.oto.offset, 'double');
    mod.setValue(b + 600, s.oto.consonant, 'double');
    mod.setValue(b + 616, s.oto.pre, 'double');
    mod.setValue(b + 624, s.oto.ovl, 'double');
  }
  mod.ccall('set_oto_data', null, ['number', 'number'], [otoPtr, defs.length]);
  mod._free(otoPtr);

  const timeline = [
    { key: 'a', midi: 60, startMs: 0, durMs: 450 },
    { key: 'ka', midi: 64, startMs: 450, durMs: 350 },
    { key: 'i', midi: 67, startMs: 800, durMs: 650 },
    { key: 'sa', midi: 65, startMs: 1450, durMs: 300 },
    { key: 'e', midi: 64, startMs: 1750, durMs: 180 },
    { key: 'ka0', midi: 62, startMs: 1930, durMs: 350, preMs: 35, ovlMs: 0 },
    { key: 'o', midi: 60, startMs: 2280, durMs: 700 },
    { key: 'a', midi: 67, startMs: 2980, durMs: 550 },
    { key: 'u', midi: 69, startMs: 3530, durMs: 750 },
    { key: 'e', midi: 67, startMs: 4280, durMs: 450 },
    { key: 'o', midi: 65, startMs: 4730, durMs: 600 },
    { key: null, midi: 60, startMs: 5330, durMs: 105 },
    { key: 'sa', midi: 64, startMs: 5435, durMs: 250 },
    { key: 'a', midi: 67, startMs: 5685, durMs: 250 },
    { key: 'ka', midi: 72, startMs: 5935, durMs: 850 },
    { key: null, midi: 60, startMs: 6785, durMs: 180 },
    { key: 'i', midi: 69, startMs: 6965, durMs: 400 },
    { key: 'a', midi: 67, startMs: 7365, durMs: 900 },
  ];

  const NOTE_SIZE = 88;
  const notesPtr = mod._malloc(timeline.length * NOTE_SIZE);
  mod.HEAPU8.fill(0, notesPtr, notesPtr + timeline.length * NOTE_SIZE);
  const toFree = [notesPtr];

  for (let i = 0; i < timeline.length; i++) {
    const n = timeline[i];
    const prev = i > 0 && timeline[i - 1].key ? timeline[i - 1] : null;
    const frames = Math.max(2, Math.round(n.durMs / FRAME_MS) + 1);
    const b = notesPtr + i * NOTE_SIZE;
    const pc = buildWebPitchCurve(n.midi, prev ? prev.midi : null, n.durMs, frames);
    const pcPtr = mod._malloc(frames * 8);
    toFree.push(pcPtr);
    mod.HEAPF64.set(pc, pcPtr / 8);

    if (n.key) {
      const wp = mod._malloc(mod.lengthBytesUTF8(n.key) + 1);
      toFree.push(wp);
      mod.stringToUTF8(n.key, wp, mod.lengthBytesUTF8(n.key) + 1);
      mod.setValue(b + 0, wp, 'i32');
    }
    mod.setValue(b + 4, pcPtr, 'i32');
    mod.setValue(b + 8, frames, 'i32');
    mod.setValue(b + 48, 100.0, 'double');
    mod.setValue(b + 64, n.startMs, 'double');
    const defOto = n.key ? defs.find((d) => d.key === n.key)?.oto : null;
    mod.setValue(b + 72, n.preMs ?? (defOto ? defOto.pre : 0), 'double');
    mod.setValue(b + 80, n.ovlMs ?? (defOto ? defOto.ovl : 0), 'double');
  }

  const outPath = '/test_synth_quality.wav';
  mod.ccall(
    'execute_render_cancelable',
    null,
    ['number', 'number', 'string', 'number', 'number', 'number'],
    [notesPtr, timeline.length, outPath, 0, 0, 0]
  );

  const rawBytes = mod.FS.readFile(outPath);
  mod.FS.unlink(outPath);
  for (const p of toFree) mod._free(p);

  const cleanBuf = cleanWavArrayBuffer(rawBytes.slice().buffer);
  const metrics = analyzeWav(cleanBuf);

  assert.equal(metrics.clusters, 0, `expected 0 dense 50ms jump clusters, got ${metrics.clusters}`);
  assert.ok(metrics.jumps <= 5, `expected minimal steep jumps, got ${metrics.jumps}`);
  assert.ok(
    metrics.silences.filter((x) => x.durMs >= 41).length >= 2,
    `expected >=2 preserved rests >=41ms, got ${JSON.stringify(metrics.silences)}`
  );
  assert.ok(
    metrics.flatPct < 58,
    `expected non-staircase F0 plateau ratio < 58%, got ${metrics.flatPct.toFixed(1)}%`
  );
  assert.ok(
    metrics.vibRateHz >= 4.5 && metrics.vibRateHz <= 6.5,
    `expected natural sustained vibrato 4.5-6.5Hz, got ${metrics.vibRateHz}Hz`
  );
  assert.ok(
    metrics.db16k >= -45 && metrics.db16k <= -35,
    `expected 16kHz level between -35 and -45 dB vs 1-3kHz, got ${metrics.db16k.toFixed(1)}dB`
  );
  assert.ok(
    metrics.db4_8 >= -18,
    `expected healthy 4-8kHz clarity >= -18 dB vs 1-3kHz, got ${metrics.db4_8.toFixed(1)}dB`
  );
});

test('UST portamento Fix A/B, hasExplicitUstPortamento, 10-cent serialization, and Section 4 gating', async () => {
  const { parseUstText } = await import('../assets/src/utils/formatConverter.ts');

  assert.equal(hasExplicitUstPortamento({ pbs: '0;0', pbw: '50', pby: '0' }), false);
  assert.equal(hasExplicitUstPortamento({ pbs: '', pbw: '', pby: '' }), false);
  assert.equal(
    hasExplicitUstPortamento({ pbs: '-29;-20', pbw: '56', pby: '', hasPbs: true, hasPbw: true, hasPby: false }),
    true
  );

  const parsedEmptyPby = parsePitchBend('-29;-20', '56', '');
  assert.equal(parsedEmptyPby.length, 2);
  assert.equal(parsedEmptyPby[0].offsetMs, -29);
  assert.equal(parsedEmptyPby[0].semitone, -2);
  assert.equal(parsedEmptyPby[1].offsetMs, 27);
  assert.equal(parsedEmptyPby[1].semitone, 0);

  const serialized = serializePitchBend(parsedEmptyPby);
  assert.equal(serialized.pbs, '-29;-20');
  assert.equal(serialized.pbw, '56');
  assert.equal(serialized.pby, '0');

  const ust = `[#VERSION]
UST Version 1.2
[#SETTING]
Tempo=120.00
[#0000]
Length=480
Lyric=あ
NoteNum=72
[#0001]
Length=480
Lyric=か
NoteNum=74
PBS=-29
PBW=56
[#0002]
Length=480
Lyric=き
NoteNum=74
PBS=-29
PBW=56
[#0003]
Length=240
Lyric=R
NoteNum=60
[#0004]
Length=480
Lyric=く
NoteNum=77
PBS=-29
PBW=56
`;
  const proj = parseUstText(ust);
  assert.equal(proj.notes[1].pbs, '-29;-20');
  assert.equal(proj.notes[1].pby, '');
  assert.equal(proj.notes[1].hasPby, false);
  // Same pitch delta === 0 stays untouched
  assert.equal(proj.notes[2].pbs, '-29');
  // Rest resets continuity (R is not emitted as a note, so 'く' is notes[3])
  assert.equal(proj.notes.length, 4);
  assert.equal(proj.notes[3].pbs, '-29');

  const clientSrc = fs.readFileSync('assets/src/voseCoreClient.ts', 'utf8');
  assert.match(clientSrc, /if \(hasExplicitUstPortamento\(note\)\)/);
  assert.match(clientSrc, /else if \(prevNote && typeof prevNote\.noteNum === 'number' && prevNote\.noteNum !== noteNum\)/);
});

test('Compiled WASM PreUtterance pitch timing alignment lands transition midpoint at ~500ms without early jump', async () => {
  const { parseUstText } = await import('../assets/src/utils/formatConverter.ts');
  const proj = parseUstText(`[#SETTING]\nTempo=120.00\n[#0000]\nLength=480\nLyric=あ\nNoteNum=72\n[#0001]\nLength=480\nLyric=か\nNoteNum=74\nPBS=-29\nPBW=56\nPreUtterance=50\nVoiceOverlap=15\n`);

  const wasmBuf = fs.readFileSync('public/wasm/vose_core.wasm');
  const nodeDesc = Object.getOwnPropertyDescriptor(process.versions, 'node');
  const hadWindow = 'window' in globalThis;
  const prevWindow = globalThis.window;
  let mod;
  try {
    delete process.versions.node;
    globalThis.window = {};
    const modFactory = (await import(pathToFileURL(path.resolve('assets/src/wasm/vose_core.js')).href)).default;
    mod = await modFactory({
      instantiateWasm(info, recv) {
        WebAssembly.instantiate(wasmBuf, info).then((r) => recv(r.instance));
        return {};
      },
      print: () => {},
      printErr: () => {},
    });
  } finally {
    if (nodeDesc) Object.defineProperty(process.versions, 'node', nodeDesc);
    if (hadWindow) globalThis.window = prevWindow;
    else delete globalThis.window;
  }

  const defs = [
    { key: 'a', alias: 'あ', pcm: makeVocalPcm({ lyric: 'あ', baseHz: 440 * Math.pow(2, (72 - 69) / 12), seed: 201 }), oto: { offset: 20, consonant: 40, cutoff: 80, pre: 0, ovl: 0 } },
    { key: 'ka', alias: 'か', pcm: makeVocalPcm({ lyric: 'か', baseHz: 440 * Math.pow(2, (74 - 69) / 12), consonantDur: 0.035, consonantKind: 'stop_k', seed: 202 }), oto: { offset: 20, consonant: 70, cutoff: 80, pre: 50, ovl: 15 } },
  ];
  const OTO_SIZE = 632;
  const otoPtr = mod._malloc(defs.length * OTO_SIZE);
  mod.HEAPU8.fill(0, otoPtr, otoPtr + defs.length * OTO_SIZE);
  for (let i = 0; i < defs.length; i++) {
    const s = defs[i];
    const kp = mod._malloc(mod.lengthBytesUTF8(s.key) + 1);
    mod.stringToUTF8(s.key, kp, mod.lengthBytesUTF8(s.key) + 1);
    const pp = mod._malloc(s.pcm.length * 4);
    mod.HEAPF32.set(s.pcm, pp / 4);
    mod._load_embedded_resource_f32(kp, pp, s.pcm.length);
    mod._free(pp);
    mod._free(kp);
    const b = otoPtr + i * OTO_SIZE;
    mod.setValue(b + 8, s.oto.cutoff, 'double');
    mod.stringToUTF8(s.key, b + 16, 64);
    mod.stringToUTF8(s.alias, b + 80, 512);
    mod.setValue(b + 592, s.oto.offset, 'double');
    mod.setValue(b + 600, s.oto.consonant, 'double');
    mod.setValue(b + 616, s.oto.pre, 'double');
    mod.setValue(b + 624, s.oto.ovl, 'double');
  }
  mod.ccall('set_oto_data', null, ['number', 'number'], [otoPtr, defs.length]);
  mod._free(otoPtr);

  const durMs = 500;
  const frames = Math.round(durMs / FRAME_MS) + 1;
  const items = [
    { key: 'a', note: proj.notes[0], next: proj.notes[1], startMs: 0, preMs: 0, ovlMs: 0 },
    { key: 'ka', note: proj.notes[1], next: null, startMs: 500, preMs: 50, ovlMs: 15 },
  ];
  const NOTE_SIZE = 88;
  const notesPtr = mod._malloc(items.length * NOTE_SIZE);
  mod.HEAPU8.fill(0, notesPtr, notesPtr + items.length * NOTE_SIZE);
  const toFree = [notesPtr];

  for (let i = 0; i < items.length; i++) {
    const it = items[i];
    const baseHz = 440 * Math.pow(2, (it.note.noteNum - 69) / 12);
    const pcPtr = mod._malloc(frames * 8);
    toFree.push(pcPtr);
    mod.HEAPF64.set(new Array(frames).fill(baseHz), pcPtr / 8);

    let pts = it.note.pbw ? parsePitchBend(it.note.pbs, it.note.pbw, it.note.pby) : [];
    if (pts.length > 0 && durMs > pts[pts.length - 1].offsetMs) {
      pts.push({ offsetMs: durMs, semitone: 0 });
    }
    if (it.next) {
      pts = appendNextNotePreBitePoints(pts.length ? pts : null, it.note.noteNum, durMs, it.next) || [];
    }
    const leadFrames = it.preMs > 0 ? Math.ceil(it.preMs / FRAME_MS) : 0;
    const po = new Array(frames + leadFrames).fill(0);
    for (let j = 0; j < po.length; j++) {
      po[j] = sampleSemitoneAt(pts, (j - leadFrames) * FRAME_MS) * 100.0;
    }
    const poPtr = mod._malloc(po.length * 8);
    toFree.push(poPtr);
    mod.HEAPF64.set(po, poPtr / 8);

    const wp = mod._malloc(mod.lengthBytesUTF8(it.key) + 1);
    toFree.push(wp);
    mod.stringToUTF8(it.key, wp, mod.lengthBytesUTF8(it.key) + 1);

    const b = notesPtr + i * NOTE_SIZE;
    mod.setValue(b + 0, wp, 'i32');
    mod.setValue(b + 4, pcPtr, 'i32');
    mod.setValue(b + 8, frames, 'i32');
    mod.setValue(b + 36, poPtr, 'i32');
    mod.setValue(b + 40, po.length, 'i32');
    mod.setValue(b + 48, 100.0, 'double');
    mod.setValue(b + 64, it.startMs, 'double');
    mod.setValue(b + 72, it.preMs, 'double');
    mod.setValue(b + 80, it.ovlMs, 'double');
  }

  const outPath = '/test_preutt_align.wav';
  mod.ccall(
    'execute_render_cancelable',
    null,
    ['number', 'number', 'string', 'number', 'number', 'number'],
    [notesPtr, items.length, outPath, 0, 0, 0]
  );
  const u8 = mod.FS.readFile(outPath);
  mod.FS.unlink(outPath);
  for (const p of toFree) mod._free(p);

  const view = new DataView(u8.buffer, u8.byteOffset, u8.byteLength);
  let pos = 12, dOff = 0, dSz = 0;
  while (pos + 8 <= u8.length) {
    const id = String.fromCharCode(view.getUint8(pos), view.getUint8(pos + 1), view.getUint8(pos + 2), view.getUint8(pos + 3));
    const sz = view.getUint32(pos + 4, true);
    if (id === 'data') { dOff = pos + 8; dSz = sz; break; }
    pos += 8 + sz + (sz & 1);
  }
  const pcm16 = new Int16Array(u8.buffer, u8.byteOffset + dOff, dSz / 2);
  const s = new Float32Array(pcm16.length);
  for (let i = 0; i < pcm16.length; i++) s[i] = pcm16[i] / 32768;

  const yinMidi512 = (frame) => {
    const N = frame.length;
    const tMin = Math.floor(SR / 700), tMax = Math.ceil(SR / 250);
    const d = new Float64Array(tMax + 2), c = new Float64Array(tMax + 2);
    for (let t = 1; t <= tMax + 1; t++) {
      let acc = 0;
      for (let j = 0; j < N - tMax - 2; j++) { const df = frame[j] - frame[j + t]; acc += df * df; }
      d[t] = acc;
    }
    c[0] = 1;
    let rs = 0;
    for (let t = 1; t <= tMax + 1; t++) { rs += d[t]; c[t] = rs > 0 ? (d[t] * t) / rs : 1; }
    let bt = -1;
    for (let t = tMin; t <= tMax; t++) {
      if (c[t] < 0.15) { while (t + 1 <= tMax && c[t + 1] < c[t]) t++; bt = t; break; }
    }
    if (bt < 0) return null;
    if (bt > 1 && bt < tMax + 1) {
      const y0 = c[bt - 1], y1 = c[bt], y2 = c[bt + 1], den = 2 * (2 * y1 - y2 - y0);
      if (Math.abs(den) > 1e-12) bt += Math.max(-0.5, Math.min(0.5, (y0 - y2) / den));
    }
    return 69 + 12 * Math.log2((SR / bt) / 440);
  };

  let preJumpMax = -Infinity;
  for (let tMs = 456; tMs <= 470; tMs += 2) {
    const center = Math.round((tMs / 1000) * SR);
    const m = yinMidi512(s.subarray(center - 256, center + 256));
    if (m !== null) preJumpMax = Math.max(preJumpMax, m);
  }
  assert.ok(preJumpMax < 72.3, `expected no early pitch jump during PreUtterance lead-in, got max ${preJumpMax.toFixed(3)}`);

  let midpointMs = null;
  for (let tMs = 460; tMs <= 530; tMs += 2) {
    const center = Math.round((tMs / 1000) * SR);
    const m = yinMidi512(s.subarray(center - 256, center + 256));
    if (midpointMs === null && m !== null && m >= 73.0) midpointMs = tMs;
  }
  assert.ok(
    midpointMs !== null && midpointMs >= 494 && midpointMs <= 506,
    `expected 50% midpoint near 499-500ms, got ${midpointMs}ms`
  );

  const postCenter = Math.round((536 / 1000) * SR);
  const postMidi = yinMidi512(s.subarray(postCenter - 256, postCenter + 256));
  assert.ok(postMidi !== null && postMidi >= 73.8, `expected settled target near 74.0 MIDI, got ${postMidi}`);
});

test('C++ vose_core short-note preutterance cap, gap allowance, floor bypass, and consonant compression contract', () => {
  const coreSrc = fs.readFileSync('assets/src/vose_core.cpp', 'utf8');
  const internalHdr = fs.readFileSync('include/vose_core_internal.h', 'utf8');

  // 1. Prepass tracks nominal base_note_samples and orig_preutterance_ms
  assert.match(coreSrc, /int64_t\s+base_note_samples\s*=\s*0;/);
  assert.match(coreSrc, /double\s+orig_preutterance_ms\s*=\s*0\.0;/);
  assert.match(internalHdr, /int64_t\s+base_note_samples\s*=\s*0;/);
  assert.match(internalHdr, /double\s+orig_preutterance_ms\s*=\s*0\.0;/);

  // 2. 50% default / 32% short-note (<120ms) fraction constants + gap-inclusive limit + proportional pre_ms/ov_ms scaling + floor bypass
  assert.match(coreSrc, /kMaxPrevNotePreutterFraction\s*=\s*0\.5;/);
  assert.match(coreSrc, /kShortPrevNoteThresholdMs\s*=\s*120\.0;/);
  assert.match(coreSrc, /kShortPrevNotePreutterFraction\s*=\s*0\.32;/);
  assert.match(coreSrc, /kOnsetFadeUnvoicedMs\s*=\s*8\.0;/);
  assert.match(coreSrc, /const double prev_len_ms = 1000\.0 \* static_cast<double>\(pv\.base_note_samples\) \/ kFs;/);
  assert.match(coreSrc, /const double gap_ms = std::max\(0\.0, notes\[i\]\.start_time_ms - prev_end_ms\);/);
  assert.match(coreSrc, /const double limit = prev_fraction \* prev_len_ms \+ gap_ms;/);
  assert.match(coreSrc, /const bool floor_overlap = prev_renderable && notes\[i\]\.overlap_ms < 0\.0 && !pseudo_pre && !capped_pre;/);

  // 3. map_time_with_preutterance compresses preutterance to keep vowel on beat, and portamento indexes by beat offset
  assert.match(coreSrc, /const double pre_scale = \(safe_pre > 0\.0\) \? \(safe_orig_pre \/ safe_pre\) : 1\.0;/);
  assert.match(coreSrc, /const double fixed_after_pre = std::max\(0\.0, fixed - safe_orig_pre\);/);
  assert.match(coreSrc, /const bool in_consonant_friction = has_unvoiced && \(src_offset_ms < unvoiced_attack_ms\);/);
  assert.match(coreSrc, /const int orig_lead = n\.portamento_length - n\.pitch_length;/);
});

test('applyAutoTuneToNotes preserves explicit UST PBS/PBW notes and tunes only unspecified/placeholder notes', () => {
  const input = [
    { id: 'n1', lyric: 'あ', noteNum: 60, tick: 0, length: 480, pbs: '0;0', pbw: '50', pby: '0' },
    { id: 'n2', lyric: 'い', noteNum: 64, tick: 480, length: 480, pbs: '-29;-40', pbw: '56', pby: '12', hasPbs: true, hasPbw: true, hasPby: true },
    { id: 'n3', lyric: 'う', noteNum: 67, tick: 960, length: 480, pbs: '', pbw: '', pby: '' },
  ];
  const tuned = applyAutoTuneToNotes(input, 120);
  // Placeholder ('0;0', '50', '0') was auto-tuned
  assert.notEqual(tuned[0].pbw, '50');
  // Explicit UST note is untouched
  assert.equal(tuned[1].pbs, '-29;-40');
  assert.equal(tuned[1].pbw, '56');
  assert.equal(tuned[1].pby, '12');
  // Unspecified note gets auto-portamento transition
  assert.ok(tuned[2].pbs.startsWith('-'));
  assert.ok(tuned[2].pbw.length > 0);
});

