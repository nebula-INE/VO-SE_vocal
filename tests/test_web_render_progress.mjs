import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const client = fs.readFileSync('src/voseCoreClient.ts', 'utf8');
const worker = fs.readFileSync('src/voseCoreWorker.ts', 'utf8');

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
