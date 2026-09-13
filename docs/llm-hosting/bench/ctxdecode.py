#!/usr/bin/env python3
"""Does decode rate collapse with context length?

All our tuning benchmarks (concsweep.py, the maxModelLen bisect) use SHORT
prompts and report ~31 tok/s at conc 1. Production averages 52,290-token
prompts and shows 3.8 tok/s at the same concurrency. Same engine, same
concurrency, only context differs - this measures that difference directly.
"""
import json, sys, time, urllib.request

# Port 18000 assumes a `kubectl port-forward` to the vLLM pod.
URL = "http://127.0.0.1:18000/v1/completions"
GEN = 64
WORDS = ("storage replication consensus quorum latency throughput partition ledger "
         "checkpoint compaction manifest snapshot heartbeat gossip shard rebalance "
         "durability coordinator epoch lease tombstone compress index segment").split()


def make_prompt(nwords, salt):
    # Salt first so prefix caching cannot serve any block - every run is
    # measured cache-cold, otherwise a warm prefix would flatter the long case.
    rnd, parts = salt, []
    for i in range(nwords):
        rnd = (rnd * 1103515245 + 12345 + i) & 0x7FFFFFFF
        parts.append(WORDS[rnd % len(WORDS)])
        if i % 18 == 17:
            parts.append(".\n")
    return f"Run {salt} unique. Technical corpus:\n" + " ".join(parts) + \
           "\n\nSummarize the corpus above in one sentence."


def run(nwords, label):
    prompt = make_prompt(nwords, int(time.time_ns() % 10**9))
    req = json.dumps({
        "model": "qwen-3.8", "prompt": prompt, "max_tokens": GEN,
        "temperature": 0.7, "ignore_eos": True, "stream": True,
        "stream_options": {"include_usage": True},
    }).encode()
    t0 = time.perf_counter()
    ttft, n, usage = None, 0, {}
    r = urllib.request.Request(URL, data=req, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=1800) as resp:
        for raw in resp:
            if not raw.startswith(b"data: "):
                continue
            c = raw[6:].strip()
            if c == b"[DONE]":
                break
            d = json.loads(c)
            if d.get("usage"):
                usage = d["usage"]
            if d.get("choices") and d["choices"][0].get("text"):
                if ttft is None:
                    ttft = time.perf_counter() - t0
                n += 1
    # usage is authoritative: an SSE chunk is not guaranteed to carry exactly one token.
    n = usage.get("completion_tokens") or n
    total = time.perf_counter() - t0
    decode = total - ttft if ttft else 0
    rate = n / decode if decode else 0
    print(f"  {label:>14}: prompt={usage.get('prompt_tokens','?'):>7} tok  "
          f"TTFT={ttft:7.2f}s  decode {n:>3} tok in {decode:6.2f}s = {rate:5.2f} tok/s",
          flush=True)
    return rate


if __name__ == "__main__":
    print(f"conc=1, gen={GEN}, cache-cold (salted). Engine must be otherwise idle.", flush=True)
    short = run(300, "short ~0.4K")
    long_ = run(38000, "long ~52K")
    if short and long_:
        print(f"\n  decode slowdown at production context: {short/long_:.1f}x", flush=True)
