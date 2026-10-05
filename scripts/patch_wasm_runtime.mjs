import fs from 'node:fs';

const file = process.argv[2];
if (!file) throw new Error('Usage: node patch_wasm_runtime.mjs <vose_core.js>');

let source = fs.readFileSync(file, 'utf8');
const marker = 'var wasmImports={';
const helper = 'function invoke_ilj(index,a1,a2){var sp=stackSave();try{return getWasmTableEntry(index)(a1,a2)}catch(e){stackRestore(sp);if(!(e instanceof EmscriptenEH))throw e;_setThrew(1,0)}}';

if (!source.includes('function invoke_ilj(')) {
  if (!source.includes(marker)) {
    throw new Error('Emscripten wasmImports marker not found in generated loader');
  }
  source = source.replace(marker, helper + ' ' + marker);
}

if (!source.includes('invoke_ilj:invoke_ilj')) {
  const importMarker = 'invoke_diii:invoke_diii,';
  if (!source.includes(importMarker)) {
    throw new Error('Emscripten wasmImports invoke list marker not found');
  }
  source = source.replace(importMarker, importMarker + 'invoke_ilj:invoke_ilj,');
}

fs.writeFileSync(file, source);
console.log('Patched vose_core.js with env.invoke_ilj compatibility bridge.');
