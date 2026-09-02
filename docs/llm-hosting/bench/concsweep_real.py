#!/usr/bin/env python3
"""Decode sweep over concurrencies that actually exist on this engine.

concsweep.py hardcodes [1, 8, 16], but max_num_seqs=6, so 8 and 16 both measure
M=6 with a queue and their per-stream column is aggregate/N -- meaningless.
This sweeps 1..6 so every point maps to a real batch size M, including M=2 where
down_proj (K=17408) falls off the HIP skinny GEMM (K*M > 32768 LDS).

Usage: concsweep_real.py PORT MODEL [concurrencies]
"""
import json
import sys
import threading
import time
import urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
MODEL = sys.argv[2] if len(sys.argv) > 2 else "qwen-3.8"
CONCS = [int(x) for x in sys.argv[3].split(",")] if len(sys.argv) > 3 else [1, 2, 3, 4, 5, 6]
URL = f"http://127.0.0.1:{PORT}/v1/completions"
GEN = 96


def stream(salt, out, lk):
    body = json.dumps({
        "model": MODEL,
        "prompt": f"Run {salt}. Write a long detailed essay about distributed storage systems.",
        "max_tokens": GEN, "temperature": 0.7, "ignore_eos": True,
    }).encode()
    t0 = time.perf_counter()
    try:
        rq = urllib.request.Request(URL, data=body,
                                    headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(rq, timeout=1200) as r:
            d = json.load(r)
        el = time.perf_counter() - t0
        with lk:
            out.append((d["usage"]["completion_tokens"], el))
    except Exception:
        pass


print(" conc  agg tok/s  per-stream   wall s   ok")
for c in CONCS:
    out, lk = [], threading.Lock()
    base = int(time.time() * 1000)
    t0 = time.perf_counter()
    ths = [threading.Thread(target=stream, args=(base + i, out, lk)) for i in range(c)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    wall = time.perf_counter() - t0
    tot = sum(n for n, _ in out)
    agg = tot / wall if wall else 0
    print("%5d %10.2f %11.2f %8.1f %4d" % (c, agg, agg / c if c else 0, wall, len(out)))
    time.sleep(3)
