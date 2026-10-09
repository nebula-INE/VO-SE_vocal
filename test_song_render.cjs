const fs = require('fs');
global.window = {};
delete process.versions.node;
const wasmBuf = fs.readFileSync('./public/wasm/vose_core.wasm');

function parseWav(buffer) {
  const view = new DataView(buffer.buffer, buffer.byteOffset, buffer.byteLength);
  let pos = 12;
  while (pos < buffer.length - 8) {
    const id = String.fromCharCode(view.getUint8(pos), view.getUint8(pos+1), view.getUint8(pos+2), view.getUint8(pos+3));
    const size = view.getUint32(pos + 4, true);
    if (id === 'data') {
      return new Int16Array(buffer.buffer, buffer.byteOffset + pos + 8, size / 2);
    }
    pos += 8 + size;
  }
  throw new Error('No data chunk');
}

const vbBase = 'temp/voicebanks/Standard Japanese CV/';
const otoText = fs.readFileSync(vbBase + 'oto.ini', 'utf-8');
const otoMap = new Map();
otoText.split(/\r?\n/).forEach(line => {
  const eq = line.indexOf('=');
  if (eq === -1) return;
  const fn = line.slice(0, eq).trim();
  const rest = line.slice(eq + 1).split(',');
  if (rest.length >= 6) {
    const alias = rest[0].trim() || fn.replace('.wav', '');
    otoMap.set(alias, {
      fn,
      offset: parseFloat(rest[1]) || 0,
      consonant: parseFloat(rest[2]) || 0,
      cutoff: parseFloat(rest[3]) || 0,
      preutterance: parseFloat(rest[4]) || 0,
      overlap: parseFloat(rest[5]) || 0
    });
  }
});

const songNotes = [
  { lyric: 'ど', noteNum: 62, frames: 60 },
  { lyric: 'う', noteNum: 62, frames: 40 },
  { lyric: 'し', noteNum: 65, frames: 50 },
  { lyric: 'て', noteNum: 67, frames: 50 },
  { lyric: 'す', noteNum: 69, frames: 50 },
  { lyric: 'ぐ', noteNum: 67, frames: 50 },
  { lyric: 'に', noteNum: 65, frames: 60 },
  { lyric: 'し', noteNum: 62, frames: 50 },
  { lyric: 'ん', noteNum: 62, frames: 40 },
  { lyric: 'で', noteNum: 65, frames: 50 },
  { lyric: 'し', noteNum: 67, frames: 50 },
  { lyric: 'ま', noteNum: 69, frames: 60 },
  { lyric: 'う', noteNum: 67, frames: 40 },
  { lyric: 'の', noteNum: 65, frames: 50 },
  { lyric: 'よ', noteNum: 62, frames: 100 },
];

import('./public/wasm/vose_core.js').then(m => {
  return m.default({
    instantiateWasm(info, receiveInstance) {
      WebAssembly.instantiate(wasmBuf, info).then(res => {
        receiveInstance(res.instance);
      });
      return {};
    }
  });
}).then(async mod => {
  
  const OTO_SIZE = 632;
  const otoPtr = mod._malloc(songNotes.length * OTO_SIZE);
  mod.HEAPU8.fill(0, otoPtr, otoPtr + songNotes.length * OTO_SIZE);
  
  for (let i = 0; i < songNotes.length; i++) {
    const sn = songNotes[i];
    const oto = otoMap.get(sn.lyric) || { fn: '_' + sn.lyric + '.wav', offset: 20, consonant: 50, cutoff: 50, preutterance: 10, overlap: 10 };
    const wavPath = vbBase + oto.fn;
    const pcm16 = parseWav(fs.readFileSync(wavPath));
    
    const key = 's' + i;
    const keyPtr = mod._malloc(64);
    mod.stringToUTF8(key, keyPtr, 64);
    sn.keyPtr = keyPtr;
    
    const dataPtr = mod._malloc(pcm16.byteLength);
    mod.HEAPU8.set(new Uint8Array(pcm16.buffer, pcm16.byteOffset, pcm16.byteLength), dataPtr);
    mod.ccall('load_embedded_resource', null, ['number', 'number', 'number'], [keyPtr, dataPtr, pcm16.length]);
    mod._free(dataPtr);
    
    const base = otoPtr + i * OTO_SIZE;
    mod.setValue(base + 8, oto.cutoff, 'double');
    mod.stringToUTF8(key, base + 16, 64);
    mod.stringToUTF8(key, base + 80, 256);
    mod.setValue(base + 592, oto.offset, 'double');
    mod.setValue(base + 600, oto.consonant, 'double');
    mod.setValue(base + 616, oto.preutterance, 'double');
    mod.setValue(base + 624, oto.overlap, 'double');
  }
  
  mod.ccall('set_oto_data', null, ['number', 'number'], [otoPtr, songNotes.length]);
  mod._free(otoPtr);
  
  const NOTE_SIZE = 88;
  const notesPtr = mod._malloc(songNotes.length * NOTE_SIZE);
  mod.HEAPU8.fill(0, notesPtr, notesPtr + songNotes.length * NOTE_SIZE);
  
  for (let i = 0; i < songNotes.length; i++) {
    const sn = songNotes[i];
    const base = notesPtr + i * NOTE_SIZE;
    const baseHz = 440 * Math.pow(2, (sn.noteNum - 69) / 12);
    const pitchPtr = mod._malloc(sn.frames * 8);
    for (let f = 0; f < sn.frames; f++) {
      mod.setValue(pitchPtr + f * 8, baseHz, 'double');
    }
    const breathPtr = mod._malloc(sn.frames * 8);
    for (let f = 0; f < sn.frames; f++) {
      mod.setValue(breathPtr + f * 8, 0.0, 'double');
    }
    mod.setValue(base + 0, sn.keyPtr, 'i32');
    mod.setValue(base + 4, pitchPtr, 'i32');
    mod.setValue(base + 8, sn.frames, 'i32');
    mod.setValue(base + 20, breathPtr, 'i32');
    mod.setValue(base + 48, 100.0, 'double'); // intensity = 100
    mod.setValue(base + 56, 0.0, 'double');   // modulation = 0
    mod.setValue(base + 64, -1.0, 'double');  // start_time_ms = -1
    mod.setValue(base + 72, -1.0, 'double');  // preutterance_ms = -1
    mod.setValue(base + 80, -1.0, 'double');  // overlap_ms = -1
  }
  
  const outPath = '/song_test.wav';
  mod.ccall('execute_render', null, ['number', 'number', 'string', 'number'], [notesPtr, songNotes.length, outPath, 0]);
  
  const outBytes = mod.FS.readFile(outPath);
  fs.writeFileSync('temp_out_song.wav', Buffer.from(outBytes));
  console.log('Saved temp_out_song.wav, size:', outBytes.length);
});
