#!/usr/bin/env python3
"""Is decode slower when the prefix came from cache than when just computed?

Observed side-effect earlier: ~22.8 tok/s decode at conc 1 with a freshly
computed 48K prefix, but only ~10 tok/s at conc 1 with a cached one. Production
is 93.3% cache-hit and decodes at 3.96 tok/s, so the cached path is the one that
resembles production - making this potentially a bigger lever than parallelSlots.

Those two numbers were NOT comparable: different endpoints (/v1/chat vs
/v1/completions) and different generation lengths (64 vs 128). This holds
everything fixed and varies only cold-vs-cached.

Each pair: send a unique long prompt (cold, full prefill), then send THE SAME
prompt again (warm, prefix-cache hit). Identical endpoint, identical max_tokens.
"""
import json, os, sys, time, urllib.request

URL = os.environ.get("URL", "http://127.0.0.1:18000/v1/completions")
GEN = int(os.environ.get("GEN", "128"))
WORDS = ("storage replication consensus quorum latency throughput partition ledger "
         "checkpoint compaction manifest snapshot heartbeat gossip shard rebalance "
         "durability coordinator epoch lease tombstone compress index segment").split()


def make_prompt(nwords, salt):
    rnd, parts = salt, []
    for i in range(nwords):
        rnd = (rnd * 1103515245 + 12345 + i) & 0x7FFFFFFF
        parts.append(WORDS[rnd % len(WORDS)])
        if i % 18 == 17:
            parts.append(".\n")
    return f"Run {salt} unique. Technical corpus:\n" + " ".join(parts) + \
           "\n\nSummarize the corpus above in one sentence."


def run(prompt, tag):
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
    decode = (time.perf_counter() - t0) - ttft if ttft else 0
    rate = n / decode if decode else 0
    print(f"    {tag:>18}: TTFT={ttft:8.2f}s  decode {n:>3} tok in {decode:6.2f}s "
          f"= {rate:6.2f} tok/s   prompt={usage.get('prompt_tokens','?')}", flush=True)
    return rate


if __name__ == "__main__":
    nwords = int(sys.argv[1]) if len(sys.argv) > 1 else 38000
    pairs = int(sys.argv[2]) if len(sys.argv) > 2 else 2
    print(f"conc=1, gen={GEN}, ctx~{nwords} words, endpoint={URL}", flush=True)
    print("cold = unique prompt (full prefill); warm = same prompt repeated (cache hit)\n", flush=True)
    colds, warms = [], []
    for i in range(pairs):
        p = make_prompt(nwords, int(time.time_ns() % 10**9))
        print(f"  pair {i+1}:", flush=True)
        colds.append(run(p, "cold prefix"))
        warms.append(run(p, "warm (cached)"))
        time.sleep(3)
    mc = sum(colds) / len(colds)
    mw = sum(warms) / len(warms)
    print(f"\n  mean cold {mc:.2f} tok/s   mean warm {mw:.2f} tok/s", flush=True)
    if mw:
        print(f"  cached-prefix decode penalty: {mc/mw:.2f}x", flush=True)
