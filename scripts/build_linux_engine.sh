#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_dir="${BUILD_DIR:-${repo_root}/build/linux}"
build_type="${BUILD_TYPE:-Release}"

cmake -S "${repo_root}" -B "${build_dir}" -DCMAKE_BUILD_TYPE="${build_type}"
cmake --build "${build_dir}" --target vose_core --parallel "${CMAKE_BUILD_PARALLEL_LEVEL:-$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 2)}"

engine_path="${repo_root}/bin/libvose_core.so"
if [[ ! -f "${engine_path}" ]]; then
    echo "Linux engine was not produced at ${engine_path}" >&2
    exit 1
fi

ENGINE_PATH="${engine_path}" python - <<'PY'
import ctypes
import os

engine_path = os.environ["ENGINE_PATH"]
ctypes.CDLL(engine_path)
print(f"✅ Linux VOSE Core loaded: {engine_path}")
PY
