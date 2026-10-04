import assert from 'node:assert/strict';
import http from 'node:http';
import { once } from 'node:events';
import { mkdir, writeFile, rm } from 'node:fs/promises';
import { readFileSync } from 'node:fs';
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

test('Web sample endpoint exposes voicebank and alias context on lookup failure', () => {
  const server = readFileSync('server.js', 'utf8');
  assert.match(server, /voicebank-sample: voicebank not found requested=/);
  assert.match(server, /voicebank-sample: \$\{reason\} voicebank=/);
  assert.match(server, /requestedVoicebank: String\(name \|\| ''\)/);
});

test('Web Audio fallback reports sample HTTP failures with alias context', () => {
  const engine = readFileSync('src/wasmEngine.ts', 'utf8');
  assert.match(engine, /\[wasmEngine\] サンプル取得失敗 status=/);
  assert.match(engine, /await res\.text\(\)/);
  assert.match(engine, /noteNum=\$\{noteNum \?\? ''\}/);
});

test('Web voicebank resolution trims and NFC-normalizes the requested name', () => {
  const server = readFileSync('server.js', 'utf8');
  assert.match(server, /String\(targetName\)\.normalize\('NFC'\)\.trim\(\)/);
  assert.match(server, /const lowerTarget = normalizedTargetName\.toLowerCase\(\);/);
});

test('Web sample fetch logs the HTTP failure detail instead of hiding every unresolved sample', () => {
  const client = readFileSync('src/voseCoreClient.ts', 'utf8');
  assert.match(client, /サンプル取得失敗 status=/);
  assert.match(client, /await res\.text\(\)/);
  assert.match(client, /detail=/);
});

test('Web prefix.map two-column entries use the suffix like Desktop', () => {
  const server = readFileSync('server.js', 'utf8');
  assert.match(server, /if \(cols\.length >= 3\)/);
  assert.match(server, /suffix = \(cols\[1\] \|\| ''\)\.trim\(\);/);
  assert.match(server, /two-column suffix as a prefix/i);
});


test('Default voicebank repairs a legacy literal\\n oto.ini', () => {
  const server = readFileSync('server.js', 'utf8');
  assert.match(server, /needsRebuild = !otoText\.includes\('\\n'\) && otoText\.includes\('\\\\n'\);/);
  assert.match(server, /createDefaultVoicebank\(defaultName, true\);/);
});

test('Default voicebank writes real newlines to oto.ini', () => {
  const server = readFileSync('server.js', 'utf8');
  assert.match(
    server,
    /fs\.writeFileSync\(otoPathFinal, otoLines\.join\('\\n'\), \{ encoding: 'utf-8' \}\);/
  );
  assert.doesNotMatch(
    server,
    /fs\.writeFileSync\(otoPathFinal, otoLines\.join\('\\\\n'\), \{ encoding: 'utf-8' \}\);/
  );
});

test('Web multi-pitch aliases with identical oto aliases select the closest pitch WAV', async (t) => {
  const voicebank = '__test_multipitch_duplicate_' + process.pid;
  const voiceDir = join(VOICEBANKS, voicebank);
  const c4 = makeWav(21);
  const f4 = makeWav(111);
  await mkdir(voiceDir, { recursive: true });
  // Deliberately use the same alias for both pitch samples. This is the
  // case that a first-entry-only aliasMap silently mishandles.
  await writeFile(
    join(voiceDir, 'oto.ini'),
    'a_C4.wav=あ,0,0,0,0,0\na_F4.wav=あ,0,0,0,0,0\n',
    'utf8'
  );
  await writeFile(join(voiceDir, 'a_C4.wav'), c4);
  await writeFile(join(voiceDir, 'a_F4.wav'), f4);

  const port = 34000 + (process.pid % 1000);
  const child = spawn(process.execPath, ['server.js', '--port', String(port)], {
    cwd: ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, NODE_ENV: 'test' }
  });
  t.after(async () => {
    child.kill('SIGTERM');
    await rm(voiceDir, { recursive: true, force: true });
  });

  let stderr = '';
  child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });

  try {
    await waitForServer(child, port);
    for (const item of [[60, 'a_C4.wav', c4], [65, 'a_F4.wav', f4]]) {
      const noteNum = item[0];
      const expectedWav = item[2];
      const sample = await request(
        port,
        'GET',
        '/api/py/voicebank-sample?name=' +
          encodeURIComponent(voicebank) +
          '&alias=' +
          encodeURIComponent('あ') +
          '&noteNum=' +
          noteNum
      );
      assert.equal(sample.status, 200, sample.body.toString());
      assert.equal(sample.headers['x-sample-base-midi'], String(noteNum));
      assert.match(decodeURIComponent(sample.headers['x-alias-matched']), /^あ$/);
      assert.match(sample.headers['content-type'], /audio\/wav/);
      assert.deepEqual(sample.body, expectedWav);
    }
  } catch (error) {
    error.message += '\nserver stderr:\n' + stderr;
    throw error;
  }
});

test('Web default voicebank name matches the UI default and preserves legacy compatibility', () => {
  const server = readFileSync('server.js', 'utf8');
  assert.match(server, /const defaultName = 'Official Voice \\(VCV\\)'/);
  assert.match(server, /official voice \\(vcv\\)/i);
  assert.match(server, /standard japanese cv/i);
  assert.match(server, /legacyDefault/);
});

