import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fixture = JSON.parse(
  fs.readFileSync(path.join(root, 'specs', 'track_mixer_cases.json'), 'utf8')
);
const app = fs.readFileSync(path.join(root, 'assets', 'src', 'App.tsx'), 'utf8');
const panel = fs.readFileSync(
  path.join(root, 'assets', 'src', 'components', 'MultiTrackPanel.tsx'),
  'utf8'
);
const voseFixture = JSON.parse(
  fs.readFileSync(path.join(root, 'specs', 'vose_project_roundtrip.json'), 'utf8')
);
const voseCodec = fs.readFileSync(
  path.join(root, 'assets', 'src', 'utils', 'voseProject.ts'),
  'utf8'
);
const projectSheet = fs.readFileSync(
  path.join(root, 'assets', 'src', 'components', 'ProjectSheet.tsx'),
  'utf8'
);

test('shared track mixer cases use normalized gain and pan values', () => {
  for (const item of fixture.cases) {
    assert.ok(item.volume >= 0 && item.volume <= 1, item.id);
    assert.ok(item.pan >= -1 && item.pan <= 1, item.id);
    assert.ok(Number.isFinite(item.left_gain));
    assert.ok(Number.isFinite(item.right_gain));
  }
});

test('Web playback routes each vocal track through its stereo panner', () => {
  assert.match(app, /const panner = ctx\.createStereoPanner\(\)/);
  assert.match(app, /track\.pan \?\? 0/);
  assert.match(app, /panner\.connect\(masterGainRef\.current\)/);
  assert.match(panel, /onUpdateTrack\(t\.id, \{ pan: parseFloat\(e\.target\.value\) \}\)/);
  assert.match(panel, /max="1"/);
});

test('shared effect chain contract is explicit', () => {
  assert.equal(fixture.effect_chain_schema.track_property, 'effects');
  assert.deepEqual(fixture.effect_chain_schema.types, ['compressor', 'eq', 'reverb']);
});


