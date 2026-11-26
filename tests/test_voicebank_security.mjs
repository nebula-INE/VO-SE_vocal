import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

import {
  normalizeVoicebankName,
  voicebankNameFromFilename,
  isSafeUploadId,
  resolvePathInside,
  resolveZipEntryPath,
  isRealPathInside
} from '../modules/utils/voicebank_security.js';

test('voicebank names reject path separators, traversal and control characters', () => {
  assert.equal(normalizeVoicebankName('つくよみちゃん'), 'つくよみちゃん');
  for (const value of ['../outside', '..', '.', 'a/b', 'a\\b', 'C:escape', 'bad\0name']) {
    assert.throws(() => normalizeVoicebankName(value));
  }
});

test('upload names use only the final filename component', () => {
  assert.equal(voicebankNameFromFilename('../../voices/つくよみちゃん.zip'), 'つくよみちゃん');
  assert.equal(voicebankNameFromFilename('C:\\fakepath\\bank.zip'), 'bank');
  assert.throws(() => voicebankNameFromFilename('folder/..zip'));
});

test('chunk upload identifiers are strictly bounded', () => {
  assert.equal(isSafeUploadId('session_123-abc'), true);
  for (const value of ['../outside', 'a/b', 'x'.repeat(81), '', null]) {
    assert.equal(isSafeUploadId(value), false);
  }
});

test('relative paths must remain inside their configured root', () => {
  const root = path.resolve('/tmp/voicebanks-test-root');
  assert.equal(resolvePathInside(root, 'bank-a'), path.join(root, 'bank-a'));
  assert.equal(resolvePathInside(root, '../outside'), null);
  assert.equal(resolvePathInside(root, '/etc/passwd'), null);
  assert.equal(resolvePathInside(root, 'C:/Windows/win.ini'), null);
});

test('ZIP entry names reject traversal, absolute paths and alternate data streams', () => {
  const root = path.resolve('/tmp/voicebanks-test-root');
  assert.equal(resolveZipEntryPath(root, 'Voice/oto.ini'), path.join(root, 'Voice', 'oto.ini'));
  for (const value of ['../outside.txt', 'Voice/../../outside.txt', '/etc/passwd', 'C:/Windows/a', 'Voice/file:stream', '']) {
    assert.equal(resolveZipEntryPath(root, value), null);
  }
});

test('realpath checks reject symlinks that escape a voicebank root', (t) => {
  const tempRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'vose-path-test-'));
  const bankRoot = path.join(tempRoot, 'bank');
  const outsideRoot = path.join(tempRoot, 'outside');
  fs.mkdirSync(bankRoot);
  fs.mkdirSync(outsideRoot);
  const outsideFile = path.join(outsideRoot, 'secret.wav');
  fs.writeFileSync(outsideFile, 'not a voice sample');
  const linkPath = path.join(bankRoot, 'linked.wav');
  try {
    fs.symlinkSync(outsideFile, linkPath);
  } catch (error) {
    fs.rmSync(tempRoot, { recursive: true, force: true });
    if (error.code === 'EPERM' || error.code === 'EACCES' || error.code === 'ENOTSUP') {
      t.skip('Symlink creation is not available on this platform');
      return;
    }
    throw error;
  }
  try {
    assert.equal(isRealPathInside(bankRoot, linkPath), false);
    assert.equal(isRealPathInside(bankRoot, path.join(bankRoot, 'missing.wav')), false);
  } finally {
    fs.rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('server.js has valid Node.js module syntax', () => {
  const serverPath = fileURLToPath(new URL('../server.js', import.meta.url));
  const result = spawnSync(process.execPath, ['--check', serverPath], { encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr || result.stdout || 'node --check failed');
});

test('server never shells out for ZIP extraction or reports fake test success', () => {
  const source = fs.readFileSync(new URL('../server.js', import.meta.url), 'utf8');
  assert.equal(source.includes('exec('), false);
  assert.equal(source.includes('extractAllTo('), false);
  assert.equal(source.includes('res.status(501).json'), true);
  assert.equal(source.includes("process.env.HOST || '127.0.0.1'"), true);
});

test('web voicebank imports have no fixed archive, upload, or OTO count ceilings', () => {
  const source = fs.readFileSync(new URL('../server.js', import.meta.url), 'utf8');
  for (const removedLimit of [
    'MAX_ZIP_ENTRIES',
    'MAX_ZIP_MEMBER_BYTES',
    'MAX_ZIP_TOTAL_BYTES',
    'MAX_UPLOAD_BYTES',
    'MAX_INDEXED_ENTRIES',
    'MAX_CHUNK_BYTES',
    'totalChunks > 300',
    "fileSize: 600 * 1024 * 1024"
  ]) {
    assert.equal(source.includes(removedLimit), false, `unexpected fixed limit: ${removedLimit}`);
  }
  assert.equal(source.includes('fs.promises.statfs'), true);
  assert.equal(source.includes('readTextFileLines(layer.iniPath)'), true);
  assert.equal(source.includes('fs.promises.readFile(layer.iniPath)'), false);
  assert.equal(source.includes("app.use(express.raw"), false);
});
