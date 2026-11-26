import fs from 'node:fs';
import fsp from 'node:fs/promises';
import path from 'node:path';
import { isPathInside, isRealPathInside } from './voicebank_security.js';

const HIRAGANA_CV = new Set(Array.from(
  'あいうえおかきくけこさしすせそたちつてとなにぬねの' +
  'はひふへほまみむめもやゆよらりるれろわをん' +
  'がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽ' +
  'ぁぃぅぇぉゃゅょゔっ'
));

const KANA_TO_ROMAJI = {
  'あ':'a','い':'i','う':'u','え':'e','お':'o',
  'か':'ka','き':'ki','く':'ku','け':'ke','こ':'ko',
  'さ':'sa','し':'shi','す':'su','せ':'se','そ':'so',
  'た':'ta','ち':'chi','つ':'tsu','て':'te','と':'to',
  'な':'na','に':'ni','ぬ':'nu','ね':'ne','の':'no',
  'は':'ha','ひ':'hi','ふ':'fu','へ':'he','ほ':'ho',
  'ま':'ma','み':'mi','む':'mu','め':'me','も':'mo',
  'や':'ya','ゆ':'yu','よ':'yo',
  'ら':'ra','り':'ri','る':'ru','れ':'re','ろ':'ro',
  'わ':'wa','を':'wo','ん':'n',
  'が':'ga','ぎ':'gi','ぐ':'gu','げ':'ge','ご':'go',
  'ざ':'za','じ':'ji','ず':'zu','ぜ':'ze','ぞ':'zo',
  'だ':'da','ぢ':'di','づ':'du','で':'de','ど':'do',
  'ば':'ba','び':'bi','ぶ':'bu','べ':'be','ぼ':'bo',
  'ぱ':'pa','ぴ':'pi','ぷ':'pu','ぺ':'pe','ぽ':'po'
};
const ROMAJI_TO_KANA = Object.fromEntries(
  Object.entries(KANA_TO_ROMAJI).map(([kana, romaji]) => [romaji, kana])
);

const MAX_BASENAME_SEARCH_ENTRIES = 200000;

function compareNames(a, b) {
  const left = String(a).normalize('NFKC').toLowerCase();
  const right = String(b).normalize('NFKC').toLowerCase();
  return left < right ? -1 : left > right ? 1 : String(a).localeCompare(String(b));
}

function decodeTextBuffer(buffer) {
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(buffer);
  } catch {
    try {
      return new TextDecoder('shift-jis').decode(buffer);
    } catch {
      return Buffer.from(buffer).toString('utf8');
    }
  }
}

function normalizeName(value) {
  return String(value || '').normalize('NFKC').toLowerCase();
}

function stripSupplementSuffix(name) {
  const normalized = String(name || '').normalize('NFKC').trim();
  return normalized.replace(/[ ._-]*(?:エクストラ|extra|append)$/i, '').trim() || normalized;
}

async function readDirectory(directory) {
  try {
    return (await fsp.readdir(directory, { withFileTypes: true }))
      .sort((a, b) => compareNames(a.name, b.name));
  } catch {
    return [];
  }
}

async function containsRootMarker(directory) {
  const entries = await readDirectory(directory);
  const names = new Set(entries.map((entry) => entry.name.toLowerCase()));
  return names.has('character.txt') || names.has('readme.txt');
}

export async function findVoicebankRoot(start, managedVoicebanksRoot, maxUp = 3) {
  const selected = path.resolve(start);
  const managedRoot = path.resolve(managedVoicebanksRoot);
  let current = selected;

  for (let depth = 0; depth <= Math.max(0, Number(maxUp) || 0); depth += 1) {
    if (!isPathInside(managedRoot, current)) break;
    if (await containsRootMarker(current)) return current;
    if (path.resolve(current) === managedRoot) break;
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }

  if (isPathInside(managedRoot, selected)) {
    const relative = path.relative(managedRoot, selected);
    if (!relative || relative === '.') return managedRoot;
    const firstChild = relative.split(path.sep)[0];
    return path.join(managedRoot, firstChild);
  }
  return selected;
}

async function hasOtoIni(directory) {
  const stack = [directory];
  while (stack.length) {
    const current = stack.pop();
    const entries = await readDirectory(current);
    for (const entry of entries) {
      const fullPath = path.join(current, entry.name);
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) {
        stack.push(fullPath);
      } else if (entry.isFile() && entry.name.toLowerCase() === 'oto.ini') {
        return true;
      }
    }
  }
  return false;
}