test('Web alias resolution normalizes path separators and whitespace without losing pitch selection', async (t) => {
  const voicebank = '__test_alias_normalization_' + process.pid;
  const voiceDir = join(VOICEBANKS, voicebank);
  const c4 = makeWav(31);
  const f4 = makeWav(131);
  await mkdir(join(voiceDir, 'Pitches'), { recursive: true });
  await writeFile(join(voiceDir, 'Pitches', 'a_C4.wav'), c4);
  await writeFile(join(voiceDir, 'Pitches', 'a_F4.wav'), f4);
  await writeFile(
    join(voiceDir, 'Pitches', 'oto.ini'),
    'a_C4.wav=あ,0,0,0,0,0\na_F4.wav=あ,0,0,0,0,0\n',
    'utf8'
  );

  const port = 35000 + (process.pid % 1000);
  const child = spawn(process.execPath, ['server.js', '--port', String(port)], {
    cwd: ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, NODE_ENV: 'test' }
  });
  t.after(async () => {
    child.kill('SIGTERM');
    await rm(voiceDir, { recursive: true, force: true });
  });

  try {
    await waitForServer(child, port);
    const sample = await request(
      port,
      'GET',
      '/api/py/voicebank-sample?name=' +
        encodeURIComponent(voicebank) +
        '&alias=' + encodeURIComponent(' あ ') +
        '&noteNum=65'
    );
    assert.equal(sample.status, 200, sample.body.toString());
    assert.deepEqual(sample.body, f4);
    assert.equal(decodeURIComponent(sample.headers['x-alias-matched']), 'あ');
  } finally {
    child.kill('SIGTERM');
  }
});

test('Web server keeps normalized alias candidates indexed for nested oto.ini folders', () => {
  const server = readFileSync('server.js', 'utf8');
  assert.match(server, /aliasLookupMap: new Map\(\)/);
  assert.match(server, /normalizeLookupAlias/);
  assert.match(server, /prefixedLookupKey/);
});

test('Web parser accepts UTF-16 oto.ini voicebanks', async (t) => {
  const voicebank = '__test_utf16_oto_' + process.pid;
  const voiceDir = join(VOICEBANKS, voicebank);
  await mkdir(voiceDir, { recursive: true });
  const expectedWav = makeWav(64);
  await writeFile(join(voiceDir, 'a.wav'), expectedWav);
  const otoText = 'a.wav=あ,0,0,0,0,0\\r\\n';
  await writeFile(
    join(voiceDir, 'oto.ini'),
    Buffer.concat([Buffer.from([0xff, 0xfe]), Buffer.from(otoText, 'utf16le')])
  );

  const port = 35600 + (process.pid % 500);
  const child = spawn(process.execPath, ['server.js', '--port', String(port)], {
    cwd: ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, NODE_ENV: 'test' }
  });
  t.after(async () => {
    child.kill('SIGTERM');
    await rm(voiceDir, { recursive: true, force: true });
  });

  await waitForServer(child, port);
  const sample = await request(
    port,
    'GET',
    '/api/py/voicebank-sample?name=' +
      encodeURIComponent(voicebank) +
      '&alias=' +
      encodeURIComponent('あ')
  );
  assert.equal(sample.status, 200, sample.body.toString());
  assert.deepEqual(sample.body, expectedWav);
});

test('Web normalized alias lookup survives the registry cache and separator normalization', async (t) => {
  const voicebank = '__test_registry_alias_lookup_' + process.pid;
  const voiceDir = join(VOICEBANKS, voicebank);
  const nestedDir = join(voiceDir, 'Pitches');
  await mkdir(nestedDir, { recursive: true });
  await writeFile(join(nestedDir, 'a.wav'), makeWav(64));
  await writeFile(
    join(nestedDir, 'oto.ini'),
    'a.wav=あ,0,0,0,0,0\n',
    'utf8'
  );

  const port = 35500 + (process.pid % 500);
  const child = spawn(process.execPath, ['server.js', '--port', String(port)], {
    cwd: ROOT,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, NODE_ENV: 'test' }
  });
  t.after(async () => {
    child.kill('SIGTERM');
    await rm(voiceDir, { recursive: true, force: true });
  });

  try {
    await waitForServer(child, port);
    const sample = await request(
      port,
      'GET',
      '/api/py/voicebank-sample?name=' +
        encodeURIComponent(voicebank) +
        '&alias=' + encodeURIComponent('Pitches/あ') +
        '&noteNum=60'
    );
    assert.equal(sample.status, 200, sample.body.toString());
    assert.deepEqual(sample.body, makeWav(64));
  } finally {
    child.kill('SIGTERM');
  }
});

test('Web voicebank alias diagnostics verifies the resolved WAV path', () => {
  const server = readFileSync('server.js', 'utf8');
  assert.match(server, /Alias resolved but WAV file is missing/);
  assert.match(server, /resolvedWavPath/);
  assert.match(server, /indexedEntryCount/);
});

test('Web render preflights one voice sample before bulk fetches', () => {
  const engine = readFileSync('src/wasmEngine.ts', 'utf8');
  assert.match(engine, /voicebank-alias-info\?name=/);
  assert.match(engine, /音源解決に失敗しました/);
  assert.match(engine, /音源解決OK voicebank=/);
});
