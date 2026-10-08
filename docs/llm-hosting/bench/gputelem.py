#!/usr/bin/env python3
"""amdgpu sysfs telemetry for A/B runs: how the card behaves, not just how fast a kernel is.

sample OUT [--interval 0.5]   append one JSON line per tick until OUT.stop exists (run it beside the bench)
summarize OUT [--work N --unit tok] [--fast-sclk 3100 --fast-mem-busy 60]
    mean/p50/p95 of sclk, mclk, gpu/mem busy, power, temp; joules (power x dt); work per joule;
    share of ticks in the fast band of ROCm#6347 (sclk >= --fast-sclk MHz while the GPU is busy).
Sysfs is node-wide, so run it with production paused or read the numbers as shared. No sysfs: exit non-zero.
"""
import argparse
import glob
import json
import os
import statistics
import sys
import time


def dev():
    p = glob.glob("/sys/class/drm/card*/device/mem_info_vram_total")
    if not p:
        sys.exit("gputelem: no amdgpu sysfs here")
    return os.path.dirname(p[0])


def rd(path, scale=1.0):
    try:
        return float(open(path).read().strip()) / scale
    except (OSError, ValueError):
        return None


def tick(d):
    hw = glob.glob(d + "/hwmon/hwmon*")[0]
    return {"t": time.time(),
            "sclk_mhz": rd(hw + "/freq1_input", 1e6), "mclk_mhz": rd(hw + "/freq2_input", 1e6),
            "gpu_busy": rd(d + "/gpu_busy_percent"), "mem_busy": rd(d + "/mem_busy_percent"),
            "power_w": rd(hw + "/power1_average", 1e6), "temp_c": rd(hw + "/temp1_input", 1e3),
            "fan_rpm": rd(hw + "/fan1_input"), "vram_used_gib": rd(d + "/mem_info_vram_used", 2**30)}


def sample(out, interval):
    d = dev()
    with open(out, "a") as f:
        while not os.path.exists(out + ".stop"):
            f.write(json.dumps(tick(d)) + "\n")
            f.flush()
            time.sleep(interval)


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def summarize(out, work, unit, fast_sclk, fast_mem_busy):
    rows = [json.loads(line) for line in open(out)]
    if len(rows) < 2:
        sys.exit("gputelem: fewer than 2 samples")
    dt = [b["t"] - a["t"] for a, b in zip(rows, rows[1:])]
    wall = rows[-1]["t"] - rows[0]["t"]
    s = {"samples": len(rows), "wall_s": round(wall, 1)}
    for k in ("sclk_mhz", "mclk_mhz", "gpu_busy", "mem_busy", "power_w", "temp_c"):
        xs = [r[k] for r in rows if r[k] is not None]
        if xs:
            s[k] = {"mean": round(statistics.fmean(xs), 1), "p50": round(pct(xs, .5), 1), "p95": round(pct(xs, .95), 1)}
    pw = [r["power_w"] for r in rows if r["power_w"] is not None]
    if pw:
        s["joules"] = round(sum(a["power_w"] * d for a, d in zip(rows, dt) if a["power_w"] is not None), 1)
    busy = [r for r in rows if (r["gpu_busy"] or 0) >= 50]
    s["busy_ticks"] = len(busy)
    if busy:
        s["fast_band_share"] = round(sum(1 for r in busy if (r["sclk_mhz"] or 0) >= fast_sclk
                                         or (r["mem_busy"] or 0) >= fast_mem_busy) / len(busy), 3)
    if work and s.get("joules"):
        s[f"{unit}_per_joule"] = round(work / s["joules"], 4)
    print(json.dumps(s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=("sample", "summarize"))
    ap.add_argument("out")
    ap.add_argument("--interval", type=float, default=0.5)
    ap.add_argument("--work", type=float, default=0)
    ap.add_argument("--unit", default="tok")
    ap.add_argument("--fast-sclk", type=float, default=3100)
    ap.add_argument("--fast-mem-busy", type=float, default=60)
    a = ap.parse_args()
    if a.mode == "sample":
        sample(a.out, a.interval)
    else:
        summarize(a.out, a.work, a.unit, a.fast_sclk, a.fast_mem_busy)


if __name__ == "__main__":
    main()
