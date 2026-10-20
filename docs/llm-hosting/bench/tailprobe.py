"""TTFT vs recomputed tail on a cached prefix. Prefix = BLOCKS*1536 token ids, so the hit is exact.
usage: tailprobe.py PORT [BLOCKS=32] [TAILS=1,128,361,768,1001,1535,3000]"""
import json
import random
import sys
import time
import urllib.request

port = int(sys.argv[1])
blocks = int(sys.argv[2]) if len(sys.argv) > 2 else 32
tails = [int(x) for x in (sys.argv[3] if len(sys.argv) > 3 else "1,128,361,768,1001,1535,3000").split(",")]
base_url = f"http://127.0.0.1:{port}"
rnd = random.Random(7)
base = [rnd.randrange(1000, 60000) for _ in range(blocks * 1536)]


def metric(name):
    t = urllib.request.urlopen(f"{base_url}/metrics", timeout=5).read().decode()
    return sum(float(ln.rsplit(" ", 1)[1]) for ln in t.splitlines() if ln.startswith(name + "{"))


def wait_idle():
    calm = 0
    while calm < 3:
        calm = calm + 1 if metric("vllm:num_requests_running") + metric("vllm:num_requests_waiting") == 0 else 0
        time.sleep(2)


def run(ids):
    body = json.dumps({"model": "qwen-3.8", "prompt": ids, "max_tokens": 1, "temperature": 0}).encode()
    q0, h0 = metric("vllm:prefix_cache_queries_total"), metric("vllm:prefix_cache_hits_total")
    t = time.perf_counter()
    urllib.request.urlopen(urllib.request.Request(f"{base_url}/v1/completions", data=body,
                                                  headers={"Content-Type": "application/json"}), timeout=1800).read()
    dt = time.perf_counter() - t
    return dt, metric("vllm:prefix_cache_hits_total") - h0, metric("vllm:prefix_cache_queries_total") - q0


wait_idle()
dt, hit, _ = run(base + [42])
print(f"warm  prompt={len(base) + 1} t={dt:.2f}s hit={hit:.0f}", flush=True)
print(f"{'tail':>5} {'ttft_s':>7} {'hit':>7} {'recompute':>9}")
for tail in tails:
    for _ in range(2):
        wait_idle()
        dt, hit, q = run(base + [rnd.randrange(1000, 60000) for _ in range(tail)])
        print(f"{tail:>5} {dt:>7.3f} {hit:>7.0f} {q - hit:>9.0f}", flush=True)