async function findCompanionVoicebankRoots(root, managedVoicebanksRoot) {
  const rootPath = path.resolve(root);
  const managedRoot = path.resolve(managedVoicebanksRoot);
  if (path.dirname(rootPath) !== managedRoot) return [];

  const rootName = path.basename(rootPath);
  const baseName = stripSupplementSuffix(rootName);
  const normalizedRootName = normalizeName(rootName);
  const isSupplement = normalizeName(baseName) !== normalizedRootName;
  const entries = await readDirectory(managedRoot);
  const candidates = [];

  if (isSupplement) {
    const match = entries.find((entry) =>
      entry.isDirectory() && normalizeName(entry.name) === normalizeName(baseName)
    );
    if (match) candidates.push(path.join(managedRoot, match.name));
  } else {
    for (const entry of entries) {
      if (!entry.isDirectory() || entry.name === rootName) continue;
      const siblingBase = stripSupplementSuffix(entry.name);
      if (
        normalizeName(siblingBase) === normalizedRootName &&
        normalizeName(siblingBase) !== normalizeName(entry.name)
      ) {
        candidates.push(path.join(managedRoot, entry.name));
      }
    }
  }

  const result = [];
  for (const candidate of candidates) {
    if (await hasOtoIni(candidate)) result.push(candidate);
  }
  return result;
}

function katakanaToHiragana(value) {
  return Array.from(value).map((char) => {
    const code = char.codePointAt(0);
    return code >= 0x30a1 && code <= 0x30f6
      ? String.fromCodePoint(code - 0x60)
      : char;
  }).join('');
}

