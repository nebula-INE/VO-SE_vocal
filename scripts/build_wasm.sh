#!/usr/bin/env bash
set -euo pipefail

# Build the browser/Worker WebAssembly engine from the canonical native sources.
# The same script is used by the deploy workflow and Web regression tests so
# tests never pair a new JS loader with a stale checked-in WASM binary.

output_dir="${1:-build/wasm}"
source_dir="${output_dir}/source"

mkdir -p "${source_dir}/world"
cp assets/src/vose_core.cpp "${source_dir}/vose_core.cpp"
cp assets/src/world/*.cpp "${source_dir}/world/"
cp include/world/*.h "${source_dir}/world/"

em++ "${source_dir}/vose_core.cpp" \
     "${source_dir}/world/audioio.cpp" \
     "${source_dir}/world/cheaptrick.cpp" \
     "${source_dir}/world/common.cpp" \
     "${source_dir}/world/d4c.cpp" \
     "${source_dir}/world/dio.cpp" \
     "${source_dir}/world/fft.cpp" \
     "${source_dir}/world/harvest.cpp" \
     "${source_dir}/world/matlabfunctions.cpp" \
     "${source_dir}/world/stonemask.cpp" \
     "${source_dir}/world/synthesis.cpp" \
     -I"${source_dir}" \
     -Iassets/src \
     -Iinclude \
     -O3 \
     -std=c++17 \
     -fexceptions \
     -s WASM=1 \
     -s ALLOW_MEMORY_GROWTH=1 \
     -s ALLOW_TABLE_GROWTH=1 \
     -s MODULARIZE=1 \
     -s EXPORT_NAME="createVoseCoreModule" \
     -s EXPORT_ES6=1 \
     -s ENVIRONMENT=web,worker \
     -s ASSERTIONS=1 \
     -s DISABLE_EXCEPTION_CATCHING=0 \
     -s EXPORTED_FUNCTIONS="['_load_embedded_resource', '_load_embedded_resource_f32', '_execute_render', '_execute_render_cancelable', '_set_oto_data', '_set_vocal_timeline', '_get_engine_version', '_get_note_event_abi', '_get_oto_entry_abi', '_clear_engine_cache', '_malloc', '_free']" \
     -s EXPORTED_RUNTIME_METHODS="['ccall', 'cwrap', 'getValue', 'setValue', 'FS', 'stringToUTF8', 'lengthBytesUTF8', 'addFunction', 'removeFunction', 'HEAPU8', 'HEAPF32', 'HEAPF64']" \
     -s DEFAULT_TO_CXX \
     -o "${output_dir}/vose_core.js"
