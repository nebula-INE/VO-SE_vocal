import assert from 'node:assert/strict';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';
import {
  isAllowedRequestOrigin,
  isPathInside,
  normalizeChildName,
  normalizeRequestHostname,
  normalizeUploadId,
  resolveRelativeAssetPath,
  validateZipEntryPath
} from '../modules/utils/path_security.js';

test('voicebank names reject traversal, separators and reserved device names', () => {
  assert.equal(normalizeChildName('あ音源'), 'あ音源');
  assert.equal(normalizeChildName('../outside'), null);
  assert.equal(normalizeChildName('..'), null);
  assert.equal(normalizeChildName('folder\\bank'), null);
  assert.equal(normalizeChildName('CON'), null);
  assert.equal(normalizeChildName('CON.txt'), null);
  assert.equal(normalizeChildName('invalid:name'), null);
  assert.equal(normalizeChildName(''), null);
});

test('upload IDs are bounded opaque path-safe tokens', () => {
  assert.equal(normalizeUploadId('up_1700000000000_abc1234'), 'up_1700000000000_abc1234');
  assert.equal(normalizeUploadId('../outside'), null);
  assert.equal(normalizeUploadId('short'), null);
  assert.equal(normalizeUploadId('x'.repeat(129)), null);
});

test('oto references stay inside the owning voicebank, including Windows paths', () => {
  const root = path.join(os.tmpdir(), 'vose-voicebank');
  const nested = path.join(root, 'sub');
  assert.equal(resolveRelativeAssetPath(root, nested, 'voice.wav'), path.join(nested, 'voice.wav'));
  assert.equal(resolveRelativeAssetPath(root, nested, '../../outside.wav'), null);
  assert.equal(resolveRelativeAssetPath(root, nested, '/etc/passwd'), null);
  assert.equal(resolveRelativeAssetPath(root, nested, 'C:\\Windows\\win.ini'), null);
});

test('ZIP paths reject traversal and absolute paths', () => {
  assert.equal(validateZipEntryPath('Voice\\oto.ini'), 'Voice/oto.ini');
  assert.equal(validateZipEntryPath('Voice\\oto.ini'), 'Voice/oto.ini');
  assert.equal(validateZipEntryPath('../../outside.txt'), null);
  assert.equal(validateZipEntryPath('C:\\outside.txt'), null);
  assert.equal(validateZipEntryPath('/etc/passwd'), null);
});

test('local server accepts only loopback or explicitly allowed origins and hosts', () => {
  assert.equal(isAllowedRequestOrigin('http://localhost:3000', 3000), true);
  assert.equal(isAllowedRequestOrigin('http://127.0.0.1:3000', 3000), true);
  assert.equal(isAllowedRequestOrigin('http://evil.example', 3000), false);
  assert.equal(isAllowedRequestOrigin('null', 3000), false);
  assert.equal(isAllowedRequestOrigin('null', 3000, [], true), true);
  assert.equal(isAllowedRequestOrigin('https://lan.example', 3000, ['https://lan.example']), true);
  assert.equal(normalizeRequestHostname('localhost:3000'), 'localhost');
  assert.equal(normalizeRequestHostname('127.0.0.1:3000'), '127.0.0.1');
  assert.equal(normalizeRequestHostname('[::1]:3000'), '::1');
});

test('path containment only accepts strict descendants by default', () => {
  const root = path.join(os.tmpdir(), 'vose-root');
  assert.equal(isPathInside(root, path.join(root, 'child')), true);
  assert.equal(isPathInside(root, root), false);
  assert.equal(isPathInside(root, path.resolve(root, '..', 'outside')), false);
});