test('Web track effect presets connect to shared per-track audio buses', () => {
  assert.match(app, /params\.makeup_gain_db/);
  assert.match(app, /params\.width/);
  assert.match(app, /const nextNoise = \(\) =>/);
  assert.match(app, /trackAudioBusesRef/);
  assert.match(app, /bus\.volume\.gain\.setTargetAtTime/);
  assert.match(app, /bus\.panner\.pan\.setTargetAtTime/);
  assert.match(panel, /TRACK FX/);
  assert.match(panel, /TRACK_EFFECT_PARAM_RANGES/);
  assert.match(panel, /updateEffect\(effect\.id, \{ enabled: e\.target\.checked \}\)/);
  assert.match(app, /const getTrackBusInput = \(/);
  assert.match(app, /ctx\.createDynamicsCompressor\(\)/);
  assert.match(app, /ctx\.createBiquadFilter\(\)/);
  assert.match(app, /ctx\.createConvolver\(\)/);
  assert.match(app, /chainOutput\.connect\(volume\)/);
  assert.match(app, /volume\.connect\(panner\)/);
  assert.match(app, /track\.effects \?\? \[\]/);
  assert.match(panel, /value="vocal_clean"/);
  assert.match(panel, /value="warm_room"/);
  assert.match(panel, /value="vocal_presence"/);
  assert.match(panel, /onUpdateTrack\(t\.id, \{ effects: createTrackEffectPreset\(preset\) \}\)/);
});


test('active Web track FX buses follow live track fader and pan changes', () => {
  assert.match(app, /bus\.volume\.gain\.setTargetAtTime/);
  assert.match(app, /bus\.panner\.pan\.setTargetAtTime/);
});


test('Web presets carry the same stable IDs and effect types as the shared fixture', () => {
  for (const chain of Object.values(fixture.presets)) {
    for (const effect of chain) {
      assert.ok(panel.includes("id: '" + effect.id + "'"), effect.id);
      assert.ok(panel.includes("type: '" + effect.type + "'"), effect.type);
      for (const parameter of Object.keys(effect.params)) {
        assert.ok(panel.includes(parameter), parameter);
      }
    }
  }
});



test('Web per-track effect parameter ranges match the shared Desktop contract', () => {
  for (const [effectType, parameters] of Object.entries(fixture.effect_parameter_ranges)) {
    for (const [name, [minimum, maximum]] of Object.entries(parameters)) {
      const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      // Match the complete property name; gain_db must not match makeup_gain_db.
      const match = panel.match(new RegExp('^\\s*' + escaped + ': \\{ min: ([^,]+), max: ([^,]+),', 'm'));
      assert.ok(match, effectType + '.' + name);
      assert.equal(Number(match[1]), minimum, effectType + '.' + name + ' min');
      assert.equal(Number(match[2]), maximum, effectType + '.' + name + ' max');
    }
  }
  assert.match(app, /Math\.pow\(10, Math\.max\(-12, Math\.min\(12, params\.makeup_gain_db/);
  assert.match(app, /params\.frequency_hz \?\? 1000/);
  assert.match(app, /params\.room_size \?\? 0\.45/);
});


test('shared VO-SE project fixture covers multiple tracks and effect scopes', () => {
  assert.equal(voseFixture.tracks.length, 2);
  assert.equal(voseFixture.current_track_idx, 1);
  assert.equal(voseFixture.tracks[0].volume, 0.72);
  assert.equal(voseFixture.tracks[0].pan, -0.25);
  assert.equal(voseFixture.tracks[1].is_muted, true);
  assert.equal(voseFixture.tracks[1].effects[0].type, 'reverb');
  assert.equal(voseFixture.effect_chain[0].type, 'eq');
  assert.equal(voseFixture.tracks[0].notes[0]._ust_pbm, '0');
});

test('Web imports Desktop .vose files and restores the complete track collection', () => {
  assert.match(app, /fileName\.endsWith\('\.vose'\)/);
  assert.match(app, /parseVoseProjectJson\(decodeTextBuffer\(buffer\)\)/);
  assert.match(app, /setTracks\(importedTracks\)/);
  assert.match(app, /setProjectMasterEffects\(project\.masterEffects/);
  assert.match(app, /setProjectAutomation\(project\.parameters\)/);
  assert.match(voseCodec, /raw\.start_time/);
  assert.match(voseCodec, /raw\.note_number/);
  assert.match(voseCodec, /raw\.volume/);
  assert.match(voseCodec, /raw\.pan/);
  assert.match(voseCodec, /raw\.is_muted/);
  assert.match(voseCodec, /raw\.is_solo/);
  assert.match(voseCodec, /normalizeVoseEffectChain\(raw\.effects/);
});

test('Web exports a Desktop-compatible .vose project from every track', () => {
  assert.match(app, /serializeVoseProjectJson\(state\)/);
  assert.match(app, /tracks: tracks\.map\(\(track\) =>/);
  assert.match(app, /downloadBlob\(new Blob\(\[json\]/);
  assert.match(projectSheet, /handleExport\('vose'\)/);
  assert.match(projectSheet, /\.VOSE \(全トラック\)/);
  assert.match(voseCodec, /note_number: note\.noteNum/);
  assert.match(voseCodec, /start_time: note\.tick \* secondsPerTick/);
  assert.match(voseCodec, /duration: note\.length \* secondsPerTick/);
  assert.match(voseCodec, /effect_chain: normalizeVoseEffectChain\(state\.masterEffects/);
  assert.match(voseCodec, /effects: normalizeVoseEffectChain\(track\.effects/);
});


test('VO-SE codec keeps internal voicebank identity separate from display name', () => {
  assert.equal(voseFixture.tracks[0].voicebank_id, 'voice-a');
  assert.equal(voseFixture.tracks[0].voicebank_name, 'Voice A');
  assert.equal(voseFixture.current_voice_id, 'voice-a');
  assert.equal(voseFixture.current_voice, 'Voice A');
  assert.match(voseCodec, /currentVoiceId: String\(root\.current_voice_id/);
  assert.match(voseCodec, /currentVoiceName: String\(root\.current_voice/);
  assert.match(voseCodec, /current_voice_id: state\.currentVoiceId/);
  assert.match(voseCodec, /current_voice: state\.currentVoiceName/);
  assert.match(voseCodec, /voicebankId: String\(raw\.voicebank_id/);
  assert.match(voseCodec, /voicebank: String\(raw\.voicebank_name/);
  assert.match(voseCodec, /voicebank_id: track\.voicebankId \?\? track\.voicebank/);
  assert.match(voseCodec, /voicebank_name: track\.voicebank/);
  assert.match(app, /voicebankId: track\.voicebankId/);
  assert.match(app, /next\.voicebankId = undefined/);
});

test('VO-SE codec retains UST dynamic extensions and vocal expression data', () => {
  assert.match(voseCodec, /ustExtras: extractUstExtras/);
  assert.match(voseCodec, /startsWith\('_ust_'\)/);
  assert.match(voseCodec, /KNOWN_UST_NOTE_FIELDS\.has\(key\)/);
  assert.match(voseCodec, /vibrato_depth/);
  assert.match(voseCodec, /vibrato_rate/);
  assert.match(voseCodec, /formant_shift/);
  assert.match(voseCodec, /\.\.\.\(note\.ustExtras \?\? \{\}\)/);
});

test('Web warns when Desktop .vose references local-only WAV files', () => {
  assert.match(app, /unresolvedDesktopAudio/);
  assert.match(app, /ブラウザから直接開けないためWeb側で再リンクが必要/);
  assert.match(app, /voicebank_idは保持しましたが、対応するWeb音源が未登録/);
});


test('Imported Web master effects are applied before the fixed master limiter', () => {
  assert.match(app, /const applyMasterEffects = useCallback/);
  assert.match(app, /masterEffectSourceRef\.current = safetyLpf/);
  assert.match(app, /setProjectMasterEffects\(project\.masterEffects/);
  assert.match(app, /output\.connect\(limiter\)/);
  assert.match(app, /masterEffectSignatureRef/);
});


test('VO-SE Web project parser rejects unknown track/effect types and string booleans', () => {
  assert.match(voseCodec, /hasOwnProperty\.call\(EFFECT_DEFAULTS, type\)/);
  assert.match(voseCodec, /Unsupported track type on track/);
  assert.match(voseCodec, /must be booleans/);
});

test('Web .vose export warns when uploaded WAV data is only a browser blob', () => {
  assert.match(app, /browserOnlyAudioTracks/);
  assert.match(app, /ブラウザ内だけのWAVデータはJSONに含まれない/);
});
