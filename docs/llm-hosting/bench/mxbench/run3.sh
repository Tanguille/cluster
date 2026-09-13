#!/usr/bin/env bash
# Three configurations back to back so they see the same prod load as far as possible.
set -euo pipefail
cd /tmp/mxbench
for cfg in "0 0" "1 0" "1 1"; do
    # shellcheck disable=SC2086
    set -- $cfg
    echo "=== wperm=$1 decode_nt=$2"
    RADIANCE_MXFP4_DECODE_NT=$2 python3 mxfp4_vs_w4a16.py --iters 120 --wperm "$1" 2>&1 | grep -vE 'lds-gate|Warning|warn'
done
