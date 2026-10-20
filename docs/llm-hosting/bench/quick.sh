#!/usr/bin/env bash
# quick.sh <label>: port-forward the vLLM pod, run band check, 4 concsweep reps, 64K decode. Output in ./<label>/
set -euo pipefail
L=${1:?label}
O=$PWD/$L
mkdir -p "$O"
POD=$(kubectl -n ai get pods -l app=qwen38-27b-vllm --no-headers -o custom-columns=N:.metadata.name | head -1)
kubectl -n ai port-forward "pod/$POD" 18000:8000 >"$O/pf.log" 2>&1 &
pf=$!
trap 'kill $pf 2>/dev/null || true' EXIT
sleep 4
cd "$(dirname "$(readlink -f "$0")")"
echo "pod $POD" | tee "$O/out.txt"
python3 longctx.py 18000 qwen-3.8 4000 3 2>&1 | tee -a "$O/out.txt"
for _ in 1 2 3 4; do python3 concsweep.py 18000 qwen-3.8 1,2,3,4,5 2>&1 | tee -a "$O/out.txt"; done
python3 longctx.py 18000 qwen-3.8 50000 2 2>&1 | tee -a "$O/out.txt"
echo DONE | tee -a "$O/out.txt"
