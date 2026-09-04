#!/usr/bin/env python3
"""Aggregate decode throughput vs concurrency AT PRODUCTION CONTEXT LENGTH.

The gap this fills: concsweep.py measured 14.88/53.93/100.82 agg tok/s at
conc 1/8/16 using SHORT prompts, i.e. batching clearly helped. But the W4A16
RDNA kernel has a documented LDS gate at M=2 (down_proj K=17408 falls off the
fast path) and a dispatch cliff at M=6. Nobody has measured whether batching
still wins once each sequence carries a ~50K context, which is what production
actually runs at ~3.5 concurrency.

Method: one warmup populates the prefix cache with a fixed long prompt, then
every stream in every round reuses THAT SAME prompt, so prefill is a cache hit
and what we measure is decode. Concurrency is the only variable.

If aggregate tok/s peaks at conc 1-2, parallelSlots: 6 is costing throughput.
"""
import json, sys, threading, time, urllib.request

URL = "http://127.0.0.1:18000/v1/completions"
METRICS = "http://127.0.0.1:18000/metrics"
GEN = 128
WORDS = ("storage replication consensus quorum latency throughput partition ledger "
         "checkpoint compaction manifest snapshot heartbeat gossip shard rebalance "
         "durability coordinator epoch lease tombstone compress index segment").split()


def build_prompt(nwords):
    # Fixed seed on purpose: every request in this sweep must hit the same
    # cached prefix, otherwise each stream pays a ~280s cold prefill and the
    # run measures prefill contention instead of decode.
    rnd, parts = 12345, []
    for i in range(nwords):
        rnd = (rnd * 1103515245 + 12345 + i) & 0x7FFFFFFF
        parts.append(WORDS[rnd % len(WORDS)])
        if i % 18 == 17:
            parts.append(".\n")
    return "Fixed sweep corpus.\n" + " ".join(parts) + "\n\nSummarize in one sentence."


def engine_busy():
    try:
        with urllib.request.urlopen(METRICS, timeout=5) as r:
            t = r.read().decode()
        import re
        run = re.search(r"^vllm:num_requests_running\{[^}]*\}\s+(\S+)", t, re.M)
        return float(run.group(1)) if run else -1
    except Exception:
        return -1


def stream(prompt, t0, out, lk):
    req = json.dumps({
        "model": "qwen-3.8", "prompt": prompt, "max_tokens": GEN,
        "temperature": 0.7, "ignore_eos": True, "stream": True,
        "stream_options": {"include_usage": True},
    }).encode()
    ttft, n, usage = None, 0, {}
    try:
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
        with lk:
            out.append({"ttft": ttft, "done": time.perf_counter() - t0, "gen": n,
                        "prompt": usage.get("prompt_tokens", 0),
                        "cached": (usage.get("prompt_tokens_details") or {}).get("cached_tokens")})
    except Exception as e:
        with lk:
            out.append({"err": str(e)[:110]})


if __name__ == "__main__":
    nwords = int(sys.argv[1]) if len(sys.argv) > 1 else 38000
    levels = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["1", "2", "4", "6"])]
    prompt = build_prompt(nwords)

    print(f"warmup: populating prefix cache with the fixed ~{nwords}-word prompt "
          f"(one cold prefill, expect minutes)...", flush=True)
    w0 = time.perf_counter()
    out, lk = [], threading.Lock()
    stream(prompt, w0, out, lk)
    if out and out[0].get("err"):
        sys.exit(f"warmup failed: {out[0]['err']}")
    print(f"  warmup done in {time.perf_counter()-w0:.1f}s, prompt={out[0]['prompt']} tok", flush=True)

    print(f"\n{'conc':>5} {'agg tok/s':>10} {'per-stream':>11} {'TTFT med':>9} {'busy@start':>11} {'ok':>4}")
    for c in levels:
        b = engine_busy()
        out, lk, th = [], threading.Lock(), []
        t0 = time.perf_counter()
        for _ in range(c):
            t = threading.Thread(target=stream, args=(prompt, t0, out, lk))
            t.start(); th.append(t)
        for t in th:
            t.join()
        ok = [o for o in out if o.get("gen")]
        if len(ok) != c:
            print(f"{c:>5}  FAILED ({len(ok)}/{c}): "
                  f"{next((o.get('err') for o in out if o.get('err')), 'incomplete')}", flush=True)
            continue
        span = max(o["done"] for o in ok)
        agg = sum(o["gen"] for o in ok) / span
        ttfts = sorted(o["ttft"] for o in ok)
        print(f"{c:>5} {agg:>10.2f} {agg/c:>11.2f} {ttfts[len(ttfts)//2]:>9.2f} {b:>11.1f} {len(ok):>4}",
              flush=True)
        time.sleep(5)
