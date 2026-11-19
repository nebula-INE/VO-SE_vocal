import type { Note } from './formatConverter';

export type VoseEffectType = 'compressor' | 'eq' | 'reverb';

export interface VoseTrackEffect {
  id: string;
  type: VoseEffectType;
  enabled: boolean;
  params: Record<string, number>;
}

export interface VoseWebTrack {
  id: string;
  name: string;
  type: 'vocal' | 'wave';
  voicebank?: string;
  voicebankId?: string;
  notes: Note[];
  volume: number;
  pan: number;
  effects: VoseTrackEffect[];
  isMuted: boolean;
  isSolo: boolean;
  audioUrl?: string;
  sourcePath?: string;
  engineType?: string;
  colorLabel?: string;
}

export interface VoseProjectState {
  projectName: string;
  tempo: number;
  currentTrackIndex: number;
  currentTime: number;
  currentVoiceId?: string;
  currentVoiceName?: string;
  tracks: VoseWebTrack[];
  masterEffects: VoseTrackEffect[];
  parameters: Record<string, unknown>;
}

const EFFECT_DEFAULTS: Record<VoseEffectType, Record<string, number>> = {
  compressor: {
    threshold_db: -18,
    ratio: 4,
    attack_ms: 10,
    release_ms: 100,
    makeup_gain_db: 0,
  },
  eq: {
    frequency_hz: 1000,
    gain_db: 0,
    q: 1,
  },
  reverb: {
    room_size: 0.45,
    damping: 0.5,
    wet: 0.12,
    dry: 0.9,
    width: 0.75,
  },
};

const KNOWN_UST_NOTE_FIELDS = new Set([
  '_ust_flags',
  '_ust_pbs',
  '_ust_pbw',
  '_ust_pby',
  '_ust_pbm',
  '_ust_pbs_present',
  '_ust_pbw_present',
  '_ust_pby_present',
  '_ust_tempo',
]);

const extractUstExtras = (record: Record<string, unknown>): Record<string, unknown> =>
  Object.fromEntries(
    Object.entries(record).filter(
      ([key]) => key.startsWith('_ust_') && !KNOWN_UST_NOTE_FIELDS.has(key)
    )
  );

const EFFECT_RANGES: Record<VoseEffectType, Record<string, [number, number]>> = {
  compressor: {
    threshold_db: [-60, 0],
    ratio: [1, 20],
    attack_ms: [1, 200],
    release_ms: [10, 1000],
    makeup_gain_db: [-12, 12],
  },
  eq: {
    frequency_hz: [20, 20000],
    gain_db: [-18, 18],
    q: [0.2, 10],
  },
  reverb: {
    room_size: [0, 0.9],
    damping: [0, 0.95],
    wet: [0, 1],
    dry: [0, 1],
    width: [0, 1],
  },
};

