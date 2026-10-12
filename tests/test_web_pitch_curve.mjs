import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

// Compile the production TypeScript utility in-memory so this test exercises the
// actual source without adding a runtime loader or modifying production code.
const here = dirname(fileURLToPath(import.meta.url));
const sourcePath = resolve(here, '../src/utils/pitchCurve.ts');
const source = readFileSync(sourcePath, 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.ESNext,
    target: ts.ScriptTarget.ES2022,
  },
}).outputText;
const pitchCurve = await import(
  'data:text/javascript;base64,' + Buffer.from(compiled, 'utf8').toString('base64')
);

test('PBS/PBW/PBY parser uses fixed 0.1 semitone scale and preserves negative PBS offset', () => {
  const points = pitchCurve.parsePitchBend('-50;10', '100,200', '20,-15s');
  assert.deepEqual(points, [
    { offsetMs: -50, semitone: 1 },
    { offsetMs: 50, semitone: 2 },
    { offsetMs: 250, semitone: -1.5 },
  ]);
});

test('parser clamps start offset, segment widths, and semitone range deterministically', () => {
  const points = pitchCurve.parsePitchBend('-9999;999', '0,6000', '999,-999');
  assert.deepEqual(points, [
    { offsetMs: -3000, semitone: 24 },
    { offsetMs: -2999, semitone: 24 },
    { offsetMs: 2001, semitone: -24 },
  ]);
});

test('sampler linearly interpolates and holds both endpoints', () => {
  const points = [
    { offsetMs: -100, semitone: -2 },
    { offsetMs: 100, semitone: 2 },
  ];
  assert.equal(pitchCurve.sampleSemitoneAt([], 0), 0);
  assert.equal(pitchCurve.sampleSemitoneAt(points, -200), -2);
  assert.equal(pitchCurve.sampleSemitoneAt(points, -100), -2);
  assert.equal(pitchCurve.sampleSemitoneAt(points, 0), 0);
  assert.equal(pitchCurve.sampleSemitoneAt(points, 50), 1);
  assert.equal(pitchCurve.sampleSemitoneAt(points, 100), 2);
  assert.equal(pitchCurve.sampleSemitoneAt(points, 500), 2);
});

test('smoother applies the configured time-based slew limit without changing timestamps', () => {
  const input = [
    { offsetMs: -20, semitone: 0 },
    { offsetMs: 0, semitone: 8 },
    { offsetMs: 100, semitone: -8 },
  ];
  const output = pitchCurve.smoothPitchBendPoints(input, 0.08);
  assert.deepEqual(output.map((p) => p.offsetMs), [-20, 0, 100]);
  assert.equal(output[0].semitone, 0);
  assert.equal(output[1].semitone, 1.6);
  assert.ok(Math.abs(output[2].semitone - (-6.4)) < 1e-12);
});

test('tempo conversion uses 480 ticks per beat and is inverse at positive tempo', () => {
  assert.ok(Math.abs(pitchCurve.msToTicks(500, 120) - 480) < 1e-9);
  assert.ok(Math.abs(pitchCurve.ticksToMs(480, 120) - 500) < 1e-9);
  for (const tempo of [60, 90, 120, 150, 240]) {
    const ticks = pitchCurve.msToTicks(375, tempo);
    assert.ok(Math.abs(pitchCurve.ticksToMs(ticks, tempo) - 375) < 1e-9);
  }
});

test('serializer converts semitone offsets to UST tenths and round-trips through parser', () => {
  const original = [
    { offsetMs: -50, semitone: 1.25 },
    { offsetMs: 50, semitone: -1.5 },
    { offsetMs: 175, semitone: 0.35 },
  ];

  const serialized = pitchCurve.serializePitchBend(original);
  assert.deepEqual(serialized, {
    pbs: '-50;12.5',
    pbw: '100,125',
    pby: '-15,3.5',
  });

  const reparsed = pitchCurve.parsePitchBend(
    serialized.pbs,
    serialized.pbw,
    serialized.pby,
  );
  assert.deepEqual(reparsed, original);
});


test('representative existing UST bend values retain the fixed tenths scale', () => {
  // Shape already used by the repository's UST parser regression fixture.
  // This guards compatibility for imported UST data while the editor serializer
  // is corrected independently.
  assert.deepEqual(
    pitchCurve.parsePitchBend('0;0', '50,100', '0,5'),
    [
      { offsetMs: 0, semitone: 0 },
      { offsetMs: 50, semitone: 0 },
      { offsetMs: 150, semitone: 0.5 },
    ],
  );

  assert.deepEqual(
    pitchCurve.parsePitchBend('0;5', '100', '10'),
    [
      { offsetMs: 0, semitone: 0.5 },
      { offsetMs: 100, semitone: 1 },
    ],
  );
});



