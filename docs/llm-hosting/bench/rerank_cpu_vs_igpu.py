#!/usr/bin/env python3
"""Interleaved iGPU vs CPU /v1/rerank latency and score parity.

Usage: IGPU_URL=... CPU_URL=... python3 -I rerank_cpu_vs_igpu.py [N]
URLs default to kubectl port-forwards on 18081 (iGPU) and 18082 (CPU).
"""
import json, glob, os, statistics as st, sys, time, urllib.request

REPO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..")
QUERY = "why is vllm decode throughput capped on the rdna4 gpu"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 15
URLS = {
    "igpu": os.environ.get("IGPU_URL", "http://127.0.0.1:18081/v1/rerank"),
    "cpu": os.environ.get("CPU_URL", "http://127.0.0.1:18082/v1/rerank"),
}

def docs(chars, n=12):
    out = []
    for f in sorted(glob.glob(f"{REPO}/docs/**/*.md", recursive=True)):
        t = " ".join(open(f, errors="ignore").read().split())
        for i in range(0, len(t) - chars, chars * 7):
            out.append(t[i:i + chars])
            if len(out) == n:
                return out
    raise SystemExit("not enough docs")

def call(url, d):
    body = json.dumps({"model": "bge-reranker-v2-m3", "query": QUERY, "documents": d}).encode()
    t = time.perf_counter()
    r = json.load(urllib.request.urlopen(urllib.request.Request(url, body, {"Content-Type": "application/json"}), timeout=120))
    return time.perf_counter() - t, [x["relevance_score"] for x in sorted(r["results"], key=lambda x: x["index"])]

for chars in (1024, 300):
    d = docs(chars)
    res = {k: [] for k in URLS}
    scores = {}
    for k in URLS:  # warmup
        call(URLS[k], d)
    for _ in range(N):
        for k in URLS:  # interleaved so drift/live traffic hits both
            dt, scores[k] = call(URLS[k], d)
            res[k].append(dt)
    print(f"\n== 12 docs x {chars} chars, n={N} each ==")
    for k, v in res.items():
        v.sort()
        print(f"{k:5} p50={st.median(v):.2f}s  max={v[-1]:.2f}s")
    diffs = [abs(a - b) for a, b in zip(scores["igpu"], scores["cpu"])]
    rank = lambda s: sorted(range(len(s)), key=lambda i: -s[i])
    print(f"score parity: max|diff|={max(diffs):.4f}  same ranking={rank(scores['igpu']) == rank(scores['cpu'])}")
    for k in URLS:
        print(f"{k:5}", [round(x, 3) for x in scores[k]])