const asRecord = (value: unknown, description: string): Record<string, unknown> => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${description} must be an object`);
  }
  return value as Record<string, unknown>;
};

const finiteNumber = (
  value: unknown,
  fallback: number,
  description: string,
  minimum?: number,
  maximum?: number,
): number => {
  const parsed = value === undefined || value === null ? fallback : Number(value);
  if (!Number.isFinite(parsed)) throw new Error(`${description} must be a finite number`);
  if (minimum !== undefined && parsed < minimum) throw new Error(`${description} is below its minimum`);
  if (maximum !== undefined && parsed > maximum) throw new Error(`${description} is above its maximum`);
  return parsed;
};

export function normalizeVoseEffectChain(rawChain: unknown, description = 'effects'): VoseTrackEffect[] {
  if (rawChain === undefined || rawChain === null) return [];
  if (!Array.isArray(rawChain)) throw new Error(`${description} must be an array`);
  if (rawChain.length > 16) throw new Error(`${description} cannot contain more than 16 effects`);

  const usedIds = new Set<string>();
  return rawChain.map((raw, index) => {
    const item = asRecord(raw, `${description}[${index}]`);
    const type = String(item.type || '') as VoseEffectType;
    if (!Object.prototype.hasOwnProperty.call(EFFECT_DEFAULTS, type)) {
      throw new Error(`Unsupported effect type: ${type}`);
    }
    const id = String(item.id || `effect_${index + 1}`).trim();
    if (!id || id.length > 128 || usedIds.has(id)) throw new Error(`Invalid or duplicate effect ID: ${id}`);
    usedIds.add(id);
    if (item.enabled !== undefined && typeof item.enabled !== 'boolean') {
      throw new Error(`${id}.enabled must be a boolean`);
    }
    const rawParams = item.params === undefined ? {} : asRecord(item.params, `${id}.params`);
    const allowed = new Set(Object.keys(EFFECT_DEFAULTS[type]));
    for (const key of Object.keys(rawParams)) {
      if (!allowed.has(key)) throw new Error(`Unsupported parameter ${key} on ${id}`);
    }
    const params: Record<string, number> = {};
    for (const [name, fallback] of Object.entries(EFFECT_DEFAULTS[type])) {
      const [minimum, maximum] = EFFECT_RANGES[type][name];
      params[name] = finiteNumber(rawParams[name], fallback, `${id}.${name}`, minimum, maximum);
    }
    return { id, type, enabled: item.enabled !== false, params };
  });
}

const noteFromDesktop = (rawValue: unknown, index: number, tempo: number): Note => {
  const raw = asRecord(rawValue, `track note ${index + 1}`);
  if (raw.noteNum !== undefined || raw.tick !== undefined || raw.length !== undefined) {
    const noteNum = finiteNumber(raw.noteNum ?? raw.note_num, 60, 'noteNum', 0, 127);
    const tick = finiteNumber(raw.tick, 0, 'tick', 0);
    const length = finiteNumber(raw.length, 480, 'length', 0);
    return {
      id: String(raw.id || `note_${index + 1}`),
      lyric: String(raw.lyric ?? raw.lyrics ?? 'あ'),
      noteNum,
      tick,
      length,
      intensity: finiteNumber(raw.intensity ?? raw.velocity, 100, 'intensity', 0, 200),
      flags: String(raw.flags ?? raw._ust_flags ?? ''),
      ustExtras: extractUstExtras(raw),
      vibrato_depth: raw.vibrato_depth === undefined ? undefined : finiteNumber(raw.vibrato_depth, 0, 'vibrato_depth'),
      vibrato_rate: raw.vibrato_rate === undefined ? undefined : finiteNumber(raw.vibrato_rate, 5.5, 'vibrato_rate'),
      formant_shift: raw.formant_shift === undefined ? undefined : finiteNumber(raw.formant_shift, 0, 'formant_shift'),
      pbs: String(raw.pbs ?? raw._ust_pbs ?? '0;0'),
      pbw: String(raw.pbw ?? raw._ust_pbw ?? '50'),
      pby: String(raw.pby ?? raw._ust_pby ?? '0'),
      ...(raw.pbm !== undefined || raw._ust_pbm !== undefined
        ? { pbm: String(raw.pbm ?? raw._ust_pbm) }
        : {}),
      hasPbs: Boolean(raw.hasPbs ?? raw._ust_pbs_present ?? false),
      hasPbw: Boolean(raw.hasPbw ?? raw._ust_pbw_present ?? false),
      hasPby: Boolean(raw.hasPby ?? raw._ust_pby_present ?? false),
      ...(raw.tempo !== undefined || raw._ust_tempo !== undefined
        ? { tempo: finiteNumber(raw.tempo ?? raw._ust_tempo, tempo, 'note tempo', 1) }
        : {}),
    };
  }

  // Desktop NoteEvent stores seconds and MIDI velocity; Web uses ticks and 0..200 intensity.
  const ticksPerSecond = tempo * 480 / 60;
  const startSeconds = finiteNumber(raw.start_time ?? raw.start, 0, 'start_time', 0);
  const durationSeconds = finiteNumber(raw.duration, 0, 'duration', 0);
  const extras = raw as Record<string, unknown>;
  return {
    id: String(raw.id || `note_${index + 1}`),
    lyric: String(raw.lyric ?? raw.lyrics ?? 'あ'),
    noteNum: finiteNumber(raw.note_number ?? raw.note_num, 60, 'note_number', 0, 127),
    tick: Math.round(startSeconds * ticksPerSecond),
    length: Math.max(0, Math.round(durationSeconds * ticksPerSecond)),
    intensity: finiteNumber(raw.velocity, 100, 'velocity', 0, 200),
    flags: String(extras._ust_flags ?? ''),
    ustExtras: extractUstExtras(extras),
    vibrato_depth: extras.vibrato_depth === undefined ? undefined : finiteNumber(extras.vibrato_depth, 0, 'vibrato_depth'),
    vibrato_rate: extras.vibrato_rate === undefined ? undefined : finiteNumber(extras.vibrato_rate, 5.5, 'vibrato_rate'),
    formant_shift: extras.formant_shift === undefined ? undefined : finiteNumber(extras.formant_shift, 0, 'formant_shift'),
    pbs: String(extras._ust_pbs ?? '0;0'),
    pbw: String(extras._ust_pbw ?? '50'),
    pby: String(extras._ust_pby ?? '0'),
    ...(extras._ust_pbm !== undefined ? { pbm: String(extras._ust_pbm) } : {}),
    hasPbs: Boolean(extras._ust_pbs_present ?? false),
    hasPbw: Boolean(extras._ust_pbw_present ?? false),
    hasPby: Boolean(extras._ust_pby_present ?? false),
    ...(extras._ust_tempo !== undefined
      ? { tempo: finiteNumber(extras._ust_tempo, tempo, 'note tempo', 1) }
      : {}),
  };
};

export function parseVoseProjectJson(jsonText: string): VoseProjectState {
  const root = asRecord(JSON.parse(jsonText) as unknown, 'VO-SE project');
  const tempo = finiteNumber(root.tempo, 120, 'tempo', 1, 1000);
  const rawTracks = root.tracks;
  if (rawTracks !== undefined && !Array.isArray(rawTracks)) {
    throw new Error('tracks must be an array');
  }

  const usedTrackIds = new Set<string>();
  const tracks: VoseWebTrack[] = (Array.isArray(rawTracks) ? rawTracks : []).map((value, index) => {
    const raw = asRecord(value, `track ${index + 1}`);
    const trackId = String(raw.web_track_id ?? raw.id ?? `track_${index + 1}`);
    if (!trackId || usedTrackIds.has(trackId)) {
      throw new Error(`Invalid or duplicate track ID: ${trackId}`);
    }
    usedTrackIds.add(trackId);
    const rawNotes = raw.notes === undefined ? [] : raw.notes;
    if (!Array.isArray(rawNotes)) throw new Error(`track ${index + 1}.notes must be an array`);
    const rawType = String(raw.type ?? raw.track_type ?? 'vocal').toLowerCase();
    if (rawType !== 'vocal' && rawType !== 'wave') {
      throw new Error(`Unsupported track type on track ${index + 1}: ${rawType}`);
    }
    const type: 'vocal' | 'wave' = rawType;
    const isMuted = raw.is_muted ?? raw.isMuted ?? false;
    const isSolo = raw.is_solo ?? raw.isSolo ?? false;
    if (typeof isMuted !== 'boolean' || typeof isSolo !== 'boolean') {
      throw new Error(`track ${index + 1}.is_muted and is_solo must be booleans`);
    }
    const audioPath = String(raw.audio_path ?? raw.sourcePath ?? '');
    const audioUrl = typeof raw.audio_url === 'string' ? raw.audio_url : '';
    return {
      id: trackId,
      name: String(raw.name ?? `Track ${index + 1}`),
      type,
      voicebank: String(raw.voicebank_name ?? raw.voicebank ?? '') || undefined,
      voicebankId: String(raw.voicebank_id ?? '') || undefined,
      notes: rawNotes.map((note, noteIndex) => noteFromDesktop(note, noteIndex, tempo)),
      volume: finiteNumber(raw.volume, 1, `track ${index + 1}.volume`, 0, 1),
      pan: finiteNumber(raw.pan, 0, `track ${index + 1}.pan`, -1, 1),
      effects: normalizeVoseEffectChain(raw.effects, `track ${index + 1}.effects`),
      isMuted,
      isSolo,
      ...(audioUrl ? { audioUrl } : {}),
      ...(audioPath ? { sourcePath: audioPath } : {}),
      ...(raw.engine_type ? { engineType: String(raw.engine_type) } : {}),
      ...(raw.color_label ? { colorLabel: String(raw.color_label) } : {}),
    };
  });

  // Older single-track JSON projects used a top-level notes array.
  if (tracks.length === 0 && Array.isArray(root.notes)) {
    tracks.push({
      id: 'track_1',
      name: String(root.project_name ?? root.projectName ?? 'Vocal 1'),
      type: 'vocal',
      voicebank: String(root.voicebank ?? '') || undefined,
      voicebankId: String(root.voicebank_id ?? '') || undefined,
      notes: root.notes.map((note, index) => noteFromDesktop(note, index, tempo)),
      volume: 1,
      pan: 0,
      effects: [],
      isMuted: false,
      isSolo: false,
    });
  }
  if (tracks.length === 0) throw new Error('VO-SE project does not contain any tracks');

  const currentTrackIndex = Math.max(
    0,
    Math.min(tracks.length - 1, Math.trunc(finiteNumber(root.current_track_idx, 0, 'current_track_idx', 0))),
  );
  const parameters = root.parameters && typeof root.parameters === 'object' && !Array.isArray(root.parameters)
    ? root.parameters as Record<string, unknown>
    : {};
  return {
    projectName: String(root.project_name ?? root.projectName ?? 'VO-SE Project'),
    tempo,
    currentTrackIndex,
    currentTime: finiteNumber(root.current_time ?? root.currentTime, 0, 'current_time', 0),
    currentVoiceId: String(root.current_voice_id ?? '') || undefined,
    currentVoiceName: String(root.current_voice ?? '') || undefined,
    tracks,
    masterEffects: normalizeVoseEffectChain(root.effect_chain, 'effect_chain'),
    parameters,
  };
}

const noteToDesktop = (note: Note, tempo: number): Record<string, unknown> => {
  const secondsPerTick = 60 / (tempo * 480);
  const record = note as Note & Record<string, unknown>;
  return {
    note_number: note.noteNum,
    start_time: note.tick * secondsPerTick,
    duration: note.length * secondsPerTick,
    lyric: note.lyric,
    velocity: note.intensity,
    _ust_flags: note.flags,
    _ust_pbs: note.pbs,
    _ust_pbw: note.pbw,
    _ust_pby: note.pby,
    ...(note.pbm !== undefined ? { _ust_pbm: note.pbm } : {}),
    _ust_pbs_present: Boolean(note.hasPbs),
    _ust_pbw_present: Boolean(note.hasPbw),
    _ust_pby_present: Boolean(note.hasPby),
    ...(note.tempo !== undefined ? { _ust_tempo: note.tempo } : {}),
    ...(typeof record.vibrato_depth === 'number' ? { vibrato_depth: record.vibrato_depth } : {}),
    ...(typeof record.vibrato_rate === 'number' ? { vibrato_rate: record.vibrato_rate } : {}),
    ...(typeof record.formant_shift === 'number' ? { formant_shift: record.formant_shift } : {}),
    ...(note.ustExtras ?? {}),
  };
};

export function serializeVoseProjectJson(state: VoseProjectState): string {
  const tempo = finiteNumber(state.tempo, 120, 'tempo', 1, 1000);
  const tracks = state.tracks.map((track) => ({
    web_track_id: track.id,
    name: track.name,
    type: track.type,
    audio_path: track.sourcePath ?? '',
    audio_url: track.audioUrl && !track.audioUrl.startsWith('blob:') ? track.audioUrl : '',
    volume: finiteNumber(track.volume, 1, `${track.name}.volume`, 0, 1),
    pan: finiteNumber(track.pan, 0, `${track.name}.pan`, -1, 1),
    is_muted: Boolean(track.isMuted),
    is_solo: Boolean(track.isSolo),
    engine_type: track.engineType ?? 'Aural',
    color_label: track.colorLabel ?? '',
    voicebank_id: track.voicebankId ?? track.voicebank ?? '',
    voicebank_name: track.voicebank ?? '',
    effects: normalizeVoseEffectChain(track.effects, `${track.name}.effects`),
    notes: track.notes.map((note) => noteToDesktop(note, tempo)),
  }));
  return JSON.stringify({
    app_id: 'VO_SE_Pro_2026',
    version: '1.5.0',
    project_name: state.projectName,
    tempo,
    current_track_idx: Math.max(0, Math.min(tracks.length - 1, Math.trunc(state.currentTrackIndex))),
    current_time: finiteNumber(state.currentTime, 0, 'current_time', 0),
    current_voice_id: state.currentVoiceId ?? state.tracks[state.currentTrackIndex]?.voicebankId ?? '',
    current_voice: state.currentVoiceName ?? state.tracks[state.currentTrackIndex]?.voicebank ?? '未選択',
    effect_chain: normalizeVoseEffectChain(state.masterEffects, 'effect_chain'),
    parameters: state.parameters ?? {},
    tracks,
  }, null, 2);
}
