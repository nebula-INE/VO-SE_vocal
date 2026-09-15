// Project Format Converter for Vocal Synthesizers
// Supports: UST (.ust), VOCALOID3/4 (.vsqx), Synthesizer V (.svp), Standard MIDI (.mid)

import { parsePitchBend, serializePitchBend, PitchPoint } from './pitchCurve';

export interface Note {
  id: string;
  lyric: string;
  noteNum: number; // MIDI 36-96
  tick: number; // 480 ticks per beat
  length: number; // in ticks
  intensity: number; // 0-150
  flags: string;
  pbs: string;
  pbw: string;
  pby: string;
}

export interface ProjectData {
  projectName: string;
  tempo: number;
  voicebank?: string;
  notes: Note[];
}

// Japanese Hiragana/Katakana to VOCALOID/SVP Phoneme Mapping
const KANA_TO_PHONEME: Record<string, string> = {
  'あ': 'a', 'い': 'i', 'う': 'u', 'え': 'e', 'お': 'o',
  'か': 'k a', 'き': 'k i', 'く': 'k u', 'け': 'k e', 'こ': 'k o',
  'さ': 's a', 'し': 's i', 'す': 's u', 'せ': 's e', 'そ': 's o',
  'た': 't a', 'ち': 'tS i', 'つ': 'ts u', 'て': 't e', 'と': 't o',
  'な': 'n a', 'に': 'n i', 'ぬ': 'n u', 'ね': 'n e', 'の': 'n o',
  'は': 'h a', 'ひ': 'h i', 'ふ': 'p\\ u', 'へ': 'h e', 'ほ': 'h o',
  'ま': 'm a', 'み': 'm i', 'む': 'm u', 'め': 'm e', 'も': 'm o',
  'や': 'j a', 'ゆ': 'j u', 'よ': 'j o',
  'ら': 'r a', 'り': 'r i', 'る': 'r u', 'れ': 'r e', 'ろ': 'r o',
  'わ': 'w a', 'を': 'o', 'ん': 'N',
  'が': 'g a', 'ぎ': 'g i', 'ぐ': 'g u', 'げ': 'g e', 'ご': 'g o',
  'ざ': 'z a', 'じ': 'dZ i', 'ず': 'z u', 'ぜ': 'z e', 'ぞ': 'z o',
  'だ': 'd a', 'ぢ': 'dZ i', 'づ': 'z u', 'で': 'd e', 'ど': 'd o',
  'ば': 'b a', 'び': 'b i', 'ぶ': 'b u', 'べ': 'b e', 'ぼ': 'b o',
  'ぱ': 'p a', 'ぴ': 'p i', 'ぷ': 'p u', 'ぺ': 'p e', 'ぽ': 'p o',
  'きゃ': 'k' + " '" + 'a', 'きゅ': 'k' + " '" + 'u', 'きょ': 'k' + " '" + 'o',
  'しゃ': 'S a', 'しゅ': 'S u', 'しょ': 'S o',
  'ちゃ': 'tS a', 'ちゅ': 'tS u', 'ちょ': 'tS o',
  'にゃ': 'n' + " '" + 'a', 'にゅ': 'n' + " '" + 'u', 'にょ': 'n' + " '" + 'o',
  'ひゃ': 'h' + " '" + 'a', 'ひゅ': 'h' + " '" + 'u', 'ひょ': 'h' + " '" + 'o',
  'みゃ': 'm' + " '" + 'a', 'みゅ': 'm' + " '" + 'u', 'みょ': 'm' + " '" + 'o',
  'りゃ': 'r' + " '" + 'a', 'りゅ': 'r' + " '" + 'u', 'りょ': 'r' + " '" + 'o',
  'ぎゃ': 'g' + " '" + 'a', 'ぎゅ': 'g' + " '" + 'u', 'ぎょ': 'g' + " '" + 'o',
  'じゃ': 'dZ a', 'じゅ': 'dZ u', 'じょ': 'dZ o', 'びゃ': 'b' + " '" + 'a', 'びゅ': 'b' + " '" + 'u', 'びょ': 'b' + " '" + 'o',
  'ぴゃ': 'p' + " '" + 'a', 'ぴゅ': 'p' + " '" + 'u', 'ぴょ': 'p' + " '" + 'o'
};

export function lyricToPhoneme(lyric: string): string {
  if (KANA_TO_PHONEME[lyric]) return KANA_TO_PHONEME[lyric];
  return lyric || 'a';
}

