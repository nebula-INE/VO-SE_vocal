import fs from 'node:fs';
import path from 'node:path';

export function normalizeVoicebankName(value) {
  if (typeof value !== 'string') throw new TypeError('Voicebank name must be a string');
  const name = value.trim();
  if (
    !name ||
    name.length > 128 ||
    name === '.' ||
    name === '..' ||
    /[\\\\/\0\x00-\x1f\x7f:*?"<>|]/u.test(name) ||
    /^[a-z]:/i.test(name)
  ) {
    throw new TypeError('Invalid voicebank name');
  }
  return name;
}

export function voicebankNameFromFilename(value, fallback = 'custom_voicebank') {
  if (typeof value !== 'string' || !value.trim()) {
    return normalizeVoicebankName(fallback);
  }
  const normalized = value.replace(/\\\\/g, '/');
  const basename = normalized.slice(normalized.lastIndexOf('/') + 1);
  const stem = path.posix.parse(basename).name;
  return normalizeVoicebankName(stem || fallback);
}

export function isSafeUploadId(value) {
  return typeof value === 'string' && /^[A-Za-z0-9_-]{1,80}$/.test(value);
}

export function isPathInside(root, candidate) {
  const absoluteRoot = path.resolve(root);
  const absoluteCandidate = path.resolve(candidate);
  const relative = path.relative(absoluteRoot, absoluteCandidate);
  return relative === '' ||
    (relative !== '..' && !relative.startsWith(`..${path.sep}`) && !path.isAbsolute(relative));
}

export function resolvePathInside(root, relativePath, baseDir = root) {
  if (typeof relativePath !== 'string' || relativePath.includes('\0')) return null;
  const normalized = relativePath.replace(/\\\\/g, '/');
  if (
    normalized.startsWith('/') ||
    normalized.startsWith('//') ||
    /^[a-z]:/i.test(normalized)
  ) return null;
  const candidate = path.resolve(baseDir, normalized);
  return isPathInside(root, candidate) ? candidate : null;
}

export function resolveZipEntryPath(root, entryName) {
  if (typeof entryName !== 'string' || !entryName || entryName.includes('\0')) return null;
  const normalized = entryName.replace(/\\\\/g, '/');
  if (
    normalized.startsWith('/') ||
    normalized.startsWith('//') ||
    /^[a-z]:/i.test(normalized)
  ) return null;
  const segments = normalized.split('/').filter(Boolean);
  if (
    segments.length === 0 ||
    segments.some((segment) =>
      segment === '.' ||
      segment === '..' ||
      /[\x00-\x1f\x7f:]/u.test(segment)
    )
  ) return null;
  const candidate = path.resolve(root, ...segments);
  return isPathInside(root, candidate) ? candidate : null;
}

export function isRealPathInside(root, candidate) {
  try {
    const realRoot = fs.realpathSync(root);
    const realCandidate = fs.realpathSync(candidate);
    return isPathInside(realRoot, realCandidate);
  } catch {
    return false;
  }
}