async function loadPitchBridgeHelper() {
  // Instrument the actual private helper in-memory; do not alter production exports.
  const clientSource = readFileSync(resolve(here, '../src/voseCoreClient.ts'), 'utf8');
  const helperMatch = clientSource.match(
    /function buildPitchCurveHz\(note: any, frameCount: number, durationMs: number\): number\[\] \{[\s\S]*?\r?\n\}\r?\n\r?\nfunction silentFrames/
  );
  assert.ok(helperMatch, 'buildPitchCurveHz helper must exist in production source');
  const helperSource = helperMatch[0].replace(/\r?\n\r?\nfunction silentFrames$/, '\nexport { buildPitchCurveHz };');
  const utilityUrl = 'data:text/javascript;base64,' + Buffer.from(compiled, 'utf8').toString('base64');
  const bridgeModuleSource = 'import { parsePitchBend, smoothPitchBendPoints } from ' + JSON.stringify(utilityUrl) + ';\n' + helperSource;
  const bridgeCompiled = ts.transpileModule(bridgeModuleSource, {
    compilerOptions: {
      module: ts.ModuleKind.ESNext,
      target: ts.ScriptTarget.ES2022,
    },
  }).outputText;
  return import(
    'data:text/javascript;base64,' + Buffer.from(bridgeCompiled, 'utf8').toString('base64')
  );
}

test('WASM pitch bridge converts sampled semitone bends to absolute Hz without renderer changes', async () => {
  const bridge = await loadPitchBridgeHelper();
  const curve = bridge.buildPitchCurveHz({
    noteNum: 69,
    pbs: '0;0',
    pbw: '50,50',
    pby: '10,20',
  }, 3, 100);
  const semitoneHz = (semitones) => 440 * Math.pow(2, semitones / 12);
  assert.equal(curve.length, 3);
  assert.ok(Math.abs(curve[0] - 440) < 1e-9);
  assert.ok(Math.abs(curve[1] - semitoneHz(1)) < 1e-9);
  assert.ok(Math.abs(curve[2] - semitoneHz(2)) < 1e-9);
});

test('WASM pitch bridge preserves base frequency for notes without bend data', async () => {
  const bridge = await loadPitchBridgeHelper();
  const curve = bridge.buildPitchCurveHz({ noteNum: 60 }, 3, 10);
  const expectedHz = 440 * Math.pow(2, (60 - 69) / 12);
  assert.equal(curve.length, 3);
  for (const hz of curve) assert.ok(Math.abs(hz - expectedHz) < 1e-9);
});

test('Desktop/Web shared frequency contract matches for a constant pitch automation offset', async () => {
  // Desktop automation maps 4095.5 to +1 semitone; Web UST uses +1 semitone
  // at every control point (raw PBS/PBY values are tenths of a semitone).
  // Constant bends deliberately avoid interpolation/slew-policy differences.
  const bridge = await loadPitchBridgeHelper();
  const semitoneOffset = 1;
  const noteNumbers = [48, 60, 69, 81];
  for (const noteNum of noteNumbers) {
    const baseHz = 440 * Math.pow(2, (noteNum - 69) / 12);
    const curve = bridge.buildPitchCurveHz({
      noteNum,
      pbs: '0;10',
      pbw: '50,50',
      pby: '10,10',
    }, 3, 100);
    const expectedHz = baseHz * Math.pow(2, semitoneOffset / 12);
    assert.equal(curve.length, 3);
    for (const hz of curve) {
      assert.ok(
        Math.abs(hz - expectedHz) < 1e-9,
        `noteNum=${noteNum}: expected ${expectedHz} Hz, received ${hz} Hz`,
      );
    }
  }
});



test('WASM pitch bridge keeps negative bends in semitone-to-Hz conversion', async () => {
  const bridge = await loadPitchBridgeHelper();
  const curve = bridge.buildPitchCurveHz({
    noteNum: 69,
    pbs: '0;0',
    pbw: '50,50',
    pby: '-10,-20',
  }, 3, 100);
  const semitoneHz = (semitones) => 440 * Math.pow(2, semitones / 12);
  assert.equal(curve.length, 3);
  assert.ok(Math.abs(curve[0] - semitoneHz(0)) < 1e-9);
  assert.ok(Math.abs(curve[1] - semitoneHz(-1)) < 1e-9);
  assert.ok(Math.abs(curve[2] - semitoneHz(-2)) < 1e-9);
});

test('WASM pitch bridge samples full duration endpoints on a non-5ms-aligned duration', async () => {
  const bridge = await loadPitchBridgeHelper();
  const curve = bridge.buildPitchCurveHz({
    noteNum: 69,
    pbs: '0;0',
    pbw: '103',
    pby: '0,20',
  }, 3, 103);
  const semitoneHz = (semitones) => 440 * Math.pow(2, semitones / 12);
  assert.equal(curve.length, 3);
  assert.ok(Math.abs(curve[0] - semitoneHz(0)) < 1e-9);
  assert.ok(Math.abs(curve[1] - semitoneHz(1)) < 1e-9);
  assert.ok(Math.abs(curve[2] - semitoneHz(2)) < 1e-9);
});

test('WASM pitch bridge applies the existing slew limit to a steep bend', async () => {
  const bridge = await loadPitchBridgeHelper();
  const curve = bridge.buildPitchCurveHz({
    noteNum: 69,
    pbs: '0;0',
    pbw: '10',
    pby: '0,100',
  }, 2, 10);
  const semitoneHz = (semitones) => 440 * Math.pow(2, semitones / 12);
  assert.equal(curve.length, 2);
  assert.ok(Math.abs(curve[0] - semitoneHz(0)) < 1e-9);
  assert.ok(Math.abs(curve[1] - semitoneHz(0.8)) < 1e-9);
});
