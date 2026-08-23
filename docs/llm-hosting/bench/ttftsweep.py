#!/usr/bin/env python3
"""Measure prefill/TTFT with production-shaped prompts.

Counterpart to concsweep.py, which deliberately uses short prompts to isolate
decode. Production runs 42:1 prompt:completion at 47-56K prompt tokens, so TTFT
is the metric that decides tuning like maxNumBatchedTokens.

Unique salt in the FIRST tokens so prefix caching cannot serve any block - both
configs are compared cache-cold. Streaming, so TTFT is the real first-token time.
"""
import json, re, statistics, sys, threading, time, urllib.request

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
MODEL = sys.argv[2] if len(sys.argv) > 2 else "qwen-3.8"
PROMPT_WORDS = int(sys.argv[3]) if len(sys.argv) > 3 else 50000
URL = f"http://127.0.0.1:{PORT}/v1/completions"
METRICS_URL = f"http://127.0.0.1:{PORT}/metrics"
GEN = 32

WORDS = ("storage replication consensus quorum latency throughput partition ledger "
         "checkpoint compaction manifest snapshot heartbeat gossip shard rebalance "
         "durability coordinator epoch lease tombstone compress index segment").split()


def make_prompt(salt):
    # salt first: a distinct opening token block defeats prefix-cache reuse entirely.
    # PROMPT_WORDS counts words selected here, not tokenizer tokens - the real
    # per-request token count comes back in the response's usage.prompt_tokens.
    parts = [f"Session {salt} unique run identifier. Technical corpus follows.\n"]
    rnd = 0
    for i in range(PROMPT_WORDS):
        rnd = (rnd * 1103515245 + 12345 + i) & 0x7FFFFFFF
        parts.append(WORDS[rnd % len(WORDS)])
        if i % 18 == 17:
            parts.append(".\n")
    parts.append("\n\nSummarize the corpus above in one sentence.")
    return " ".join(parts)


def check_idle():
    """Abort the sweep if the engine has in-flight work - production traffic
    contaminates TTFT/throughput otherwise (see the methodology lesson in
    vllm-optimization-log-2026-08.md: gate every benchmark on 0 running / 0 waiting)."""
    try:
        with urllib.request.urlopen(METRICS_URL, timeout=10) as r:
            text = r.read().decode()
    except Exception as e:
        print(f"IDLE CHECK FAILED: could not reach {METRICS_URL}: {e}", file=sys.stderr)
        sys.exit(1)
    running = sum(float(v) for v in re.findall(r"vllm:num_requests_running\{[^}]*\}\s+(\S+)", text))
    waiting = sum(float(v) for v in re.findall(r"vllm:num_requests_waiting\{[^}]*\}\s+(\S+)", text))
    if running != 0 or waiting != 0:
        print(f"ENGINE NOT IDLE: running={running} waiting={waiting} - aborting, results would be contaminated", file=sys.stderr)
        sys.exit(1)


def stream(salt, t_launch, out, lk):
    body = json.dumps({
        "model": MODEL, "prompt": make_prompt(salt), "max_tokens": GEN,
        "temperature": 0.7, "ignore_eos": True, "stream": True,
        "stream_options": {"include_usage": True},
    }).encode()
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
                        ttft = time.perf_counter() - t_launch
                    ntok += 1
        completed = time.perf_counter() - t_launch
        details = usage.get("prompt_tokens_details")
        # Preserve "field absent" as unavailable rather than coercing to a
        # miss - the manifest doesn't currently pass --enable-prompt-tokens-details,
        # so this will read as unavailable until that flag is set.
        cached = details.get("cached_tokens") if details is not None else None
        with lk:
            out.append({"ttft": ttft, "completed": completed, "gen": ntok,
                        "prompt_tokens": usage.get("prompt_tokens", 0),
                        "cached": cached})
    except Exception as e:
        with lk:
            out.append({"err": str(e)[:120]})


check_idle()
print(f"target prompt words: {PROMPT_WORDS}  model: {MODEL}  gen: {GEN}")
print(f"{'conc':>5} {'ttft med s':>11} {'ttft p90 s':>11} {'PP tok/s':>10} {'TG tok/s':>9} {'cached':>9} {'ok':>4}")
for c in [1, 4]:
    out, lk, th = [], threading.Lock(), []
    t_launch = time.perf_counter()  # shared launch barrier - see the aggregate-rate note below
    for i in range(c):
        t = threading.Thread(target=stream, args=(f"{time.time_ns()}-c{c}s{i}", t_launch, out, lk))
        t.start()
        th.append(t)
    for t in th:
        t.join()
    ok = [o for o in out if o.get("ttft")]
    if len(ok) != c:
        err = next((o.get("err") for o in out if o.get("err")), "incomplete: some streams never got a token")
        print(f"{c:>5}  FAILED ({len(ok)}/{c} completed): {err}", flush=True)
        continue
    tt = sorted(o["ttft"] for o in ok)
    med = statistics.median(tt)
    p90 = tt[min(len(tt) - 1, int(len(tt) * 0.9))]
    # All streams share t_launch, so both rates are total work over the
    # actual wall-clock span from launch - not a sum of per-stream rates,
    # which would overcount overlap, and not max(ttft) alone for TG, which
    # ignores each stream's own completion time.
    pp = sum(o["prompt_tokens"] for o in ok) / max(o["ttft"] for o in ok)
    tg = sum(o["gen"] for o in ok) / max(o["completed"] for o in ok)
    cached_vals = [o["cached"] for o in ok if o["cached"] is not None]
    cached = sum(cached_vals) if cached_vals else "n/a"
    print(f"{c:>5} {med:>11.2f} {p90:>11.2f} {pp:>10.1f} {tg:>9.2f} {cached:>9} {len(ok):>4}", flush=True)
    time.sleep(5)
