#!/usr/bin/env python3
"""Acceptance benchmark: production-shaped multi-turn agent sessions.

Each concurrent stream is an independent session with its OWN random prefix (longconcsweep.py shares one,
which flatters caching and batching). Per session: one cold turn (reported separately), then --turns steady
turns, each appending the previous reply plus a fresh tail, so only reply + tail are computed. Defaults follow
the 30-day production mix (docs/llm-hosting/ultraquant-2026-10-07.md, Workload).

--warmup runs one untimed pass per level; --runs N repeats each level with fresh seeds and reports the median
plus the max-min spread (the noise floor). Run it inside the pod (--port 8000): port-forwards stall after
30-60 min. The engine must be otherwise idle; --dry-run prints the plan.
"""
import argparse, json, os, random, re, statistics, subprocess, sys, threading, time
import urllib.request

from _metrics import sample, wait_idle

# Same vocabulary as the other bench scripts; ~1.17 tokens/word on this tokenizer.
WORDS = ("storage replication consensus quorum latency throughput partition ledger "
         "checkpoint compaction manifest snapshot heartbeat gossip shard rebalance "
         "durability coordinator epoch lease tombstone compress index segment").split()
DEFAULT_TPW = 1.17


def kint(s):
    s = s.strip().lower()
    return int(float(s[:-1]) * 1000) if s.endswith("k") else int(s)


def pct(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    i = (len(xs) - 1) * p / 100
    lo = int(i)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def r2(x):
    return None if x is None else round(x, 3)


def words(rng, n):
    out = []
    for i in range(n):
        out.append(rng.choice(WORDS))
        if i % 18 == 17:
            out.append(".\n")
    return " ".join(out)


class Session:
    def __init__(self, seed, prefix_tok, tail_tok, tpw):
        self.seed, self.tail_tok, self.tpw = seed, tail_tok, tpw
        # Seed in the first line makes the first cache block unique per session.
        self.prompt = (f"Session {seed} agent transcript.\n"
                       + words(random.Random(seed), int(prefix_tok / tpw)))
        self.turn = 0
        self.reply = ""

    def next_prompt(self):
        """Cold turn returns the bare prefix; later turns append the last reply and a fresh tail."""
        if self.turn:
            rng = random.Random(self.seed * 7919 + self.turn)
            self.prompt += (self.reply + f"\n\nTurn {self.turn}:\n"
                            + words(rng, int(self.tail_tok / self.tpw)))
        self.turn += 1
        return self.prompt


def post(base, path, body, timeout=600):
    r = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def get(base, path, timeout=10):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return json.loads(r.read())


def count_tokens(base, model, text):
    return post(base, "/tokenize", {"model": model, "prompt": text})["count"]


LINE = re.compile(r"^(vllm:\w+?)(?:\{(.*)\})? (\S+)$")


# TTFT breakdown: queue (includes waiting on offload lookups), prefill, and the offload lookup stall.
TIMERS = ("vllm:request_queue_time_seconds", "vllm:request_prefill_time_seconds",
          "vllm:kv_offload_lookup_async_delay_seconds")


def counters(port):
    """Sum the counters we diff; prompt_tokens_by_source split by label, TIMERS as _sum/_count."""
    out = {"preempt": 0.0, "success": 0.0, "local_cache_hit": 0.0,
           "local_compute": 0.0, "external_kv_transfer": 0.0}
    out.update({f"{t}_{s}": 0.0 for t in TIMERS for s in ("sum", "count")})
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=8) as r:
        for ln in r.read().decode().splitlines():
            m = LINE.match(ln)
            if not m:
                continue
            name, labels, v = m.group(1), m.group(2) or "", float(m.group(3))
            if name == "vllm:num_preemptions_total":
                out["preempt"] += v
            elif name == "vllm:request_success_total":
                out["success"] += v
            elif name == "vllm:prompt_tokens_by_source_total":
                src = re.search(r'source="([^"]*)"', labels)
                if src and src.group(1) in out:
                    out[src.group(1)] += v
            elif name in out:
                out[name] += v
    return out


