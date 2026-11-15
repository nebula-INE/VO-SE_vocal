import path from 'node:path';

const WINDOWS_DRIVE_PREFIX = /^[a-zA-Z]:/;
const CONTROL_CHARACTERS = /[\x00-\x1f\x7f]/;

export function normalizeChildName(value, maxLength = 100) {
  if (typeof value !== 'string') return null;
  const name = value.trim();
  if (!name || name.length > maxLength || name === '.' || name === '..' ||
      name.includes('/') || name.includes('\\') || /[<>:"|?*]/.test(name) ||
      name.endsWith('.') || CONTROL_CHARACTERS.test(name) ||
      /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?$/i.test(name)) return null;
  return name;
}

export function normalizeUploadId(value) {
  return typeof value === 'string' && /^[A-Za-z0-9_-]{16,128}$/.test(value) ? value : null;
}

export function isPathInside(root, candidate, allowRoot = false) {
  const absoluteRoot = path.resolve(root);
  const absoluteCandidate = path.resolve(candidate);
  const relative = path.relative(absoluteRoot, absoluteCandidate);
  if (relative === '') return allowRoot;
  return relative !== '..' && !relative.startsWith('..' + path.sep) && !path.isAbsolute(relative);
}

export function resolveRelativeAssetPath(root, baseDirectory, relativeName) {
  if (typeof relativeName !== 'string' || !relativeName || CONTROL_CHARACTERS.test(relativeName)) return null;
  const normalized = relativeName.replace(/\\/g, '/');
  if (normalized.startsWith('/') || path.posix.isAbsolute(normalized) ||
      path.win32.isAbsolute(normalized) || WINDOWS_DRIVE_PREFIX.test(normalized)) return null;
  const candidate = path.resolve(baseDirectory, ...normalized.split('/'));
  return isPathInside(root, candidate) ? candidate : null;
}

export function validateZipEntryPath(rawName) {
  if (typeof rawName !== 'string' || !rawName || CONTROL_CHARACTERS.test(rawName)) return null;
  const normalized = rawName.replace(/\\/g, '/');
  if (normalized.startsWith('/') || path.posix.isAbsolute(normalized) ||
      path.win32.isAbsolute(normalized) || WINDOWS_DRIVE_PREFIX.test(normalized)) return null;
  const isDirectory = normalized.endsWith('/');
  const segments = normalized.split('/');
  if (segments.some((segment) => segment === '..')) return null;
  const safeSegments = segments.filter((segment) => segment && segment !== '.');
  if (!safeSegments.length) return null;
  return safeSegments.join('/') + (isDirectory ? '/' : '');
}

export function normalizeRequestHostname(hostHeader) {
  const value = String(hostHeader || '').trim().toLowerCase();
  if (value.startsWith('[')) {
    const closingBracket = value.indexOf(']');
    return closingBracket > 0 ? value.slice(1, closingBracket) : '';
  }
  return value.replace(/:\\d+$/, '');
}

export function isAllowedRequestOrigin(origin, port, extraOrigins = [], allowNullOrigin = false) {
  if (!origin) return true;
  if (origin === 'null') return allowNullOrigin;
  const allowed = new Set([
    `http://localhost:${port}`,
    `http://127.0.0.1:${port}`,
    `http://[::1]:${port}`,
    ...extraOrigins
  ]);
  return allowed.has(origin);
}
