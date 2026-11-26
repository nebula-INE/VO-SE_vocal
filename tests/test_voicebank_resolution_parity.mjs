import assert from 'node:assert/strict';
import http from 'node:http';
import { mkdir, writeFile, rm } from 'node:fs/promises';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const ROOT = process.cwd();
const VOICEBANKS = join(ROOT, 'temp', 'voicebanks');
const fixture = JSON.parse(
  readFileSync(join(ROOT, 'specs', 'voicebank_resolution_parity.json'), 'utf8')
);

function makeWav(seed) {
  const sampleRate = 8000;
  const samples = 1200;
  const data = Buffer.alloc(samples * 2);
  for (let i = 0; i < samples; i += 1) {
    const value = Math.round(Math.sin((i + seed) * 0.071) * 11000);
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

function request(port, pathname) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: '127.0.0.1', port, path: pathname, method: 'GET' }, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: Buffer.concat(chunks)
      }));
    });
    req.on('error', reject);
    req.end();
  });
}

async function waitForServer(child, port) {
  let stdout = '';
  let stderr = '';
  child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
  child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error('server exited before becoming ready\nstdout:\n' + stdout + '\nstderr:\n' + stderr);
    }
    try {
      const response = await request(port, '/api/py/voicebanks');
      if (response.status < 500) return { stdout, stderr };
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error('server startup timeout\nstdout:\n' + stdout + '\nstderr:\n' + stderr);
}

function otoLines(entries) {
  return entries.map((entry) =>
    entry.filename + '=' + entry.alias + ',0,0,0,20,5'
  );
}

test('Web and Desktop share Extra pairing, main-first aliases, prefix.map, and paired WAV fallback', async (t) => {
  const suffix = '__parity_' + process.pid + '_' + Date.now();
  const mainName = fixture.voicebanks.main + suffix;
  const extraName = mainName + '_Extra';
  const unrelatedName = fixture.voicebanks.unrelated + suffix;
  const mainDir = join(VOICEBANKS, mainName);
  const extraDir = join(VOICEBANKS, extraName);
  const unrelatedDir = join(VOICEBANKS, unrelatedName);
  const directories = [mainDir, extraDir, unrelatedDir];

  const wavs = new Map();
  let seed = 10;
  for (const [relativePath, payload] of Object.entries(fixture.wav_payloads)) {
    const [bankKey, ...segments] = relativePath.split('/');
    const bankDir = bankKey === 'main'
      ? mainDir
      : bankKey === 'extra'
        ? extraDir
        : unrelatedDir;
    const absolutePath = join(bankDir, ...segments);
    const bytes = makeWav(seed);
    seed += 17;
    wavs.set(payload, bytes);
    await mkdir(join(absolutePath, '..'), { recursive: true });
    await writeFile(absolutePath, bytes);
  }

  for (const directory of directories) {
    await mkdir(directory, { recursive: true });
    await writeFile(join(directory, 'character.txt'), 'name=parity test\n', 'utf8');
  }

  const mainLines = [
    ...fixture.main_cv_aliases.map((alias, index) => 'cv_' + index + '.wav=' + alias + ',0,0,0,20,5'),
    ...otoLines(fixture.main_entries)
  ];
  await writeFile(join(mainDir, 'oto.ini'), mainLines.join('\n') + '\n', 'utf8');
  await writeFile(join(mainDir, 'prefix.map'), fixture.prefix_map.main, 'utf8');
  const encodingDir = join(mainDir, 'encoding');
  await mkdir(encodingDir, { recursive: true });
  await writeFile(
    join(encodingDir, 'oto.ini'),
    Buffer.from(fixture.encoding_case.oto_base64_cp932, 'base64')
  );

  await writeFile(join(extraDir, 'oto.ini'), otoLines(fixture.extra_entries).join('\n') + '\n', 'utf8');
  await writeFile(join(extraDir, 'prefix.map'), fixture.prefix_map.extra, 'utf8');

  await writeFile(
    join(unrelatedDir, 'oto.ini'),
    otoLines(fixture.unrelated_entries).join('\n') + '\n',
    'utf8'
  );

  const port = 37000 + (process.pid % 1000);
  const child = spawn(process.execPath, ['server.js', '--port', String(port)], {
    cwd: ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, NODE_ENV: 'test' }
  });
  t.after(async () => {
    child.kill('SIGTERM');
    await Promise.all(directories.map((directory) => rm(directory, { recursive: true, force: true })));
  });

  const startup = await waitForServer(child, port);
  const sampleUrl = (voicebank, alias, noteNum) =>
    '/api/py/voicebank-sample?name=' + encodeURIComponent(voicebank) +
    '&alias=' + encodeURIComponent(alias) +
    (noteNum === undefined ? '' : '&noteNum=' + encodeURIComponent(String(noteNum)));

  const extraOnly = await request(port, sampleUrl(extraName, fixture.expected.extra_only_alias, 60));
  assert.equal(extraOnly.status, 200, startup.stderr);
  assert.equal(decodeURIComponent(extraOnly.headers['x-alias-matched']), fixture.expected.extra_only_alias);
  assert.deepEqual(
    extraOnly.body,
    wavs.get(fixture.expected.extra_only_alias_wav_payload),
    'OTO WAV basename fallback must find the unique file in its exact paired main bank'
  );

  const duplicate = await request(port, sampleUrl(extraName, fixture.expected.main_layer_alias_wins, 60));
  assert.equal(duplicate.status, 200, startup.stderr);
  assert.deepEqual(
    duplicate.body,
    wavs.get(fixture.wav_payloads['main/audio/main-shared.wav']),
    'duplicate aliases must resolve from the main layer before Extra'
  );

  const prefixMapped = await request(port, sampleUrl(extraName, 'あ', 65));
  assert.equal(prefixMapped.status, 200, startup.stderr);
  assert.equal(decodeURIComponent(prefixMapped.headers['x-alias-matched']), fixture.expected.prefix_map_alias);
  assert.deepEqual(
    prefixMapped.body,
    wavs.get(fixture.expected.prefix_map_wav_payload),
    'the main voicebank prefix.map must take priority over the Extra prefix.map'
  );

  const cp932Entry = await request(port, sampleUrl(extraName, fixture.expected.encoding_alias, 60));
  assert.equal(cp932Entry.status, 200, startup.stderr);
  assert.equal(decodeURIComponent(cp932Entry.headers['x-alias-matched']), fixture.expected.encoding_alias);
  assert.deepEqual(
    cp932Entry.body,
    wavs.get(fixture.encoding_case.wav_payload),
    'CP932 oto.ini must be decoded and its WAV entry must remain usable'
  );

  const unrelated = await request(port, sampleUrl(extraName, fixture.expected.unrelated_alias, 60));
  assert.equal(unrelated.status, 404, 'unrelated sibling voicebanks must never be searched');

  const ambiguous = await request(port, sampleUrl(extraName, fixture.expected.ambiguous_alias, 60));
  assert.equal(ambiguous.status, 404, 'ambiguous WAV basename matches must never be guessed');

  const selectedMain = await request(port, sampleUrl(mainName, fixture.expected.extra_only_alias, 60));
  assert.equal(selectedMain.status, 200, startup.stderr);
  assert.deepEqual(
    selectedMain.body,
    wavs.get(fixture.expected.extra_only_alias_wav_payload),
    'selecting the main bank must expose its exact Extra companion as fallback'
  );
});