// ----------------------------------------------------------------------
// Universal Text & Encoding Decoder (UTF-8, Shift_JIS/CP932, UTF-16, etc.)
// ----------------------------------------------------------------------
export function decodeTextBuffer(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  if (bytes.length === 0) return '';

  // 1. Check for BOM signatures
  if (bytes.length >= 3 && bytes[0] === 0xEF && bytes[1] === 0xBB && bytes[2] === 0xBF) {
    return new TextDecoder('utf-8').decode(bytes.subarray(3));
  }
  if (bytes.length >= 2 && bytes[0] === 0xFF && bytes[1] === 0xFE) {
    return new TextDecoder('utf-16le').decode(bytes.subarray(2));
  }
  if (bytes.length >= 2 && bytes[0] === 0xFE && bytes[1] === 0xFF) {
    return new TextDecoder('utf-16be').decode(bytes.subarray(2));
  }

  // 2. Try strict UTF-8 decoding
  try {
    const utf8Decoder = new TextDecoder('utf-8', { fatal: true });
    const decoded = utf8Decoder.decode(bytes);
    // If it decoded strictly without error and contains no replacement chars, it's valid UTF-8
    if (!decoded.includes('\uFFFD')) {
      return decoded;
    }
  } catch (e) {
    // Strict UTF-8 threw an error -> file contains non-UTF-8 bytes (Shift_JIS / CP932)
  }

  // 3. Fallback to Shift-JIS (Standard encoding for Japanese UTAU .ust files)
  try {
    const sjisDecoder = new TextDecoder('shift-jis');
    const decoded = sjisDecoder.decode(bytes);
    return decoded;
  } catch (e) {
    // 4. Final fallback to regular UTF-8
    return new TextDecoder('utf-8').decode(bytes);
  }
}

