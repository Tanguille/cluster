#!/usr/bin/env python3
"""Wall-clock decode rate, valid under speculative decoding.

longctx.py derives tok/s from vllm:inter_token_latency_seconds sum/count. Under
spec decode that counter records fewer events than tokens generated (184-426
seen for a 511-token generation), so its mean is a per-step figure, not a
per-token one, and the derived tok/s is wrong. This measures generated tokens
per second of wall clock after first token instead, which is method-agnostic.

PROMPT_FILE feeds a real prompt instead of the synthetic corpus, for measuring
n-gram/prompt-lookup on traffic-shaped input. A salt line is prepended so reps
cannot serve from the prefix cache.

Usage: walltime.py PORT MODEL [PROMPT_TOKENS] [REPS] [PROMPT_FILE] [GEN]
"""
import json, sys, time, urllib.request, statistics as st

from _metrics import sample, wait_idle

PORT, MODEL = int(sys.argv[1]), sys.argv[2]
PROMPT_TOKENS = int(sys.argv[3]) if len(sys.argv) > 3 else 4000
REPS = int(sys.argv[4]) if len(sys.argv) > 4 else 4
PROMPT_FILE = sys.argv[5] if len(sys.argv) > 5 else None
GEN = int(sys.argv[6]) if len(sys.argv) > 6 else 512
FILE_TEXT = open(PROMPT_FILE).read() if PROMPT_FILE else None
WORDS = ("storage replication consensus quorum latency throughput partition ledger "
         "checkpoint compaction manifest snapshot heartbeat gossip shard rebalance "
         "durability coordinator epoch lease tombstone compress index segment").split()


def make_prompt(salt):
    if FILE_TEXT is not None:
        return f"Session {salt} unique run identifier.\n{FILE_TEXT}"
    parts = [f"Session {salt} unique run identifier. Technical corpus follows.\n"]
    rnd = 0
    for i in range(PROMPT_TOKENS):
        rnd = (rnd * 1103515245 + 12345 + i) & 0x7FFFFFFF
        parts.append(WORDS[rnd % len(WORDS)])
        if i % 18 == 17:
            parts.append(".\n")
    parts.append("\n\nSummarize the corpus above in one sentence.")
    return " ".join(parts)


print(f"{'rep':>4} {'TTFT s':>8} {'decode s':>9} {'toks':>6} {'tok/s':>8} "
      f"{'accept%':>8} {'tok/step':>9} {'ms/step':>8}", flush=True)
rates, yields = [], []
for r in range(REPS):
    # One scrape serves both the idle gate and the pre-run counter baseline.
    before = wait_idle(PORT)
    if not before.idle:
        print(f"{r + 1:>4}  SKIPPED -- engine not idle, would contaminate", flush=True)
        continue
    body = json.dumps({
        "model": MODEL, "prompt": make_prompt(f"{time.time_ns()}-r{r}"),
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
    total = time.perf_counter() - t0
    after = sample(PORT)
    n = usage.get("completion_tokens", 0)
    decode_s = total - (ttft or 0)
    rate = (n - 1) / decode_s if decode_s > 0 and n else 0
    # A spec step emits its accepted drafts plus the one always-valid token; the
    # rest of the generation runs one token per step.
    drafts = after.drafts - before.drafts
    drafted = after.draft_tokens - before.draft_tokens
    acc = after.accepted - before.accepted
    spec_toks = acc + drafts
    steps = drafts + max(n - spec_toks, 0)
    ypst = n / steps if steps else 0
    ms = 1000 * decode_s / steps if steps else 0
    if rate:
        rates.append(rate)
        yields.append(ypst)
    print(f"{r + 1:>4} {ttft or 0:>8.2f} {decode_s:>9.2f} {n:>6} {rate:>8.2f} "
          f"{100 * acc / drafted if drafted else 0:>8.1f} {ypst:>9.2f} {ms:>8.1f}",
          flush=True)

if not rates:
    raise SystemExit("no clean reps -- engine never went idle, nothing measured")
print(f"\nn={len(rates)} clean   median wall-clock decode tok/s: "
      f"{st.median(rates):.2f}"
      f"   median tokens/step: {st.median(yields):.2f}", flush=True)