def stream(base, model, prompt, gen, timeout):
    """One streamed completion; returns a record with chunk timestamps."""
    body = {"model": model, "prompt": prompt, "max_tokens": gen, "temperature": 0.7,
            "ignore_eos": True, "stream": True, "stream_options": {"include_usage": True}}
    rec = {"t0": time.perf_counter(), "ts": [], "usage": {}, "text": ""}
    try:
        r = urllib.request.Request(base + "/v1/completions", data=json.dumps(body).encode(),
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            for raw in resp:
                if not raw.startswith(b"data: "):
                    continue
                c = raw[6:].strip()
                if c == b"[DONE]":
                    break
                d = json.loads(c)
                if d.get("usage"):
                    rec["usage"] = d["usage"]
                if d.get("choices") and d["choices"][0].get("text"):
                    rec["ts"].append(time.perf_counter())
                    rec["text"] += d["choices"][0]["text"]
    except Exception as e:
        rec["err"] = str(e)[:110]
    rec["done"] = time.perf_counter()
    u = rec["usage"]
    # usage is authoritative: an SSE chunk is not guaranteed to carry exactly one token.
    rec["gen"] = u.get("completion_tokens") or len(rec["ts"])
    rec["prompt"] = u.get("prompt_tokens", 0)
    rec["ttft"] = rec["ts"][0] - rec["t0"] if rec["ts"] else None
    return rec


def itl_gaps(rec):
    """Per-token gaps: chunk gaps scaled by tokens-per-chunk (usage count / chunks)."""
    ts, n = rec["ts"], rec["gen"]
    if len(ts) < 2 or n < 2:
        return []
    scale = (len(ts) - 1) / (n - 1)
    return [(b - a) * scale for a, b in zip(ts, ts[1:])]


class Poller(threading.Thread):
    """2 s poll of running/waiting (+ VRAM_CMD free VRAM when --vram)."""

    def __init__(self, port, vram_cmd):
        super().__init__(daemon=True)
        self.port, self.vram_cmd = port, vram_cmd
        self.rows, self.stop = [], threading.Event()

    def run(self):
        n = 0
        while not self.stop.is_set():
            try:
                s = sample(self.port)
                row = [time.perf_counter(), s.running, s.waiting, None]
                n += 1
                # Every 5th poll: VRAM_CMD is typically a kubectl exec, which takes seconds.
                if self.vram_cmd and n % 5 == 0:
                    try:
                        o = subprocess.run(self.vram_cmd, shell=True, capture_output=True,
                                           text=True, timeout=20).stdout
                        row[3] = float(re.search(r"-?\d+(?:\.\d+)?", o).group())
                    except Exception:
                        pass
                self.rows.append(row)
            except Exception:
                pass
            self.stop.wait(2)


def plan_levels(args):
    prefixes = [kint(x) for x in args.prefixes.split(",")]
    return [(c, [prefixes[i % len(prefixes)] for i in range(c)])
            for c in (int(x) for x in args.levels.split(","))]


def dry_run(args):
    plan = plan_levels(args)
    reqs = ptoks = gtoks = 0
    wall = 0.0
    print(f"words/token assumed {1/args.tpw:.3f} (tokens/word {args.tpw}); "
          f"estimates use pp={args.est_pp:.0f} tok/s, per-stream tg={args.est_tg:.0f} tok/s")
    for c, pre in plan:
        lp = 0
        lw = sum(pre) / args.est_pp                       # serial cold warmups
        for p in pre:
            lp += p
            for t in range(1, args.turns + 1):
                lp += p + (args.tail + args.gen) * t      # full prompt sent each turn (tails + replies)
        lg = c * args.turns * args.gen
        # steady: per turn, c tails prefill serially then decode; sessions in lockstep
        lw += args.turns * (c * args.tail / args.est_pp + args.gen / args.est_tg)
        n = c * (1 + args.turns)
        print(f"level {c}: prefixes(tok)={pre} requests={n} prompt_tok_sent={lp} "
              f"gen_tok={lg} est_wall={lw:.0f}s")
        reqs += n; ptoks += lp; gtoks += lg; wall += lw
    print(f"TOTAL requests={reqs} prompt_tok_sent={ptoks} (mostly cache hits) gen_tok={gtoks} "
          f"est_wall={wall/60:.1f} min (+ idle checks)")


def run_level(args, base, level_idx, c, prefixes, poller, stop):
    """level_idx only seeds the sessions: pass a distinct value per pass so no prefix is reused."""
    seed0 = args.seed * 1_000_003 + level_idx * 1009
    sessions = [Session(seed0 + i, p, args.tail, args.tpw) for i, p in enumerate(prefixes)]
    row0 = len(poller.rows)
    cold, live, errors = [], [], 0
    for s in sessions:                                    # serial warmups: clean cold prefill
        if stop.is_set():
            break
        prompt = s.next_prompt()
        exact = count_tokens(base, args.model, prompt)
        rec = stream(base, args.model, prompt, 1, args.timeout)
        if rec.get("err"):
            errors += 1
            continue
        live.append(s)
        cold.append({"seed": s.seed, "tokenize": exact, "prompt": rec["prompt"],
                     "ttft": r2(rec["ttft"]),
                     "prefill_tps": r2(rec["prompt"] / rec["ttft"]) if rec["ttft"] else None})
        ttft = f"{rec['ttft']:.1f}s" if rec["ttft"] is not None else "n/a (no text chunk)"
        print(f"  cold s{s.seed}: {exact} tok (tokenize) ttft={ttft}", flush=True)
    recs, lk = [], threading.Lock()

    def worker(s):
        for _ in range(args.turns):
            if stop.is_set():
                return
            rec = stream(base, args.model, s.next_prompt(), args.gen, args.timeout)
            s.reply = rec["text"]
            with lk:
                recs.append(rec)
            if rec.get("err"):
                return

    before = counters(args.port)
    t_start = time.perf_counter()
    ths = [threading.Thread(target=worker, args=(s,), daemon=True) for s in live]
    for t in ths:
        t.start()
    while any(t.is_alive() for t in ths):                 # polling join keeps Ctrl-C responsive
        for t in ths:
            t.join(0.5)
    t_end = time.perf_counter()
    after = counters(args.port)

    ok = [r for r in recs if not r.get("err") and r["gen"] > 1 and r["ts"]]
    errors += sum(1 for r in recs if r.get("err"))
    window = (max(r["done"] for r in ok) - t_start) if ok else 0
    gaps = [g for r in ok for g in itl_gaps(r)]
    per_stream = [(r["gen"] - 1) / (r["ts"][-1] - r["ts"][0]) for r in ok
                  if r["ts"][-1] > r["ts"][0]]
    ttfts = [r["ttft"] for r in ok]
    # Contamination counts the whole level (warmups too); waiting/VRAM only the steady window.
    rows = poller.rows[row0:]
    steady = [x for x in rows if t_start <= x[0] <= t_end]
    vram = [x[3] for x in steady if x[3] is not None]
    d = {k: after[k] - before[k] for k in after}
    hit, comp = d["local_cache_hit"], d["local_compute"]
    return {
        "conc": c, "prefix_tok": prefixes, "turns": args.turns,
        "cold": cold,
        "requests_ok": len(ok), "errors": errors,
        "agg_tps": r2(sum(r["gen"] for r in ok) / window) if window else None,
        "stream_tps_p50": r2(pct(per_stream, 50)),
        "ttft_p50": r2(pct(ttfts, 50)), "ttft_p90": r2(pct(ttfts, 90)),
        "itl_ms_p50": r2(pct(gaps, 50) * 1000) if gaps else None,
        "itl_ms_p90": r2(pct(gaps, 90) * 1000) if gaps else None,
        "server_hit_frac": r2(hit / (hit + comp)) if hit + comp else None,
        "d_preemptions": d["preempt"], "d_request_success": d["success"],
        "d_local_cache_hit": hit, "d_local_compute": comp,
        "d_external_kv_transfer": d["external_kv_transfer"],
        # Mean seconds per event over the steady window; count shows how many requests hit it.
        **{f"{t.split(':')[1].replace('_seconds', '')}_mean_s":
           r2(d[f"{t}_sum"] / d[f"{t}_count"]) if d[f"{t}_count"] else 0.0 for t in TIMERS},
        "lookup_stall_events": d["vllm:kv_offload_lookup_async_delay_seconds_count"],
        "max_waiting": max((x[2] for x in steady), default=None),
        "contaminated": any(x[1] > c for x in rows),
        "min_free_vram": min(vram) if vram else None,
        "window_s": r2(window),
    }


SUMMARY = ("agg_tps", "stream_tps_p50", "ttft_p50", "ttft_p90", "itl_ms_p50", "itl_ms_p90",
           "request_queue_time_mean_s", "request_prefill_time_mean_s",
           "kv_offload_lookup_async_delay_mean_s", "lookup_stall_events", "min_free_vram")


def print_summary(results):
    """Median per metric over the runs of each level; spread = max-min = the noise floor."""
    print("\nSUMMARY median [spread] over runs; errors/preemptions summed")
    for c in sorted({r["conc"] for r in results}):
        rs = [r for r in results if r["conc"] == c]
        out = {"conc": c, "runs": len(rs), "errors": sum(r["errors"] for r in rs),
               "d_preemptions": sum(r["d_preemptions"] for r in rs),
               "contaminated": any(r["contaminated"] for r in rs)}
        for k in SUMMARY:
            v = [r[k] for r in rs if r.get(k) is not None]
            out[k] = {"median": r2(statistics.median(v)), "spread": r2(max(v) - min(v))} if v else None
        print(json.dumps(out), flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=18000)
    ap.add_argument("--model", default="qwen-3.8")
    ap.add_argument("--levels", default="1,2,4,5,8", help="concurrent sessions per level")
    ap.add_argument("--prefixes", default="40k,16k,64k,40k,98k",
                    help="session prefix tokens, cycled; default mean ~52K, p50 40K")
    ap.add_argument("--turns", type=int, default=3, help="steady turns after the warmup turn")
    ap.add_argument("--tail", type=int, default=1254, help="new tokens per turn (prod p50 computed)")
    ap.add_argument("--gen", type=int, default=235, help="generated tokens per turn (prod p50)")
    ap.add_argument("--seed", type=int, default=None,
                    help="default random: a reused seed finds its prefix already cached")
    ap.add_argument("--tpw", type=float, default=None, help="tokens/word; default calibrated via /tokenize")
    ap.add_argument("--timeout", type=int, default=1800)
    ap.add_argument("--warmup", action="store_true", help="one untimed pass per level before the runs")
    ap.add_argument("--runs", type=int, default=2, help="timed passes per level; median + spread reported")
    ap.add_argument("--allow-busy", action="store_true")
    ap.add_argument("--vram", action="store_true", help="record min free VRAM via $VRAM_CMD")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--est-pp", type=float, default=3500, help="dry-run only: prefill tok/s")
    ap.add_argument("--est-tg", type=float, default=25, help="dry-run only: per-stream decode tok/s")
    args = ap.parse_args()
    if args.seed is None:
        args.seed = random.SystemRandom().randrange(1, 10**6)
    if args.dry_run:
        args.tpw = args.tpw or DEFAULT_TPW
        return dry_run(args)

    base = f"http://127.0.0.1:{args.port}"
    vram_cmd = os.environ.get("VRAM_CMD") if args.vram else None
    if args.vram and not vram_cmd:
        print("note: --vram set but VRAM_CMD is unset; min_free_vram will be null", flush=True)

    if not args.allow_busy and not wait_idle(args.port).idle:
        sys.exit("engine never idle for two consecutive scrapes in 5 min; refusing (use --allow-busy)")

    if args.tpw is None:                                  # calibrate words->tokens on this tokenizer
        n = 3000
        args.tpw = count_tokens(base, args.model, words(random.Random(1), n)) / n
        print(f"calibrated tokens/word = {args.tpw:.3f}", flush=True)

    poller = Poller(args.port, vram_cmd)
    poller.start()
    stop, results = threading.Event(), []
    try:
        for li, (c, pre) in enumerate(plan_levels(args)):
            # Distinct seed per pass (level*100 + pass) so no pass finds a prefix cached.
            if args.warmup:
                print(f"level {c}: untimed warmup pass...", flush=True)
                run_level(args, base, li * 100, c, pre, poller, stop)
            for ri in range(args.runs):
                if stop.is_set():
                    break
                print(f"level {c} run {ri + 1}/{args.runs}: warming {c} session(s) serially...", flush=True)
                res = run_level(args, base, li * 100 + 1 + ri, c, pre, poller, stop)
                res["run"] = ri + 1
                results.append(res)
                print(json.dumps(res), flush=True)
            if stop.is_set():
                break
    except KeyboardInterrupt:
        stop.set()
        print("\ninterrupted; partial results follow", flush=True)
    poller.stop.set()

    cols = [("conc", "conc"), ("agg_tps", "agg t/s"), ("stream_tps_p50", "strm t/s"),
            ("ttft_p50", "ttft50"), ("ttft_p90", "ttft90"), ("itl_ms_p50", "itl50ms"),
            ("itl_ms_p90", "itl90ms"), ("requests_ok", "ok"), ("errors", "err"),
            ("server_hit_frac", "hit%"), ("d_preemptions", "preempt"),
            ("max_waiting", "maxwait"), ("contaminated", "contam")]
    print("\nPER RUN")
    print(" ".join(f"{h:>9}" for _, h in cols))
    for r in results:
        print(" ".join(f"{str(r.get(k)):>9}" for k, _ in cols))
    print_summary(results)
    for r in results:
        cs = [x["prefill_tps"] for x in r["cold"] if x["prefill_tps"]]
        print(f"cold conc {r['conc']}: ttft={[x['ttft'] for x in r['cold']]} "
              f"prefill tok/s p50={r2(pct(cs, 50))}")
    try:
        ver, models = get(base, "/version"), get(base, "/v1/models")
    except Exception as e:
        ver = models = str(e)
    print(json.dumps({"args": vars(args), "version": ver,
                      "models": [m.get("id") for m in models["data"]] if isinstance(models, dict) else models,
                      "levels": len(results)}))


if __name__ == "__main__":
    main()