// Helper: Convert pitch note string (e.g. "60", "C4", "A#4", "Db5") to MIDI number
export function parsePitchToMidi(val: string | number): number {
  if (typeof val === 'number') return Math.round(val);
  const str = String(val).trim();
  if (!str) return 60;

  // Pure integer or float (e.g. "60" or "60.0")
  const num = parseFloat(str);
  if (!isNaN(num) && /^-?\d+(\.\d+)?$/.test(str)) {
    return Math.round(num);
  }

  // Tone name e.g. "C4", "C#4", "Db4", "A3", "F#5", "Eb2"
  const match = str.match(/^([A-Ga-g])([#b♯♭]?)(-?\d+)$/);
  if (match) {
    const noteLetter = match[1].toUpperCase();
    const accidental = match[2];
    const octave = parseInt(match[3], 10);

    const baseNotes: Record<string, number> = {
      'C': 0, 'D': 2, 'E': 4, 'F': 5, 'G': 7, 'A': 9, 'B': 11
    };

    let semitone = baseNotes[noteLetter] ?? 0;
    if (accidental === '#' || accidental === '♯') semitone += 1;
    if (accidental === 'b' || accidental === '♭') semitone -= 1;

    // MIDI C4 = 60 ((4 + 1) * 12 + 0)
    const midi = (octave + 1) * 12 + semitone;
    return Math.max(0, Math.min(127, midi));
  }

  return 60;
}

// ----------------------------------------------------------------------
// 1. UST (.ust) Parser & Exporter
// ----------------------------------------------------------------------

export function parseUstText(text: string): ProjectData {
  // Strip UTF-8 BOM if present and normalize newlines
  const cleanText = text.replace(/^\uFEFF/, '');
  const lines = cleanText.split(/\r?\n|\r/);

  let tempo = 120;
  let projectName = 'UTAU Project';
  let voicebank = '';
  const notes: Note[] = [];

  let currentTick = 0;
  let inSetting = false;
  let inNote = false;

  let curLength = 480;
  let curLyric = 'あ';
  let curNoteNum = 60;
  let curIntensity = 120;
  let curFlags = '';
  let curPbs = '0;0';
  let curPbw = '50';
  let curPby = '0';
  let curPitches = '';

  const pushCurrentNote = () => {
    if (!inNote) return;

    const trimmedLyric = curLyric.trim();

    // UTAUの「タイ（継続）ノート」記法: Lyric="+" は新しい音素を発音せず、
    // 直前のノートの発音をそのまま継続する（ピッチカーブだけを追加で乗せることが多い）。
    // これを普通の歌詞として音源解決しようとすると、音源には存在しないエイリアスなので
    // 必ず失敗し、その区間が余計な無音になってしまっていた。
    // 正しくは: 新規ノートを作らず、直前のノートの長さを延長し、
    // このノート自身のPBS/PBW/PBYを直前ノートの続きとして結合する。
    if (trimmedLyric === '+') {
      if (notes.length > 0) {
        const prevNote = notes[notes.length - 1];
        const prevLengthMs = (prevNote.length / 480) * (60000 / (tempo || 120));

        if (curPbs || curPbw || curPby) {
          const prevPoints = parsePitchBend(prevNote.pbs, prevNote.pbw, prevNote.pby);
          const tiePoints = parsePitchBend(curPbs, curPbw, curPby).map((p) => ({
            offsetMs: p.offsetMs + prevLengthMs,
            semitone: p.semitone
          }));
          // タイノートのPBSは直前ノートの領域に食い込む負のオフセットを持つことがある
          // (滑らかなポルタメントとして正しい挙動)ため、結合後は時系列順に並べ直す。
          const mergedPoints = [...prevPoints, ...tiePoints].sort((a, b) => a.offsetMs - b.offsetMs);
          const merged = serializePitchBend(mergedPoints);
          prevNote.pbs = merged.pbs;
          prevNote.pbw = merged.pbw;
          prevNote.pby = merged.pby;
        }
        prevNote.length = Math.max(1, prevNote.length) + Math.max(1, curLength);
      }
      currentTick += Math.max(1, curLength);
      return;
    }

    // In UTAU, Rest and Breath notes are strictly R, r, 休符, br, 息, 吸, pau, sil, etc.
    const lLower = trimmedLyric.toLowerCase();
    const isRest = (
      lLower === 'r' ||
      lLower === 'r_' ||
      lLower === 'r_0' ||
      lLower === '[r]' ||
      lLower === '休' ||
      lLower === '休符' ||
      lLower === 'null' ||
      lLower === '' ||
      lLower === 'pau' ||
      lLower === 'sil' ||
      lLower === 'br' ||
      lLower === '息' ||
      lLower === '吸' ||
      lLower === '吸気' ||
      lLower === '息吸い' ||
      /^br[0-9]*$/i.test(lLower) ||
      /^息[0-9]*$/i.test(lLower) ||
      /^吸[0-9]*$/i.test(lLower) ||
      /^_?(br|息|吸)[0-9]*$/i.test(lLower)
    );

    if (!isRest) {
      let finalPbs = curPbs;
      let finalPbw = curPbw;
      let finalPby = curPby;

      // Mode 1 Pitches / PitchBend fallback if Mode 2 PBS/PBW/PBY is empty or default
      if ((!curPbs || curPbs === '0;0') && (!curPby || curPby === '0') && curPitches) {
        const pitchList = curPitches.split(',').map((s) => parseFloat(s.trim())).filter((n) => !isNaN(n));
        if (pitchList.length > 1) {
          // UST/UTAU標準仕様: 単位は常に固定で「10 = 1半音」。
          // (以前はノート内の最大絶対値でセント/0.1半音/実半音を"自動判定"していたが、
          //  これは pitchCurve.ts と同じ根本原因のバグ。ベンドの深さと単位は無関係)
          const factor = 0.1;
          const stepMs = Math.max(10, Math.round(((curLength / 480) * (60000 / (tempo || 120))) / pitchList.length));
          const stepSample = Math.max(1, Math.floor(pitchList.length / 6));
          const pts: PitchPoint[] = [];
          let curMs = 0;
          for (let idx = 0; idx < pitchList.length; idx += stepSample) {
            pts.push({ offsetMs: curMs, semitone: Math.max(-24, Math.min(24, pitchList[idx] * factor)) });
            curMs += stepMs * stepSample;
          }
          if (pts.length > 0) {
            const serialized = serializePitchBend(pts);
            finalPbs = serialized.pbs;
            finalPbw = serialized.pbw;
            finalPby = serialized.pby;
          }
        }
      }

      // Automatically calibrate and normalize through parsePitchBend & serializePitchBend to ensure safe bounds
      if (finalPbs || finalPbw || finalPby) {
        const parsedPoints = parsePitchBend(finalPbs, finalPbw, finalPby);
        const normalized = serializePitchBend(parsedPoints);
        finalPbs = normalized.pbs;
        finalPbw = normalized.pbw;
        finalPby = normalized.pby;
      }

      notes.push({
        id: `ust_${notes.length + 1}`,
        lyric: trimmedLyric || 'あ',
        noteNum: Math.min(108, Math.max(24, curNoteNum)),
        tick: currentTick,
        length: Math.max(1, curLength),
        intensity: Math.min(200, Math.max(0, curIntensity)),
        flags: curFlags,
        pbs: finalPbs || '0;0',
        pbw: finalPbw || '50',
        pby: finalPby || '0'
      });
    }

    currentTick += Math.max(1, curLength);
  };

  for (let rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;

    // Check section header: [#...]
    const sectionMatch = line.match(/^\[#([A-Za-z0-9_]+)\]$/i);
    if (sectionMatch) {
      const sectionName = sectionMatch[1].toUpperCase();

      if (sectionName === 'SETTING') {
        pushCurrentNote();
        inSetting = true;
        inNote = false;
        continue;
      }

      if (sectionName === 'VERSION' || sectionName.startsWith('VERSION')) {
        pushCurrentNote();
        inSetting = false;
        inNote = false;
        continue;
      }

      if (sectionName === 'TRACKEND') {
        pushCurrentNote();
        inSetting = false;
        inNote = false;
        continue;
      }

      if (sectionName === 'PREV' || sectionName === 'NEXT' || sectionName === 'INSERT' || sectionName === 'DELETE') {
        // Skip auxiliary plugin context sections
        pushCurrentNote();
        inSetting = false;
        inNote = false;
        continue;
      }

      // Valid note section (e.g. [#0000], [#0001], [#0], [#1], etc.)
      pushCurrentNote();
      inSetting = false;
      inNote = true;

      // Reset defaults for this note
      curLength = 480;
      curLyric = 'あ';
      curNoteNum = 60;
      curIntensity = 120;
      curFlags = '';
      curPbs = '0;0';
      curPbw = '50';
      curPby = '0';
      curPitches = '';
      continue;
    }

    const eqIdx = line.indexOf('=');
    if (eqIdx === -1) continue;
    const key = line.substring(0, eqIdx).trim().toLowerCase();
    const val = line.substring(eqIdx + 1).trim();

    if (inSetting) {
      if (key === 'tempo') {
        const parsedTempo = parseFloat(val);
        if (!isNaN(parsedTempo) && parsedTempo > 0) tempo = parsedTempo;
      } else if (key === 'projectname' || key === 'project') {
        projectName = val;
      } else if (key === 'voicedir' || key === 'voicebank') {
        // Clean Windows path if present e.g. "C:\UTAU\voice\defoko" -> "defoko"
        const cleaned = val.replace(/\\/g, '/').split('/').filter(Boolean).pop() || val;
        voicebank = cleaned.replace(/%VOICE%/gi, '').trim();
      }
    } else if (inNote) {
      switch (key) {
        case 'length':
          curLength = Math.round(parseFloat(val)) || 480;
          break;
        case 'lyric':
          curLyric = val.replace(/^["']|["']$/g, '');
          break;
        case 'notenum':
        case 'tone':
        case 'key':
          curNoteNum = parsePitchToMidi(val);
          break;
        case 'intensity':
        case 'velocity':
          curIntensity = Math.round(parseFloat(val)) || 120;
          break;
        case 'flags':
          curFlags = val;
          break;
        case 'pbs':
        case 'pbstart':
          curPbs = val;
          break;
        case 'pbw':
          curPbw = val;
          break;
        case 'pby':
          curPby = val;
          break;
        case 'pitches':
        case 'pitchbend':
          curPitches = val;
          break;
        case 'tempo': {
          // If tempo specified on note and setting didn't have custom tempo
          const parsed = parseFloat(val);
          if (!isNaN(parsed) && parsed > 0 && tempo === 120) {
            tempo = parsed;
          }
          break;
        }
      }
    } else {
      // Global key-value pair outside sections
      if (key === 'tempo') {
        const parsed = parseFloat(val);
        if (!isNaN(parsed) && parsed > 0) tempo = parsed;
      } else if (key === 'projectname') {
        projectName = val;
      } else if (key === 'voicedir' || key === 'voicebank') {
        const cleaned = val.replace(/\\/g, '/').split('/').filter(Boolean).pop() || val;
        voicebank = cleaned.replace(/%VOICE%/gi, '').trim();
      }
    }
  }

  // Push the final note if file did not have [#TRACKEND]
  pushCurrentNote();

  return { projectName, tempo, voicebank, notes };
}

export function exportUstText(project: ProjectData): string {
  let ust = `[#VERSION]\nUST Version 1.2\n[#SETTING]\nTempo=${project.tempo.toFixed(2)}\nProjectName=${project.projectName || 'VO-SE Song'}\n`;
  if (project.voicebank) ust += `Voicebank=${project.voicebank}\n`;

  let currentTick = 0;
  let sectionIndex = 0;

  const sortedNotes = [...project.notes].sort((a, b) => a.tick - b.tick);

  sortedNotes.forEach((n) => {
    // Gap before note -> insert Rest note 'R'
    if (n.tick > currentTick) {
      const gap = n.tick - currentTick;
      const padIdx = String(sectionIndex++).padStart(4, '0');
      ust += `[#${padIdx}]\nLength=${gap}\nLyric=R\nNoteNum=60\n`;
      currentTick = n.tick;
    }

    const padIdx = String(sectionIndex++).padStart(4, '0');
    ust += `[#${padIdx}]\nLength=${n.length}\nLyric=${n.lyric}\nNoteNum=${n.noteNum}\nIntensity=${Math.round(n.intensity || 120)}\nFlags=${n.flags || ''}\nPBS=${n.pbs || '0;0'}\nPBW=${n.pbw || '50'}\nPBY=${n.pby || '0'}\n`;
    currentTick = n.tick + n.length;
  });

  ust += `[#TRACKEND]\n`;
  return ust;
}

// ----------------------------------------------------------------------
// 2. VOCALOID 3/4 (.vsqx) Parser & Exporter
// ----------------------------------------------------------------------

export function parseVsqxXml(xmlString: string): ProjectData {
  const parser = new DOMParser();
  const xmlDoc = parser.parseFromString(xmlString, 'text/xml');

  let tempo = 120;
  let projectName = 'VOCALOID Project';
  const notes: Note[] = [];

  // Parse Master Tempo
  const bpmNode = xmlDoc.querySelector('masterTrack > tempo > bpm') || xmlDoc.querySelector('tempo > bpm');
  if (bpmNode && bpmNode.textContent) {
    const rawBpm = parseFloat(bpmNode.textContent);
    if (!isNaN(rawBpm)) {
      // VSQX stores BPM * 100 (e.g. 12000 = 120.00)
      tempo = rawBpm > 1000 ? rawBpm / 100 : rawBpm;
    }
  }

  // Parse Track Name
  const trackNameNode = xmlDoc.querySelector('vsTrack > name');
  if (trackNameNode && trackNameNode.textContent) {
    projectName = trackNameNode.textContent;
  }

  // Parse Notes
  const noteNodes = xmlDoc.querySelectorAll('note');
  noteNodes.forEach((nNode, idx) => {
    const posTickNode = nNode.querySelector('posTick');
    const durTickNode = nNode.querySelector('durTick');
    const noteNumNode = nNode.querySelector('noteNum');
    const lyricNode = nNode.querySelector('lyric');

    // Get parent musicalPart posTick if any
    const partNode = nNode.closest('musicalPart');
    let partOffset = 0;
    if (partNode) {
      const partPos = partNode.querySelector('posTick');
      if (partPos && partPos.textContent) {
        partOffset = parseInt(partPos.textContent, 10) || 0;
      }
    }

    const posTick = (parseInt(posTickNode?.textContent || '0', 10) || 0) + partOffset;
    const durTick = parseInt(durTickNode?.textContent || '480', 10) || 480;
    const noteNum = parseInt(noteNumNode?.textContent || '60', 10) || 60;
    const lyric = lyricNode?.textContent || 'あ';

    notes.push({
      id: `vsqx_${idx + 1}`,
      lyric,
      noteNum,
      tick: posTick,
      length: durTick,
      intensity: 120,
      flags: '',
      pbs: '0;0',
      pbw: '50',
      pby: '0'
    });
  });

  notes.sort((a, b) => a.tick - b.tick);

  return { projectName, tempo, notes };
}

export function exportVsqxXml(project: ProjectData): string {
  const vsqBpm = Math.round((project.tempo || 120) * 100);
  const totalTicks = project.notes.reduce((max, n) => Math.max(max, n.tick + n.length), 3840) + 1920;

  let notesXml = '';
  project.notes.forEach((n) => {
    const phn = lyricToPhoneme(n.lyric);
    notesXml += `
        <note>
          <posTick>${n.tick}</posTick>
          <durTick>${n.length}</durTick>
          <noteNum>${n.noteNum}</noteNum>
          <velocity>64</velocity>
          <lyric><![CDATA[${n.lyric}]]></lyric>
          <phn><![CDATA[${phn}]]></phn>
          <noteStyle>
            <attr id="accent">50</attr>
            <attr id="bendDep">8</attr>
            <attr id="bendLen">0</attr>
            <attr id="decay">50</attr>
            <attr id="fallPort">0</attr>
            <attr id="opening">127</attr>
            <attr id="risePort">0</attr>
            <attr id="vibLen">0</attr>
            <attr id="vibType">0</attr>
          </noteStyle>
        </note>`;
  });

  return `<?xml version="1.0" encoding="UTF-8" standalone="no"?>
<vsq4 xmlns="http://www.yamaha.co.jp/vocaloid/schema/vsq4/"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xsi:schemaLocation="http://www.yamaha.co.jp/vocaloid/schema/vsq4/ vsq4.xsd">
  <vender><![CDATA[Yamaha corporation]]></vender>
  <version><![CDATA[4.0.0.0]]></version>
  <stylePlugin>
    <stylePluginID><![CDATA[36015A33-875C-409B-8332-9D227C0E73B9]]></stylePluginID>
    <version><![CDATA[4.0.0.0]]></version>
    <stylePluginName><![CDATA[VOCALOID4 Default Style]]></stylePluginName>
  </stylePlugin>
  <masterTrack>
    <seqName><![CDATA[${project.projectName || 'VO-SE Song'}]]></seqName>
    <comment><![CDATA[Exported from VO-SE Vocal Studio]]></comment>
    <resolution>480</resolution>
    <timeSig><m>0</m><nu>4</nu><de>4</de></timeSig>
    <tempo>
      <tick>0</tick>
      <bpm>${vsqBpm}</bpm>
    </tempo>
  </masterTrack>
  <vsTrack>
    <tNo>0</tNo>
    <name><![CDATA[Vocal 1]]></name>
    <comment><![CDATA[Main Track]]></comment>

    <musicalPart>
      <posTick>0</posTick>
      <playTime>${totalTicks}</playTime>
      <name><![CDATA[Vocal Part]]></name>
      <comment><![CDATA[]]></comment>
      <stylePlugin>
        <stylePluginID><![CDATA[36015A33-875C-409B-8332-9D227C0E73B9]]></stylePluginID>
        <version><![CDATA[4.0.0.0]]></version>
        <stylePluginName><![CDATA[VOCALOID4 Default Style]]></stylePluginName>
      </stylePlugin>
      <partStyle>
        <attr id="accent">50</attr>
        <attr id="bendDep">8</attr>
        <attr id="bendLen">0</attr>
        <attr id="decay">50</attr>
        <attr id="fallPort">0</attr>
        <attr id="opening">127</attr>
        <attr id="risePort">0</attr>
        <attr id="vibLen">0</attr>
        <attr id="vibType">0</attr>
      </partStyle>
      <singer>
        <posTick>0</posTick>
        <bank>0</bank>
        <program>0</program>
      </singer>
      ${notesXml}
      <plane>0</plane>
    </musicalPart>
  </vsTrack>
</vsq4>`;
}

// ----------------------------------------------------------------------
// 3. Synthesizer V (.svp) Parser & Exporter
// ----------------------------------------------------------------------

const SVP_TICK_SCALE = 306250; // 147,000,000 bL per quarter note / 480 ticks = 306,250

export function parseSvpJson(jsonString: string): ProjectData {
  const data = JSON.parse(jsonString);

  let tempo = 120;
  let projectName = 'Synthesizer V Project';
  const notes: Note[] = [];

  // Tempo
  if (data.time?.tempo && Array.isArray(data.time.tempo) && data.time.tempo.length > 0) {
    tempo = parseFloat(data.time.tempo[0].bpm) || 120;
  }

  // Tracks & Notes
  const tracks = data.tracks || [];
  if (tracks.length > 0 && tracks[0].name) {
    projectName = tracks[0].name;
  }

  let noteCounter = 1;

  const processNoteList = (rawNotes: any[]) => {
    rawNotes.forEach((n) => {
      let onset = n.onset || 0;
      let duration = n.duration || 480;

      // Detect if onset/duration are in SVP bL resolution
      if (onset > 1000000 || duration > 1000000) {
        onset = Math.round(onset / SVP_TICK_SCALE);
        duration = Math.round(duration / SVP_TICK_SCALE);
      }

      notes.push({
        id: `svp_${noteCounter++}`,
        lyric: n.lyrics || n.lyric || 'あ',
        noteNum: typeof n.pitch === 'number' ? n.pitch : 60,
        tick: onset,
        length: duration,
        intensity: 120,
        flags: '',
        pbs: '0;0',
        pbw: '50',
        pby: '0'
      });
    });
  };

  tracks.forEach((track: any) => {
    if (track.mainGroup?.notes) {
      processNoteList(track.mainGroup.notes);
    } else if (track.groups) {
      track.groups.forEach((g: any) => {
        if (g.notes) processNoteList(g.notes);
      });
    }
  });

  notes.sort((a, b) => a.tick - b.tick);

  return { projectName, tempo, notes };
}

export function exportSvpJson(project: ProjectData): string {
  const svpNotes = project.notes.map((n) => ({
    onset: n.tick * SVP_TICK_SCALE,
    duration: n.length * SVP_TICK_SCALE,
    lyrics: n.lyric,
    phonemes: lyricToPhoneme(n.lyric),
    pitch: n.noteNum,
    attributes: {
      tF0Left: 0,
      tF0Right: 0,
      dF0Left: 0,
      dF0Right: 0
    }
  }));

  const svpData = {
    version: 1,
    time: {
      meter: [{ index: 0, numerator: 4, denominator: 4 }],
      tempo: [{ position: 0, bpm: project.tempo || 120 }]
    },
    library: [],
    tracks: [
      {
        name: project.projectName || 'Vocal Track 1',
        dispColor: 'ff5865f2',
        mainGroup: {
          name: 'main',
          parameters: {
            pitchDelta: { mode: 'linear', points: [] },
            vibratoEnv: { mode: 'linear', points: [] },
            loudness: { mode: 'linear', points: [] },
            tension: { mode: 'linear', points: [] },
            breathiness: { mode: 'linear', points: [] },
            voicing: { mode: 'linear', points: [] },
            gender: { mode: 'linear', points: [] }
          },
          notes: svpNotes
        }
      }
    ]
  };

  return JSON.stringify(svpData, null, 2);
}

// ----------------------------------------------------------------------
// 4. Standard MIDI (.mid) Parser & Exporter
// ----------------------------------------------------------------------

export function parseMidiBuffer(buffer: ArrayBuffer): ProjectData {
  const view = new DataView(buffer);
  let offset = 0;

  // Header chunk check "MThd"
  const headerStr = String.fromCharCode(view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3));
  if (headerStr !== 'MThd') {
    throw new Error('Invalid MIDI Header Chunk');
  }

  const headerLength = view.getUint32(4);
  const division = view.getUint16(12); // Ticks per quarter note (e.g., 480)
  const tickScale = 480 / (division || 480);

  offset = 8 + headerLength;

  let tempo = 120;
  let projectName = 'MIDI Import';
  const notes: Note[] = [];

  // Parse Track chunks "MTrk"
  while (offset < view.byteLength) {
    if (offset + 8 > view.byteLength) break;
    const chunkType = String.fromCharCode(
      view.getUint8(offset),
      view.getUint8(offset + 1),
      view.getUint8(offset + 2),
      view.getUint8(offset + 3)
    );
    const chunkLen = view.getUint32(offset + 4);
    offset += 8;

    if (chunkType === 'MTrk') {
      const trackEnd = offset + chunkLen;
      let currentTick = 0;
      let runningStatus = 0;
      const activeNotes = new Map<number, { tick: number; velocity: number }>();

      while (offset < trackEnd && offset < view.byteLength) {
        // Read Variable Length Quantity for Delta Time
        let delta = 0;
        let b = 0;
        do {
          b = view.getUint8(offset++);
          delta = (delta << 7) | (b & 0x7f);
        } while (b & 0x80);

        currentTick += Math.round(delta * tickScale);

        let status = view.getUint8(offset);
        if (status >= 0x80) {
          runningStatus = status;
          offset++;
        } else {
          status = runningStatus;
        }

        const msgType = status & 0xf0;

        if (status === 0xff) {
          // Meta Event
          const metaType = view.getUint8(offset++);
          let metaLen = 0;
          let mb = 0;
          do {
            mb = view.getUint8(offset++);
            metaLen = (metaLen << 7) | (mb & 0x7f);
          } while (mb & 0x80);

          if (metaType === 0x51 && metaLen === 3) {
            // Set Tempo (Microseconds per quarter note)
            const mpqn = (view.getUint8(offset) << 16) | (view.getUint8(offset + 1) << 8) | view.getUint8(offset + 2);
            if (mpqn > 0) tempo = Math.round(60000000 / mpqn);
          } else if (metaType === 0x03 && metaLen > 0) {
            // Track Name
            let nameStr = '';
            for (let i = 0; i < metaLen; i++) {
              nameStr += String.fromCharCode(view.getUint8(offset + i));
            }
            if (nameStr.trim()) projectName = nameStr.trim();
          }

          offset += metaLen;
        } else if (msgType === 0x90) {
          // Note On
          const noteNum = view.getUint8(offset++);
          const velocity = view.getUint8(offset++);
          if (velocity > 0) {
            activeNotes.set(noteNum, { tick: currentTick, velocity });
          } else {
            // Velocity 0 = Note Off
            const start = activeNotes.get(noteNum);
            if (start) {
              const len = Math.max(60, currentTick - start.tick);
              notes.push({
                id: `midi_${notes.length + 1}`,
                lyric: 'あ',
                noteNum,
                tick: start.tick,
                length: len,
                intensity: Math.round((start.velocity / 127) * 120),
                flags: '',
                pbs: '0;0',
                pbw: '50',
                pby: '0'
              });
              activeNotes.delete(noteNum);
            }
          }
        } else if (msgType === 0x80) {
          // Note Off
          const noteNum = view.getUint8(offset++);
          offset++; // Skip velocity
          const start = activeNotes.get(noteNum);
          if (start) {
            const len = Math.max(60, currentTick - start.tick);
            notes.push({
              id: `midi_${notes.length + 1}`,
              lyric: 'あ',
              noteNum,
              tick: start.tick,
              length: len,
              intensity: Math.round((start.velocity / 127) * 120),
              flags: '',
              pbs: '0;0',
              pbw: '50',
              pby: '0'
            });
            activeNotes.delete(noteNum);
          }
        } else if (msgType === 0xa0 || msgType === 0xb0 || msgType === 0xe0) {
          offset += 2;
        } else if (msgType === 0xc0 || msgType === 0xd0) {
          offset += 1;
        } else {
          // System Exclusive or unrecognized
          break;
        }
      }
    } else {
      offset += chunkLen;
    }
  }

  notes.sort((a, b) => a.tick - b.tick);

  return { projectName, tempo, notes };
}

export function exportMidiBuffer(project: ProjectData): ArrayBuffer {
  const events: { tick: number; type: 'on' | 'off'; noteNum: number; velocity: number }[] = [];

  project.notes.forEach((n) => {
    events.push({ tick: n.tick, type: 'on', noteNum: n.noteNum, velocity: 100 });
    events.push({ tick: n.tick + n.length, type: 'off', noteNum: n.noteNum, velocity: 0 });
  });

  events.sort((a, b) => a.tick - b.tick || (a.type === 'off' ? -1 : 1));

  // Build Track Chunk Bytes
  const trackBytes: number[] = [];

  // Write VLQ Delta Time
  const writeVlq = (value: number) => {
    const buffer = [];
    let v = value;
    buffer.push(v & 0x7f);
    while ((v >>= 7)) {
      buffer.push((v & 0x7f) | 0x80);
    }
    buffer.reverse();
    trackBytes.push(...buffer);
  };

  // Meta Event: Set Tempo
  const mpqn = Math.round(60000000 / (project.tempo || 120));
  trackBytes.push(0x00, 0xff, 0x51, 0x03);
  trackBytes.push((mpqn >> 16) & 0xff, (mpqn >> 8) & 0xff, mpqn & 0xff);

  let lastTick = 0;
  events.forEach((ev) => {
    const delta = Math.max(0, ev.tick - lastTick);
    writeVlq(delta);
    if (ev.type === 'on') {
      trackBytes.push(0x90, ev.noteNum, ev.velocity);
    } else {
      trackBytes.push(0x80, ev.noteNum, 0x00);
    }
    lastTick = ev.tick;
  });

  // Meta Event: End of Track
  writeVlq(0);
  trackBytes.push(0xff, 0x2f, 0x00);

  // Build Header + Track
  const totalByteLen = 14 + 8 + trackBytes.length;
  const buffer = new ArrayBuffer(totalByteLen);
  const view = new DataView(buffer);

  // Header "MThd"
  view.setUint8(0, 0x4d); // M
  view.setUint8(1, 0x54); // T
  view.setUint8(2, 0x68); // h
  view.setUint8(3, 0x64); // d
  view.setUint32(4, 6); // Header length
  view.setUint16(8, 0); // Format 0
  view.setUint16(10, 1); // 1 track
  view.setUint16(12, 480); // 480 ticks per beat

  // Track "MTrk"
  view.setUint8(14, 0x4d); // M
  view.setUint8(15, 0x54); // T
  view.setUint8(16, 0x72); // r
  view.setUint8(17, 0x6b); // k
  view.setUint32(18, trackBytes.length);

  for (let i = 0; i < trackBytes.length; i++) {
    view.setUint8(22 + i, trackBytes[i]);
  }

  return buffer;
}
