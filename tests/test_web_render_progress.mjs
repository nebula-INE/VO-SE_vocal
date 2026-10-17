import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const client = fs.readFileSync('src/voseCoreClient.ts', 'utf8');
const worker = fs.readFileSync('src/voseCoreWorker.ts', 'utf8');
const engine = fs.readFileSync('src/wasmEngine.ts', 'utf8');

test('Web render client does not use the old 25s core timeout', () => {
  assert.match(client, /const timeoutMs = 180000;/);
  assert.doesNotMatch(client, /const timeoutMs = 25000;/);
});

test('Web render client rejects Worker postMessage failures', () => {
  assert.match(client, /w\.postMessage\(msg, transferables\)/);
  assert.match(client, /pending\.delete\(requestId\)/);
  assert.match(client, /WASM Workerへのレンダリング要求送信に失敗しました/);
});

test('Web render progress is monotonic across Worker and C++ phases', () => {
  assert.match(client, /lastReportedPct = Math\.max\(lastReportedPct, mapped\)/);
  assert.match(client, /onProgress\?\.\(lastReportedPct\)/);
});

test('Web render Worker reports startup stages and has a WASM init watchdog', () => {
  assert.match(worker, /percent: 3/);
  assert.match(worker, /percent: 5/);
  assert.match(worker, /percent: 7/);
  assert.match(worker, /percent: 9/);
  assert.match(worker, /WASMモジュール初期化がタイムアウトしました/);
  assert.match(worker, /60000/);
});


test('Web render removes stale WASM output before rendering', () => {
  assert.match(worker, /mod\.FS\.unlink\?\.\(outputPath\)/);
  assert.match(worker, /前回のWAVを残したままだと/);
});

test('Web render rejects a completely silent WAV instead of reporting success', () => {
  assert.match(worker, /validateWavIsAudible\(wavBytes\)/);
  assert.match(worker, /WAVが完全な無音です/);
  assert.match(worker, /bitsPerSample === 16/);
  assert.match(worker, /bitsPerSample === 32/);
});

test('Web Audio fallback never synthesizes unresolved notes with a sawtooth oscillator', () => {
  const engine = fs.readFileSync('src/wasmEngine.ts', 'utf8');
  assert.doesNotMatch(engine, /osc\.type\s*=\s*['"]sawtooth['"]/);
  assert.doesNotMatch(engine, /offlineCtx\.createOscillator\(\)/);
  assert.doesNotMatch(engine, /psolaPitchAndTimeShiftBuffer\(/);
  assert.match(engine, /source\.playbackRate\.setValueAtTime\(/);
  assert.match(engine, /resolvedSampleCount\+\+/);
  assert.match(engine, /resolvedSampleCount === 0/);
  assert.match(engine, /unresolved notes are left silent/);
});

test('Web pitch bends use PSOLA semitone curves and keep playback-rate fallback relative to the base pitch', () => {
  const engine = fs.readFileSync('src/wasmEngine.ts', 'utf8');
  const curve = fs.readFileSync('src/utils/pitchCurve.ts', 'utf8');

  // When PSOLA succeeds, pitch bends are applied inside the pitch shifter,
  // not a second time through playbackRate.
  assert.match(engine, /makeSemitoneCurve\(bendPoints, startTimeSec - actualStartTime\)/);
  assert.match(engine, /pitchCurveSemitones:\s*curveFn/);
  assert.match(engine, /onPitchCurveApplied:\s*\(applied\) => \{ bendAppliedInPsola = applied; \}/);
  assert.match(engine, /if \(bendPoints && !bendAppliedInPsola\)/);
  assert.match(curve, /export function makeSemitoneCurve\(/);
  assert.match(curve, /p\.offsetMs \/ 1000/);
  const psola = fs.readFileSync('src/psolaPitchShift.ts', 'utf8');
  assert.match(psola, /onPitchCurveApplied\?: \(applied: boolean\) => void/);
  assert.match(psola, /opts\.onPitchCurveApplied\?\.\(false\)/);
  assert.match(psola, /opts\.onPitchCurveApplied\?\.\(!!curve\)/);

  // If PSOLA is unavailable, the playback-rate fallback remains relative to
  // the note's base pitch ratio.
  const start = engine.indexOf('scheduleSafePitchRamp(');
  assert.notEqual(start, -1);
  const block = engine.slice(start, start + 900);
  assert.match(block, /source\.playbackRate/);
  assert.match(block, /bendBaseRate/);
  assert.match(block, /bendBaseRate \* Math\.max\(0\.5, Math\.min\(2\.0, Math\.pow\(2, st \/ 12\)\)\)/);
});

