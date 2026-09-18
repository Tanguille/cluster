#!/usr/bin/env python3
"""KV offload tier throughput from the connector's own counters, per direction.

fs tier reads/writes (secondary <-> CPU), CPU <-> GPU loads/stores, plus the
promotion queue depth and lookup delays over the window. Rates are bytes
divided by the connector's own busy time, so they are per-transfer throughput,
not wall-clock averages.

Usage: prod_kvtier.py [WINDOW]   (needs the VM port-forward on 18428)
"""
import json, sys, urllib.parse, urllib.request

W = sys.argv[1] if len(sys.argv) > 1 else "30m"
SEL = '{model="qwen38-27b-vllm"}'


def q(expr):
    u = "http://127.0.0.1:18428/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    r = json.load(urllib.request.urlopen(u))["data"]["result"]
    return float(r[0]["value"][1]) if r else float("nan")


def inc(m):
    return q(f"sum(increase({m}{SEL}[{W}]))")


for label, b, t in (("fs tier read  (fs->cpu)", "vllm:kv_offload_tiering_read_bytes_total", "vllm:kv_offload_tiering_read_time_total"),
                    ("fs tier write (cpu->fs)", "vllm:kv_offload_tiering_write_bytes_total", "vllm:kv_offload_tiering_write_time_total"),
                    ("load  (cpu->gpu)", "vllm:kv_offload_load_bytes_total", "vllm:kv_offload_load_time_total"),
                    ("store (gpu->cpu)", "vllm:kv_offload_store_bytes_total", "vllm:kv_offload_store_time_total")):
    by, tm = inc(b), inc(t)
    print(f"{label:24s} {by / 1e9:8.1f} GB in {tm:7.1f} s busy = {by / tm / 1e9 if tm else 0:6.2f} GB/s")
print(f"tier chunk hits/queries    {inc('vllm:kv_offload_tiering_chunk_hits_total'):.0f} / "
      f"{inc('vllm:kv_offload_tiering_chunk_queries_total'):.0f}")
print(f"active promotion jobs      avg {q(f'avg_over_time(vllm:kv_offload_tiering_active_promotion_jobs{SEL}[{W}])'):.2f} "
      f"max {q(f'max_over_time(vllm:kv_offload_tiering_active_promotion_jobs{SEL}[{W}])'):.0f}")
for m in ("vllm:kv_offload_tiering_lookup_async_delay_seconds", "vllm:kv_offload_lookup_async_delay_seconds"):
    c, s = inc(m + "_count"), inc(m + "_sum")
    print(f"{m.split(':')[1]:45s} n={c:.0f} mean {s / c if c else 0:.2f} s")
