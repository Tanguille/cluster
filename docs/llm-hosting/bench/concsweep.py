#!/usr/bin/env python3
"""Measure the real decode ceiling by concurrency sweep.

Short prompts on purpose: isolates DECODE from prefill. Unique salt per stream so
prefix caching cannot fake the numbers. Aggregate tok/s is the number that matters -
the owner cares about concurrency, not batch-1 latency.
"""
import json, sys, time, threading, urllib.request

URL = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8000}/v1/completions"
MODEL = "qwen-3.6"
GEN = 96


def stream(salt, out, lk):
    body = json.dumps({
        "model": MODEL,
        "prompt": f"Run {salt}. Write a long detailed essay about distributed storage systems.",
        "max_tokens": GEN, "temperature": 0.7, "ignore_eos": True,
    }).encode()
    t0 = time.perf_counter()
    try:
        rq = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(rq, timeout=1200) as r:
            d = json.load(r)
        el = time.perf_counter() - t0
        n = d["usage"]["completion_tokens"]
        with lk:
            out.append((n, el))
    except Exception as e:
        with lk:
            out.append((0, time.perf_counter() - t0, str(e)[:80]))


print(f"{'conc':>5} {'agg tok/s':>10} {'per-stream':>11} {'wall s':>8} {'ok':>4}")
for c in [1, 8, 16]:
    out, lk, th = [], threading.Lock(), []
    t0 = time.perf_counter()
    for i in range(c):
        t = threading.Thread(target=stream, args=(f"c{c}s{i}", out, lk))
        t.start()
        th.append(t)
    for t in th:
        t.join()
    wall = time.perf_counter() - t0
    ok = [o for o in out if o[0] > 0]
    tot = sum(o[0] for o in ok)
    agg = tot / wall if wall else 0
    per = agg / c if c else 0
    print(f"{c:>5} {agg:>10.2f} {per:>11.2f} {wall:>8.1f} {len(ok):>4}", flush=True)
    time.sleep(4)
