"""Needle retrieval: a random code hidden at depth 10/50/90% of a ~N-word haystack (1.17 tokens/word)."""
import argparse
import random
import sys
import time
import urllib.error

from prodshape import WORDS, post

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("port", type=int)
parser.add_argument("words", type=int)
parser.add_argument("--model", default="qwen-3.8")
parser.add_argument("--chat-needles", action="store_true")
args = parser.parse_args()
base = f"http://127.0.0.1:{args.port}"
ok = 0
for depth in (0.1, 0.5, 0.9):
    rnd = random.Random(time.time_ns())
    code = "".join(rnd.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    body = [rnd.choice(WORDS) for _ in range(args.words)]
    body.insert(int(args.words * depth), f"\nThe secret access code is {code}.\n")
    prompt = f"Session {time.time_ns()}.\n" + " ".join(body) + "\n\nWhat is the secret access code? Answer with the code only."
    tok = post(base, "/tokenize", {"model": args.model, "prompt": prompt}, 120)
    print(f"depth {depth:.0%} /tokenize count {tok['count']} chars {len(prompt)}", flush=True)
    t = time.time()
    try:
        if args.chat_needles:
            r = post(base, "/v1/chat/completions", {"model": args.model, "max_tokens": 1024, "temperature": 0,
                                                    "messages": [{"role": "user", "content": prompt}]}, 900)
            out = r["choices"][0]["message"]["content"] or ""
        else:
            r = post(base, "/v1/completions", {"model": args.model, "prompt": prompt,
                                               "max_tokens": 24, "temperature": 0}, 900)
            out = r["choices"][0]["text"]
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code}: {e.read().decode()[:600]}")
    out = out.strip()
    hit = code in out
    ok += hit
    print(f"depth {depth:.0%} prompt {r['usage']['prompt_tokens']} {time.time() - t:.0f}s want {code} got {out!r} {'PASS' if hit else 'FAIL'}", flush=True)
print(f"needle {ok}/3")