function extractCoveredMora(aliasValue) {
  let alias = String(aliasValue || '').normalize('NFKC').trim();
  alias = alias.replace(/^(?:[-_]\s*|[aieuon](?:\s+|_))/i, '');
  alias = alias.replace(/_?[A-Ga-g][#b]?-?\d+$/i, '').trim();
  if (alias.includes('/') || alias.includes('\\')) {
    alias = alias.split(/[\\/]/).pop() || alias;
  }
  const tokens = alias.split(/\s+/);
  let mora = tokens.length ? tokens[tokens.length - 1] : alias;
  mora = katakanaToHiragana(mora);
  return ROMAJI_TO_KANA[mora.toLowerCase()] || mora;
}

function layerCoverageFromText(text) {
  const covered = new Set();
  for (const rawLine of String(text || '').split(/\r?\n/)) {
    if (!rawLine.includes('=')) continue;
    const separator = rawLine.indexOf('=');
    const filename = rawLine.slice(0, separator);
    const params = rawLine.slice(separator + 1);
    const fields = params.split(',');
    const rawAlias = (fields[0] || '').trim() || path.basename(filename.trim()).replace(/\.wav$/i, '');
    const mora = extractCoveredMora(rawAlias);
    if (HIRAGANA_CV.has(mora)) covered.add(mora);
  }
  return { covered, coverage: covered.size / Math.max(HIRAGANA_CV.size, 1) };
}

function isSupplementLayer(directory, root, coverage) {
  const relative = path.relative(root, directory);
  const parts = [path.basename(root), ...relative.split(path.sep).filter(Boolean)];
  const marked = parts.some((part) => {
    const lower = normalizeName(part);
    return lower.includes('エクストラ') || lower.includes('extra') || lower.includes('append');
  });
  if (marked) return { supplement: true, reason: 'name' };
  if (coverage < 0.55) return { supplement: true, reason: 'low_cv_coverage' };
  return { supplement: false, reason: '' };
}

async function findOtoIniLayers(root, scannedDirectories = []) {
  const found = [];
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    const entries = await readDirectory(current);
    scannedDirectories.push(path.resolve(current));
    const directories = entries.filter((entry) => entry.isDirectory() && !entry.isSymbolicLink());
    for (let index = directories.length - 1; index >= 0; index -= 1) {
      stack.push(path.join(current, directories[index].name));
    }
    const otoFiles = entries.filter((entry) => entry.isFile() && entry.name.toLowerCase() === 'oto.ini');
    if (otoFiles.length) {
      found.push({
        directory: path.resolve(current),
        iniPath: path.join(current, otoFiles[0].name),
        root: path.resolve(root)
      });
    }
  }
  return found;
}

async function findRootPrefixMapPath(rootDir) {
  const entries = await readDirectory(rootDir);
  const matches = entries.filter((entry) =>
    entry.isFile() && entry.name.toLowerCase() === 'prefix.map'
  );
  return matches.length ? path.join(rootDir, matches[0].name) : null;
}

export async function collectVoicebankResolutionPlan(selectedDirectory, managedVoicebanksRoot) {
  const selected = path.resolve(selectedDirectory);
  const managedRoot = path.resolve(managedVoicebanksRoot);
  const scanRoot = await findVoicebankRoot(selected, managedRoot);
  const rootSpecs = [scanRoot];
  const companions = await findCompanionVoicebankRoots(scanRoot, managedRoot);
  for (const companion of companions) {
    if (!rootSpecs.some((root) => path.resolve(root) === path.resolve(companion))) {
      rootSpecs.push(companion);
    }
  }

  const discovered = [];
  const scannedDirectories = [];
  const seenRoots = new Set();
  for (const layerRoot of rootSpecs) {
    let realRoot;
    try {
      realRoot = await fsp.realpath(layerRoot);
    } catch {
      continue;
    }
    if (seenRoots.has(realRoot)) continue;
    seenRoots.add(realRoot);
    discovered.push(...await findOtoIniLayers(layerRoot, scannedDirectories));
  }

  const selectedHasLayer = discovered.some((item) =>
    path.resolve(item.directory) === selected
  );
  const layerInfo = [];
  for (const item of discovered) {
    let text = '';
    try {
      text = decodeTextBuffer(await fsp.readFile(item.iniPath));
    } catch {}
    const { coverage } = layerCoverageFromText(text);
    const { supplement, reason } = isSupplementLayer(item.directory, item.root, coverage);
    const selectedSubtree = path.resolve(item.directory) === selected ||
      (!selectedHasLayer && isPathInside(selected, item.directory));
    const exactSelected = path.resolve(item.directory) === selected;
    const relativeDepth = path.relative(item.root, item.directory).split(path.sep).length;
    layerInfo.push({
      ...item,
      coverage,
      supplement,
      supplementReason: reason,
      selected: exactSelected,
      selectedSubtree,
      relativeDepth
    });
  }

  const priority = (item) => [
    item.selected ? 0 : item.selectedSubtree ? 1 : 2,
    item.relativeDepth,
    normalizeName(item.directory)
  ];
  const comparePriority = (left, right) => {
    const a = priority(left);
    const b = priority(right);
    return a[0] - b[0] || a[1] - b[1] || (a[2] < b[2] ? -1 : a[2] > b[2] ? 1 : 0);
  };
  const mainLayers = layerInfo.filter((item) => !item.supplement).sort(comparePriority);
  const supplementLayers = layerInfo.filter((item) => item.supplement).sort(comparePriority);
  const orderedLayers = [...mainLayers, ...supplementLayers];

  const voicebankRoots = [];
  for (const item of orderedLayers) {
    if (!voicebankRoots.includes(item.root)) voicebankRoots.push(item.root);
  }
  if (!voicebankRoots.length) voicebankRoots.push(...rootSpecs);

  const prefixRoots = [];
  for (const item of mainLayers) {
    if (!prefixRoots.includes(item.root)) prefixRoots.push(item.root);
  }
  for (const root of [scanRoot, selected]) {
    if (!prefixRoots.includes(root)) prefixRoots.push(root);
  }
  let prefixMapPath = null;
  for (const root of prefixRoots) {
    prefixMapPath = await findRootPrefixMapPath(root);
    if (prefixMapPath) break;
  }

  const signatureEntries = [];
  const signaturePaths = new Set([managedRoot, scanRoot, ...rootSpecs, ...scannedDirectories]);
  for (const item of orderedLayers) signaturePaths.add(item.iniPath);
  if (prefixMapPath) signaturePaths.add(prefixMapPath);
  for (const sourcePath of signaturePaths) {
    try {
      const stats = await fsp.stat(sourcePath);
      signatureEntries.push([
        path.resolve(sourcePath),
        stats.mtimeMs,
        stats.size
      ]);
    } catch {}
  }
  signatureEntries.sort((a, b) => compareNames(a[0], b[0]));

  return {
    selectedDirectory: selected,
    scanRoot,
    rootSpecs,
    voicebankRoots,
    layerInfo: orderedLayers,
    layers: orderedLayers.map((item) => item.directory),
    prefixMapPath,
    sourceSignature: JSON.stringify(signatureEntries)
  };
}

function safeFileInsideRoots(candidate, roots) {
  for (const root of roots) {
    if (isPathInside(root, candidate) && isRealPathInside(root, candidate)) {
      try {
        if (fs.statSync(candidate).isFile()) return true;
      } catch {}
    }
  }
  return false;
}

function rootContainingPath(candidate, roots) {
  return roots.find((root) => isPathInside(root, candidate)) || null;
}

function resolveCaseInsensitivePath(startDirectory, relativePath, roots) {
  const components = relativePath.split(/[\\/]/).filter((part) => part && part !== '.');
  let current = path.resolve(startDirectory);

  for (const component of components) {
    if (component === '..') {
      const parent = path.dirname(current);
      if (!rootContainingPath(parent, roots) || !roots.some((root) => isRealPathInside(root, parent))) {
        return null;
      }
      current = parent;
      continue;
    }

    const containingRoot = rootContainingPath(current, roots);
    if (!containingRoot || !isRealPathInside(containingRoot, current)) return null;
    let entries;
    try {
      entries = fs.readdirSync(current, { withFileTypes: true }).sort((a, b) => compareNames(a.name, b.name));
    } catch {
      return null;
    }
    const match = entries.find((entry) => entry.name.toLowerCase() === component.toLowerCase());
    if (!match || match.isSymbolicLink()) return null;
    current = path.join(current, match.name);
  }

  return safeFileInsideRoots(current, roots) ? current : null;
}

function findUniqueBasename(basename, roots) {
  const matches = new Set();
  let visited = 0;
  let limitExceeded = false;

  const search = (root) => {
    const stack = [root];
    while (stack.length && matches.size < 2 && !limitExceeded) {
      const current = stack.pop();
      let entries;
      try {
        entries = fs.readdirSync(current, { withFileTypes: true }).sort((a, b) => compareNames(a.name, b.name));
      } catch {
        continue;
      }
      for (const entry of entries) {
        visited += 1;
        if (visited > MAX_BASENAME_SEARCH_ENTRIES) {
          limitExceeded = true;
          return;
        }
        if (entry.isSymbolicLink()) continue;
        const fullPath = path.join(current, entry.name);
        if (entry.isDirectory()) {
          if (isRealPathInside(root, fullPath)) stack.push(fullPath);
        } else if (entry.isFile() && entry.name.toLowerCase() === basename.toLowerCase()) {
          if (safeFileInsideRoots(fullPath, roots)) matches.add(path.resolve(fullPath));
          if (matches.size >= 2) return;
        }
      }
    }
  };

  for (const root of roots) {
    if (matches.size >= 2 || limitExceeded) break;
    search(root);
  }
  if (limitExceeded || matches.size !== 1) return null;
  return [...matches][0];
}

export function resolveVoicebankWavPath(otoDirectory, filename, voicebankRoots) {
  if (typeof filename !== 'string' || !filename.trim()) return null;
  const roots = [...new Set((Array.isArray(voicebankRoots) ? voicebankRoots : [voicebankRoots])
    .filter(Boolean).map((root) => path.resolve(root)))];
  if (!roots.length) return null;

  const normalized = filename.replace(/\\/g, '/');
  const isWindowsAbsolute = /^[a-z]:\//i.test(normalized);
  const isAbsolute = path.isAbsolute(normalized) || isWindowsAbsolute;

  if (isAbsolute && !isWindowsAbsolute && safeFileInsideRoots(path.resolve(normalized), roots)) {
    return path.resolve(normalized);
  }
  if (!isAbsolute) {
    const exact = path.resolve(otoDirectory, normalized);
    if (safeFileInsideRoots(exact, roots)) return exact;
    const caseInsensitive = resolveCaseInsensitivePath(otoDirectory, normalized, roots);
    if (caseInsensitive) return caseInsensitive;
  } else if (!isWindowsAbsolute) {
    const candidate = path.resolve(normalized);
    const containingRoot = rootContainingPath(candidate, roots);
    if (containingRoot) {
      const caseInsensitive = resolveCaseInsensitivePath(
        containingRoot,
        path.relative(containingRoot, candidate),
        roots
      );
      if (caseInsensitive) return caseInsensitive;
    }
  }

  const basename = path.posix.basename(normalized);
  const candidates = [basename];
  if (!basename.toLowerCase().endsWith('.wav')) candidates.push(basename + '.wav');
  for (const candidate of candidates) {
    const resolved = findUniqueBasename(candidate, roots);
    if (resolved) return resolved;
  }
  return null;
}

export function isRealPathInsideVoicebankRoots(candidate, roots) {
  return (Array.isArray(roots) ? roots : [roots]).some((root) =>
    root && isRealPathInside(root, candidate)
  );
}
