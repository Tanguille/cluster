#!/usr/bin/env bash
# Print node-wide free VRAM in GiB on the R9700, read through the uq-bench pod (same GPU sysfs).
set -euo pipefail
# shellcheck disable=SC2016 # expanded by the pod shell
kubectl -n ai exec uq-bench </dev/null -- sh -c 'd=$(dirname $(ls /sys/class/drm/card*/device/mem_info_vram_total | head -1)); echo "$(cat $d/mem_info_vram_total) $(cat $d/mem_info_vram_used)"' |
  awk '{printf "%.3f\n", ($1 - $2) / 1073741824}'
