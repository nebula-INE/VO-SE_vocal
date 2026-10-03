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
