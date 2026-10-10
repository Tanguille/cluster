#!/usr/bin/env bash
# Append "<unix_ts> <free_GiB>" every ~3 s to $1 until $1.stop exists. Free VRAM is node-wide (uq-bench pod sysfs).
set -euo pipefail
SP=$(cd "$(dirname "$0")" && pwd)
out=$1
while [ ! -e "$out.stop" ]; do
    echo "$(date +%s) $(bash "$SP/vramfree.sh" 2>/dev/null || echo NA)" >>"$out"
    sleep 3
done
