#!/usr/bin/env python3
"""Long-context decode at M=1 -- the regime production actually runs.

concsweep.py uses short prompts to isolate decode; that is the wrong shape for
the 50-70K attention-backend question. Unique salt first so the prefix cache
cannot serve any block.

Decode speed is read from the server's own inter_token_latency metric, not from
client stream timing: urllib's BufferedReader coalesces SSE events, which made a
client-side measurement report 47000 tok/s at 0.0 ms ITL.

Per rep it reports the EXACT mean ITL from the sum/count counter deltas, plus
the share of tokens past the 50 and 75 ms bucket edges. The tail shares are what
separate a uniformly slower kernel from occasional stalls; percentiles are
deliberately not interpolated out of the buckets, because the edges
(.01/.025/.05/.075/.1) are coarse enough that an interpolated p50 snaps to a
bucket midpoint and invents precision.

Usage: longctx.py PORT MODEL [PROMPT_TOKENS] [REPS] [FIXED_SALT]
"""
import json, sys, time, urllib.request, statistics as st

PORT = int(sys.argv[1])
MODEL = sys.argv[2]
PROMPT_TOKENS = int(sys.argv[3]) if len(sys.argv) > 3 else 50000
REPS = int(sys.argv[4]) if len(sys.argv) > 4 else 3
# Fixed salt (arg 5) makes rep>=2 hit the prefix cache, which separates connector
# store traffic from warm-state effects; omit it for the cold-prompt default.
FIXED_SALT = sys.argv[5] if len(sys.argv) > 5 else None
GEN = 512
WORDS = ("storage replication consensus quorum latency throughput partition ledger "
         "checkpoint compaction manifest snapshot heartbeat gossip shard rebalance "
         "durability coordinator epoch lease tombstone compress index segment").split()
ITL = "vllm:inter_token_latency_seconds"


def make_prompt(salt):
    parts = [f"Session {salt} unique run identifier. Technical corpus follows.\n"]
    rnd = 0
    for i in range(PROMPT_TOKENS):
        rnd = (rnd * 1103515245 + 12345 + i) & 0x7FFFFFFF
        parts.append(WORDS[rnd % len(WORDS)])
        if i % 18 == 17:
            parts.append(".\n")
    parts.append("\n\nSummarize the corpus above in one sentence.")
    return " ".join(parts)


def scrape():
    """Return (cumulative ITL buckets as {le: count}, count, sum, running, waiting)."""
    buckets, cnt, tot, run, wait = {}, 0.0, 0.0, -1.0, -1.0
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/metrics", timeout=15) as r:
        for ln in r.read().decode().splitlines():
            if ln.startswith("#"):
                continue
            val = ln.rsplit(" ", 1)[-1]
            try:
                v = float(val)
            except ValueError:
                continue
            if ln.startswith(ITL + "_bucket"):
                le = ln.split('le="', 1)[1].split('"', 1)[0]
                buckets[float(le)] = v
            elif ln.startswith(ITL + "_count"):
                cnt = v
            elif ln.startswith(ITL + "_sum"):
                tot = v
            elif ln.startswith("vllm:num_requests_running"):
                run = v
            elif ln.startswith("vllm:num_requests_waiting"):
                wait = v
    return buckets, cnt, tot, run, wait


def wait_idle(label):
    # Two consecutive idle reads 5s apart: one read can land in the gap between
    # two production requests and report a false idle.
    calm = 0
    for _ in range(120):
        _, _, _, run, wait = scrape()
        calm = calm + 1 if run == 0 and wait == 0 else 0
        if calm >= 2:
            return True
        time.sleep(5)
    print(f"  !! {label}: engine never went idle", flush=True)
    return False


print(f"{'rep':>4} {'prompt_tok':>11} {'TTFT s':>8} {'tok/s':>10} "
      f"{'ITL ms':>9} {'>50ms %':>8} {'>75ms %':>8} {'toks':>6} {'cached':>7}", flush=True)
meds = []
for r in range(REPS):
    clean = wait_idle(f"rep {r + 1}")
    b0, c0, s0, _, _ = scrape()
    body = json.dumps({
        "model": MODEL, "prompt": make_prompt(FIXED_SALT or f"{time.time_ns()}-r{r}"),
        "max_tokens": GEN, "temperature": 0.7, "ignore_eos": True,
        "stream": True, "stream_options": {"include_usage": True},
    }).encode()
    t0 = time.perf_counter()
    ttft, usage = None, {}
    rq = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/completions", data=body,
                                headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=1800) as resp:
        for raw in resp:
            if not raw.startswith(b"data: "):
                continue
            chunk = raw[6:].strip()
            if chunk == b"[DONE]":
                break
            d = json.loads(chunk)
            if d.get("usage"):
                usage = d["usage"]
            if ttft is None and d.get("choices") and d["choices"][0].get("text"):
                ttft = time.perf_counter() - t0
    b1, c1, s1, _, _ = scrape()

    n = c1 - c0
    delta = {le: b1.get(le, 0) - b0.get(le, 0) for le in b1}
    # Mean from the sum/count counters is exact. The histogram's coarse edges
    # (.025/.05/.075) make an interpolated p50 snap to bucket midpoints, which
    # invents precision -- so buckets are only used for the tail share below.
    mean = (s1 - s0) / n if n else 0
    over75 = (n - delta.get(0.075, 0)) / n if n else 0
    over50 = (n - delta.get(0.05, 0)) / n if n else 0
    if clean and mean:
        meds.append(1 / mean)
    cached = usage.get("prompt_tokens_details", {}).get("cached_tokens", 0)
    print(f"{r + 1:>4} {usage.get('prompt_tokens', 0):>11} {ttft or 0:>8.2f} "
          f"{1 / mean if mean else 0:>10.2f} {mean * 1000:>9.2f} {over50 * 100:>8.1f} "
          f"{over75 * 100:>8.1f} {n:>6.0f} {cached:>7}{'' if clean else '  DIRTY'}",
          flush=True)

print(f"\nn={len(meds)} clean   median-of-reps tok/s: "
      f"{st.median(meds) if meds else 0:.2f}", flush=True)
