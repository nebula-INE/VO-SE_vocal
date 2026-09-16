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

// Inspect the stages
console.log('Testing pipeline stages...');
