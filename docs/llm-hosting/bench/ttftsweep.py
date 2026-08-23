#!/usr/bin/env python3
"""Measure prefill/TTFT with production-shaped prompts.

Counterpart to concsweep.py, which deliberately uses short prompts to isolate
decode. Production runs 42:1 prompt:completion at 47-56K prompt tokens, so TTFT
is the metric that decides tuning like maxNumBatchedTokens.

Unique salt in the FIRST tokens so prefix caching cannot serve any block - both
configs are compared cache-cold. Streaming, so TTFT is the real first-token time.
"""
import json, statistics, sys, threading, time, urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
MODEL = sys.argv[2] if len(sys.argv) > 2 else "qwen-3.8"
PROMPT_TOKENS = int(sys.argv[3]) if len(sys.argv) > 3 else 50000
URL = f"http://127.0.0.1:{PORT}/v1/completions"
GEN = 32

WORDS = ("storage replication consensus quorum latency throughput partition ledger "
         "checkpoint compaction manifest snapshot heartbeat gossip shard rebalance "
         "durability coordinator epoch lease tombstone compress index segment").split()


def make_prompt(salt):
    # salt first: a distinct opening token block defeats prefix-cache reuse entirely.
    parts = [f"Session {salt} unique run identifier. Technical corpus follows.\n"]
    rnd = 0
    for i in range(PROMPT_TOKENS):
        rnd = (rnd * 1103515245 + 12345 + i) & 0x7FFFFFFF
        parts.append(WORDS[rnd % len(WORDS)])
        if i % 18 == 17:
            parts.append(".\n")
    parts.append("\n\nSummarize the corpus above in one sentence.")
    return " ".join(parts)


def stream(salt, out, lk):
    body = json.dumps({
        "model": MODEL, "prompt": make_prompt(salt), "max_tokens": GEN,
        "temperature": 0.7, "ignore_eos": True, "stream": True,
        "stream_options": {"include_usage": True},
    }).encode()
    t0 = time.perf_counter()
    ttft, ntok, usage = None, 0, {}
    try:
        rq = urllib.request.Request(URL, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(rq, timeout=1800) as r:
            for raw in r:
                if not raw.startswith(b"data: "):
                    continue
                chunk = raw[6:].strip()
                if chunk == b"[DONE]":
                    break
                d = json.loads(chunk)
                if d.get("usage"):
                    usage = d["usage"]
                if d.get("choices") and d["choices"][0].get("text"):
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    ntok += 1
        el = time.perf_counter() - t0
        with lk:
            out.append({"ttft": ttft, "total": el, "gen": ntok,
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "cached": usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)})
    except Exception as e:
        with lk:
            out.append({"err": str(e)[:120]})


print(f"target prompt tokens: {PROMPT_TOKENS}  model: {MODEL}  gen: {GEN}")
print(f"{'conc':>5} {'ttft med s':>11} {'ttft p90 s':>11} {'PP tok/s':>10} {'TG tok/s':>9} {'cached':>7} {'ok':>4}")
for c in [1, 4]:
    out, lk, th = [], threading.Lock(), []
    for i in range(c):
        t = threading.Thread(target=stream, args=(f"{time.time_ns()}-c{c}s{i}", out, lk))
        t.start()
        th.append(t)
    for t in th:
        t.join()
    ok = [o for o in out if o.get("ttft")]
    if not ok:
        print(f"{c:>5}  FAILED: {out[0].get('err','no tokens')}", flush=True)
        continue
    tt = sorted(o["ttft"] for o in ok)
    med = statistics.median(tt)
    p90 = tt[min(len(tt) - 1, int(len(tt) * 0.9))]
    # PP aggregated: all streams prefill concurrently, so divide by slowest TTFT.
    pp = sum(o["prompt_tokens"] for o in ok) / max(tt)
    tg = sum(o["gen"] / (o["total"] - o["ttft"]) for o in ok if o["total"] > o["ttft"])
    cached = sum(o["cached"] for o in ok)
    print(f"{c:>5} {med:>11.2f} {p90:>11.2f} {pp:>10.1f} {tg:>9.2f} {cached:>7} {len(ok):>4}", flush=True)
    time.sleep(5)
