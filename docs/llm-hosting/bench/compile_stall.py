#!/usr/bin/env python3
"""Resume TTFT at never-seen lengths: cache one ~32K prefix, then send --n resumes with random tails.

Each resume is a new (max_seqlen_q, max_seqlen_k) pair for the continuation flash-attn, so a kernel that
JIT-compiles per length shows up as multi-second TTFTs. usage: compile_stall.py [--port 18000] [--n 6]
"""
import argparse
import random
import time

from prodshape import post, words

ap = argparse.ArgumentParser()
ap.add_argument("--port", type=int, default=18000)
ap.add_argument("--n", type=int, default=6)
args = ap.parse_args()
base = f"http://127.0.0.1:{args.port}"
rng = random.Random()


def ask(prompt):
    t = time.time()
    r = post(base, "/v1/completions", {"model": "qwen-3.8", "prompt": prompt, "max_tokens": 1, "temperature": 0}, 900)
    return r["usage"]["prompt_tokens"], time.time() - t


prefix = f"Stall {time.time_ns()}\n" + words(rng, 25000)
print("prefix (cold) tokens, s:", ask(prefix), flush=True)
ts = []
for i in range(args.n):
    tokens, dt = ask(prefix + f"\nTurn {i}.\n" + words(rng, rng.randrange(200, 1500)))
    ts.append(dt)
    print(f"resume {i}: {tokens} tokens, {dt:.2f} s", flush=True)
ts.sort()
print(f"resume TTFT p50 {ts[len(ts) // 2]:.2f} s, max {ts[-1]:.2f} s", flush=True)
