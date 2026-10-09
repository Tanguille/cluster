#!/usr/bin/env bash
# Chunk 6 steps 3-6, run from the workstation. Copies this directory to uq-bench:/work/mx/ and runs
# build (CPU only), correctness, compile safety and the bench. GPU steps take the shared lock.
# usage: run.sh <lockfile> <outdir> [real-layer-dir]   (real-layer-dir holds real_*.safetensors from fetch_layer.py)
set -euo pipefail
LOCK="$1"
OUT="$2"
REAL="${3:-}"
HERE="$(cd "$(dirname "$0")" && pwd)"
POD=(kubectl -n ai exec uq-bench --)
D=/work/mx/rdna_w4a8_gfx12

# kubectl cp exits non-zero on tar's benign ownership warning: that output is ignored, any other output fails the run.
cp_to_pod() {
    local out rest
    if ! out="$(kubectl cp "$1" "$2" 2>&1)"; then
        rest="$(grep -v 'Cannot change ownership\|Exiting with failure' <<<"$out" || true)"
        if [ -n "$rest" ]; then
            echo "kubectl cp $1 failed: $rest" >&2
            exit 1
        fi
    fi
}
# Every local file must exist in the pod with the same sha256, so a failed copy never runs stale code.
verify_in_pod() { # <local dir> <pod dir> <find args...>
    local dir="$1" pod_dir="$2"
    shift 2
    (cd "$dir" && find . -type f "$@" | sort | xargs sha256sum) |
        kubectl -n ai exec -i uq-bench -- sh -c "cd $pod_dir && sha256sum -c --quiet -" ||
        {
            echo "sync check failed for $dir: the pod copy differs" >&2
            exit 1
        }
}

cp_to_pod "$HERE" ai/uq-bench:/work/mx
verify_in_pod "$HERE" "$D" -not -path '*/__pycache__/*'
if [ -n "$REAL" ]; then
    for f in "$REAL"/real_*.safetensors; do
        cp_to_pod "$f" "ai/uq-bench:/work/mx/$(basename "$f")"
    done
    verify_in_pod "$REAL" /work/mx -name 'real_*.safetensors'
fi
"${POD[@]}" nice -n 19 python3 "$D/build.py" 2>&1 | tee "$OUT/build.out" | tail -3
for step in test_correctness test_compile bench_vs_w4a16; do
    flock -w 7200 "$LOCK" "${POD[@]}" python3 "$D/$step.py" 2>&1 | tee "$OUT/$step.out"
done
grep -h 'lds-gate-patch' "$OUT/bench_vs_w4a16.out" | sort -u
