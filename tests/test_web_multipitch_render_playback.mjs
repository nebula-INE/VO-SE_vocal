import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { mkdir, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import test from 'node:test';

const ROOT = process.cwd();
const VOICEBANKS = join(ROOT, 'temp', 'voicebanks');

function makeWav(seed) {
  const sampleRate = 8000;
  const samples = 800;
  const data = Buffer.alloc(samples * 2);
  for (let i = 0; i < samples; i += 1) {
    const value = Math.round(Math.sin((i + seed) * 0.07) * 12000);
    data.writeInt16LE(value, i * 2);
  }
  const header = Buffer.alloc(44);
  header.write('RIFF', 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write('WAVE', 8);
  header.write('fmt ', 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(sampleRate, 24);
  header.writeUInt32LE(sampleRate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write('data', 36);
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

function request(port, method, path, body) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1', port, path, method,
      headers: body ? { 'content-type': 'application/json', 'content-length': Buffer.byteLength(body) } : undefined,
    }, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

async function waitForServer(child, port) {
  const started = once(child.stdout, 'data');
  const timer = new Promise((_, reject) => setTimeout(() => reject(new Error('server startup timeout')), 15000));
  await Promise.race([started, timer]);
  for (let i = 0; i < 50; i += 1) {
    try {
      const response = await request(port, 'GET', '/api/py/voicebanks');
      if (response.status < 500) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('server did not become ready');
}

test('Web multi-pitch render and playback sample stay on the same WAV', async (t) => {
  const voicebank = '__test_multipitch_' + process.pid;
  const voiceDir = join(VOICEBANKS, voicebank);
  const c4 = makeWav(11);
  const f4 = makeWav(97);
  await mkdir(voiceDir, { recursive: true });
  await writeFile(join(voiceDir, 'prefix.map'), 'C1\t\t_C4\nF4\t\t_F4\n', 'utf8');
  await writeFile(join(voiceDir, 'oto.ini'), 'a_C4.wav=あ_C4,0,0,0,0,0\na_F4.wav=あ_F4,0,0,0,0,0\n', 'utf8');
  await writeFile(join(voiceDir, 'a_C4.wav'), c4);
  await writeFile(join(voiceDir, 'a_F4.wav'), f4);

  const port = 33000 + (process.pid % 1000);
  const child = spawn(process.execPath, ['server.js', '--port', String(port)], { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'], env: { ...process.env, NODE_ENV: 'test' } });
  t.after(async () => { child.kill('SIGTERM'); await rm(voiceDir, { recursive: true, force: true }); });
  let stderr = '';
  child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
  try {
    await waitForServer(child, port);
    const render = await request(port, 'POST', '/api/py/render-notes', JSON.stringify({ voicebank, notes: [
      { id: 1, lyric: 'あ', noteNum: 64, tick: 0, length: 480 },
      { id: 2, lyric: 'あ', noteNum: 65, tick: 480, length: 480 },
    ] }));
    assert.equal(render.status, 200, stderr);
    const renderJson = JSON.parse(render.body.toString('utf8'));
    assert.equal(renderJson.success, true);
    assert.equal(renderJson.notes[0].aliasUsed, 'あ_C4');
    assert.equal(renderJson.notes[1].aliasUsed, 'あ_F4');
    assert.equal(renderJson.notes[0].hasWav, true);
    assert.equal(renderJson.notes[1].hasWav, true);
    assert.match(renderJson.notes[0].wavPath, /a_C4\.wav$/);
    assert.match(renderJson.notes[1].wavPath, /a_F4\.wav$/);
    for (const item of [[64, 'あ_C4', c4], [65, 'あ_F4', f4]]) {
      const noteNum = item[0]; const expectedAlias = item[1]; const expectedWav = item[2];
      const sample = await request(port, 'GET', '/api/py/voicebank-sample?name=' + encodeURIComponent(voicebank) + '&alias=' + encodeURIComponent('あ') + '&noteNum=' + noteNum);
      assert.equal(sample.status, 200, sample.body.toString());
      assert.equal(decodeURIComponent(sample.headers['x-alias-matched']), expectedAlias);
      assert.equal(sample.headers['content-type'], 'audio/wav');
      assert.deepEqual(sample.body, expectedWav);
    }
  } catch (error) { error.message += '\nserver stderr:\n' + stderr; throw error; }
});