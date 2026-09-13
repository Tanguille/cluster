#!/usr/bin/env bash
# Build the radiance MXFP4 W4A8 extension for gfx1201 inside the vLLM image (low priority: the
# node is serving). Same flags as radiance's Dockerfile.
set -euo pipefail
cd /tmp/mxbench
INC=$(python3 -m pybind11 --includes)
# shellcheck disable=SC2086
nice -n 19 hipcc -O3 -std=c++17 -fPIC -shared --offload-arch=gfx1201 -Wno-unused-result \
    $INC radiance_mxfp4_fp8.hip -o radiance_mxfp4_fp8.so >build.log 2>&1 || {
    grep -E 'error' build.log
    exit 1
}
ls -la radiance_mxfp4_fp8.so
python3 -c "import sys; sys.path.insert(0,'/tmp/mxbench'); import radiance_mxfp4_fp8 as m; print('ext ok', [n for n in dir(m) if not n.startswith('_')])"
