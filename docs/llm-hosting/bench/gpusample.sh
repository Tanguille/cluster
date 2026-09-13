#!/usr/bin/env bash
# 20 samples, 3 s apart: clocks, power, temps, mem_busy against running requests.
# Run inside the vLLM pod (sysfs + localhost:8000/metrics).
set -euo pipefail
g=/sys/class/drm/card1/device
hw=$(find "$g/hwmon" -mindepth 1 -maxdepth 1 -name 'hwmon*' | head -1)
for _ in $(seq 20); do
    run=$(curl -s localhost:8000/metrics | awk '/^vllm:num_requests_running/{print $2}')
    mclk=$(awk '/\*/{print $2}' "$g/pp_dpm_mclk")
    fclk=$(awk '/\*/{print $2}' "$g/pp_dpm_fclk")
    sclk=$(awk '/\*/{print $2}' "$g/pp_dpm_sclk")
    w=$(cat "$hw/power1_average" 2>/dev/null || cat "$hw/power1_input")
    tj=$(cat "$hw/temp2_input")
    tm=$(cat "$hw/temp3_input")
    echo "$(date +%T) run=$run mclk=$mclk fclk=$fclk sclk=$sclk W=$((w / 1000000)) Tj=$((tj / 1000)) Tm=$((tm / 1000)) membusy=$(cat "$g/mem_busy_percent")"
    sleep 3
done
echo
amd-smi metric -g 0 --throttle 2>&1 | head -40
