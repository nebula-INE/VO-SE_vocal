import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

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
  for (const value of ['../outside', '..', '.', 'a/b', 'a\\\\b', 'C:escape', 'bad\\0name']) {
    assert.throws(() => normalizeVoicebankName(value));
  }
});

test('upload names use only the final filename component', () => {
  assert.equal(voicebankNameFromFilename('../../voices/つくよみちゃん.zip'), 'つくよみちゃん');
  assert.equal(voicebankNameFromFilename('C:\\\\fakepath\\\\bank.zip'), 'bank');
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

test('server never shells out for ZIP extraction or reports fake test success', () => {
  const source = fs.readFileSync(new URL('../server.js', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /exec\\s*\\(/);
  assert.doesNotMatch(source, /extractAllTo\\s*\\(/);
  assert.match(source, /res\\.status\\(501\\)\\.json/);
  assert.match(source, /process\\.env\\.HOST \\|\\| '127\\.0\\.0\\.1'/);
});
