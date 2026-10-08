#!/usr/bin/env python3
"""Quality gate for every roll (docs/plans/r9700-gpu-max.md, Acceptance table).

usage: qualitygate.py MODE [--name STACK] [--compare STACK] [--port 18000]
MODE: nll agree gsm8k tools needle vision all

  --name STACK     write this mode's records to quality/baseline-STACK.jsonl
                   (other modes already in the file are kept)
  --compare STACK  nll / agree: compare against quality/baseline-STACK.jsonl

Greedy everywhere. Every request carries a per-invocation cache_salt (cold) except
the deliberate warm passes (agree long prompts, needle cached-prefix, offload reload),
which reuse the salt of their cold twin. Engine must be otherwise idle: batch
composition changes numerics, so agree/nll run strictly sequentially.

nll uses prompt_logprobs (teacher forced). vLLM materialises logits for each scheduled
prefill chunk, so it can allocate GPU memory beyond what boot profiled: watch free VRAM
the first time (bench/vramfree.sh).
"""
import argparse, json, os, random, re, statistics, struct, sys, time, urllib.request, zlib
import base64
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from prodshape import counters, words, DEFAULT_TPW  # noqa: E402

QDIR = os.path.join(HERE, "quality")
GSM_URL = "https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/test.jsonl"
GSM_CACHE = "/tmp/gsm8k_test.jsonl"
SALT = f"qg-{random.SystemRandom().randrange(16**8):08x}"
A = None  # parsed args


def post(path, body, timeout=3600):
    r = urllib.request.Request(A.base + path, data=json.dumps(body).encode(),
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=timeout) as resp:
        return json.loads(resp.read())


def tokenize(text):
    return post("/tokenize", {"model": A.model, "prompt": text}, 600)["tokens"]


def complete(prompt, n, salt=None, **kw):
    return post("/v1/completions", {"model": A.model, "prompt": prompt, "max_tokens": n,
                                    "temperature": 0, "cache_salt": salt or SALT, **kw})


def chat(messages, n, salt=None, **kw):
    return post("/v1/chat/completions", {"model": A.model, "messages": messages, "max_tokens": n,
                                         "temperature": 0, "cache_salt": salt or SALT, **kw})


def gsm_items(n):
    if not os.path.exists(GSM_CACHE):
        urllib.request.urlretrieve(GSM_URL, GSM_CACHE)
    rows = [json.loads(x) for x in open(GSM_CACHE)]
    return [(r["question"], r["answer"].split("####")[-1].strip().replace(",", "")) for r in rows[:n]]


def long_text(seed, tokens):
    """Deterministic filler with numeric entropy so it is not trivially predictable."""
    rng = random.Random(seed)
    out, n = [], int(tokens / DEFAULT_TPW)
    for i in range(0, n, 12):
        out.append(f"Record {rng.randrange(10**6)}: " + words(rng, 10) + f" value={rng.randrange(10**4)}.")
    return "\n".join(out)


# ---- snapshot io ----
def snap_path(name):
    return os.path.join(QDIR, f"baseline-{name}.jsonl")


def load_snap(name, mode):
    return [r for r in map(json.loads, open(snap_path(name))) if r["mode"] == mode]


def save_snap(name, mode, recs):
    os.makedirs(QDIR, exist_ok=True)
    keep = []
    if os.path.exists(snap_path(name)):
        keep = [r for r in map(json.loads, open(snap_path(name))) if r["mode"] != mode]
    with open(snap_path(name), "w") as f:
        for r in keep + [{"mode": mode, **x} for x in recs]:
            f.write(json.dumps(r) + "\n")
    print(f"wrote {len(recs)} {mode} records to {snap_path(name)}")


def finish(mode, recs):
    if A.name:
        save_snap(A.name, mode, recs)


# ---- nll ----
def mode_nll():
    recs = []
    for i in range(20):
        target = 8192 + i * 1294                                   # 8K .. ~32.7K
        ids = tokenize(long_text(1000 + i, int(target * 1.15)))[:target]
        r = complete(ids, 1, prompt_logprobs=0)
        pl = r["choices"][0]["prompt_logprobs"]
        lp = []
        for tid, e in zip(ids[1:], pl[1:]):
            lp.append((e.get(str(tid)) or next(iter(e.values())))["logprob"])
        nll = -sum(lp) / len(lp)
        recs.append({"id": i, "tokens": len(ids), "nll": round(nll, 6)})
        print(f"nll text {i} ({len(ids)} tok): {nll:.4f}", flush=True)
    mean = statistics.mean(x["nll"] for x in recs)
    print(f"NLL mean {mean:.5f} over {len(recs)} texts")
    if A.compare:
        base = {x["id"]: x["nll"] for x in load_snap(A.compare, "nll")}
        d = statistics.mean(x["nll"] - base[x["id"]] for x in recs)
        print(f"NLL mean delta vs {A.compare}: {d:+.5f} nats/token -> {'PASS' if d <= 0.01 else 'FAIL'} (gate <= 0.01)")
    finish("nll", recs)


# ---- agree ----
CODE = ["a Python function that merges overlapping intervals", "a Rust function that reverses a linked list",
        "a SQL query returning the second highest salary per department",
        "a bash script that counts lines in every .py file recursively",
        "a Python LRU cache class without functools", "a Go function that checks if a string is a palindrome",
        "a Python function that parses ISO 8601 durations", "a C function that computes the gcd of two ints",
        "a Python generator yielding primes", "a JavaScript debounce function",
        "a Python function that flattens nested lists", "a Rust enum and match for a simple calculator",
        "a Python binary search returning the insertion point", "a regex and Python code that extracts emails",
        "a Python class implementing a min-heap", "a TypeScript function that deep-clones plain objects",
        "a Python function that validates balanced brackets", "a Go goroutine worker pool of 4 workers",
        "a Python function computing Levenshtein distance", "a Python topological sort of a DAG dict"]


def agree_prompts():
    ps = [("gsm8k-%d" % i, f"Question: {q}\nAnswer: Let's think step by step.", False)
          for i, (q, _) in enumerate(gsm_items(20))]
    ps += [("code-%d" % i, f"Write {c}. Include a docstring and a short example.\n\n```", False)
           for i, c in enumerate(CODE)]
    for i in range(20):
        t = 8192 if i < 10 else 32768
        ps.append((f"long{t // 1024}k-{i}", long_text(5000 + i, t)
                   + "\n\nList the three largest 'value=' numbers above and explain how you found them.\n", True))
    return ps


def gen(prompt, warm_salt=None):
    r = complete(prompt, 128, salt=warm_salt, logprobs=1)
    lp = r["choices"][0]["logprobs"]
    return {"tokens": lp["tokens"], "lp": [round(x, 4) if x is not None else None for x in lp["token_logprobs"]]}


def first_div(a, b):
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n if len(a) == len(b) else n


def agreement(cur, base, label):
    tot = same = pos_same = early = 0
    worst = []
    for cid, c in cur.items():
        b = base[cid]
        d = first_div(c["tokens"], b["tokens"])
        n = max(len(c["tokens"]), len(b["tokens"]))
        tot += n
        same += d
        pos_same += sum(x == y for x, y in zip(c["tokens"], b["tokens"]))
        if d < n:
            worst.append((d, cid))
        early += d < 16 and d < n
    worst.sort()
    print(f"{label}: prefix-agreement {100 * same / tot:.2f}%  positionwise {100 * pos_same / tot:.2f}%  "
          f"diverged {len(worst)}/{len(cur)}  before-token-16 {early}")
    print(f"  first divergences (token index, prompt): {worst[:8]}")
    return 100 * same / tot, early


def mode_agree():
    cur, prompts = {}, agree_prompts()
    for cid, p, _ in prompts:
        cur[cid] = gen(p)
        print(f"agree {cid} done", flush=True)
    recs = [{"id": k, **v} for k, v in cur.items()]
    if A.compare:
        base = {r["id"]: r for r in load_snap(A.compare, "agree")}
        pct, early = agreement(cur, base, f"vs {A.compare}")
        print(f"AGREE {pct:.2f}% -> {'PASS' if pct >= 98 and not early else 'FAIL'} (gate >= 98, none before token 16; "
              f"unchanged-stack determinism wants >= 99.9)")
    # deliberate warm pass: same salt + prompt as the cold twin -> served from the prefix cache
    warm = {cid: gen(p) for cid, p, is_long in prompts if is_long}
    agreement(warm, {k: cur[k] for k in warm}, "warm (cached prefix) vs cold, same run")
    finish("agree", recs + [{"id": f"warm-{k}", **v} for k, v in warm.items()])


# ---- gsm8k ----
def last_number(s):
    m = re.findall(r"-?\d[\d,]*\.?\d*", s)
    return m[-1].replace(",", "").rstrip(".") if m else None


def mode_gsm8k():
    items = gsm_items(150)
    sys_p = "Solve the math problem step by step. End with a final line 'Answer: <number>'."

    def one(i):
        q, want = items[i]
        r = chat([{"role": "system", "content": sys_p}, {"role": "user", "content": q}], 4096)
        txt = r["choices"][0]["message"].get("content") or ""
        m = re.findall(r"Answer:\s*\$?(-?[\d,]*\.?\d+)", txt)
        got = (m[-1].replace(",", "") if m else last_number(txt))
        try:
            ok = got is not None and float(got) == float(want)
        except ValueError:
            ok = False
        return i, ok

    with ThreadPoolExecutor(A.conc) as ex:
        res = sorted(ex.map(one, range(len(items))))
    ok = sum(o for _, o in res)
    verdict = "PASS" if ok >= 143 else "RERUN on full 1319" if ok >= 138 else "FAIL"
    print(f"GSM8K {ok}/{len(items)} -> {verdict} (gate >= 143; 138-142 rerun full; < 138 fail)")
    finish("gsm8k", [{"pass": ok, "n": len(items), "failed": [i for i, o in res if not o]}])


# ---- tools ----
TOOLS = [{"type": "function", "function": {"name": n, "description": d, "parameters": {
    "type": "object", "properties": p, "required": list(p)}}} for n, d, p in [
    ("get_weather", "Get the current weather for a city.",
     {"city": {"type": "string"}, "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]}}),
    ("add", "Add two integers.", {"a": {"type": "integer"}, "b": {"type": "integer"}}),
    ("search_docs", "Search the documentation.", {"query": {"type": "string"}, "limit": {"type": "integer"}}),
    ("create_ticket", "Create a support ticket.",
     {"title": {"type": "string"}, "priority": {"type": "string", "enum": ["low", "medium", "high"]}}),
]]
TOOL_CASES = [
    ("What is the weather in Paris in celsius?", "get_weather", {"city": "paris", "unit": "celsius"}),
    ("Weather in New York, fahrenheit please.", "get_weather", {"city": "new york", "unit": "fahrenheit"}),
    ("Compute 17 plus 25.", "add", {"a": 17, "b": 25}),
    ("Add 1000 and 2345.", "add", {"a": 1000, "b": 2345}),
    ("Search the docs for 'ceph osd flapping', 5 results.", "search_docs", {"query": "ceph osd flapping", "limit": 5}),
    ("Look up 'cilium bgp' in the documentation, limit 3.", "search_docs", {"query": "cilium bgp", "limit": 3}),
    ("Open a high priority ticket titled 'Disk full on node 2'.", "create_ticket",
     {"title": "disk full on node 2", "priority": "high"}),
    ("File a low priority ticket: 'Typo in README'.", "create_ticket", {"title": "typo in readme", "priority": "low"}),
    ("Is it hot in Madrid? Use celsius.", "get_weather", {"city": "madrid", "unit": "celsius"}),
    ("What is 7 + 8?", "add", {"a": 7, "b": 8}),
    ("Search docs for 'vllm prefix cache' with limit 10.", "search_docs", {"query": "vllm prefix cache", "limit": 10}),
    ("Create a medium priority ticket named 'Slow dashboards'.", "create_ticket",
     {"title": "slow dashboards", "priority": "medium"}),
]


def tool_case(c):
    q, name, want = c
    r = chat([{"role": "user", "content": q}], 2048, tools=TOOLS, tool_choice="auto")
    calls = r["choices"][0]["message"].get("tool_calls") or []
    if not calls or calls[0]["function"]["name"] != name:
        return False
    try:
        args = json.loads(calls[0]["function"]["arguments"])
    except ValueError:
        return False
    norm = lambda v: v.strip().lower().rstrip(".") if isinstance(v, str) else v  # noqa: E731
    return all(norm(args.get(k)) == norm(v) for k, v in want.items())


def mode_tools():
    out = []
    for label, conc in (("single", 1), ("5 concurrent", 5)):
        with ThreadPoolExecutor(conc) as ex:
            res = list(ex.map(tool_case, TOOL_CASES))
        print(f"TOOLS {label}: {sum(res)}/{len(res)} -> {'PASS' if all(res) else 'FAIL'} "
              f"(failed idx {[i for i, o in enumerate(res) if not o]})")
        out.append({"variant": label, "pass": sum(res), "n": len(res)})
    finish("tools", out)


# ---- needle ----
def needle_prompt(rng, nwords):
    code = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
    body = words(rng, nwords).split(" ")
    body.insert(int(len(body) * 0.5), f"\nThe secret access code is {code}.\n")
    return code, f"Session {rng.random()}.\n" + " ".join(body) + \
        "\n\nWhat is the secret access code? Answer with the code only."


def ask(prompt, salt):
    r = complete(prompt, 24, salt=salt)
    return r["choices"][0]["text"], r["usage"]


def mode_needle():
    out = []
    for w in (15000, 45000, 150000):                               # 17.5K / 52.5K / 175K tokens
        p = os.popen(f"{sys.executable} {HERE}/needle.py {A.port} {w}").read()
        print(p.strip().splitlines()[-1] if p.strip() else "needle.py no output")
        ok = "needle 3/3" in p
        out.append({"check": f"needle-{w}w", "pass": ok})
    rng = random.Random(time.time_ns())
    code, prompt = needle_prompt(rng, 15000)
    salt = f"{SALT}-warm"
    ask(prompt, salt)
    before = counters(A.port)  # the server returns no usage.prompt_tokens_details, so diff the hit counter
    txt, _ = ask(prompt, salt)
    hit = counters(A.port)["local_cache_hit"] - before["local_cache_hit"]
    ok = code in txt and hit > 0
    print(f"needle cached-prefix: found={code in txt} d(local_cache_hit)={hit:.0f} -> {'PASS' if ok else 'FAIL'}")
    out.append({"check": "cached-prefix", "pass": ok, "d_local_cache_hit": hit})
    # offload reload: evict from GPU with >= 450K distinct tokens, resend, expect external_kv_transfer
    code, prompt = needle_prompt(rng, 15000)
    salt = f"{SALT}-reload"
    ask(prompt, salt)
    sent, i = 0, 0
    while sent < 450_000:
        i += 1
        junk = f"Evict {SALT} {i}.\n" + long_text(rng.randrange(10**9), 46000)
        sent += complete(junk, 1, salt=f"{SALT}-evict-{i}")["usage"]["prompt_tokens"]
        print(f"  evicting: {sent} tokens sent", flush=True)
    before = counters(A.port)
    txt, u = ask(prompt, salt)
    d = counters(A.port)["external_kv_transfer"] - before["external_kv_transfer"]
    ok = code in txt and d > 0
    print(f"needle offload-reload: found={code in txt} d(external_kv_transfer)={d:.0f} -> {'PASS' if ok else 'FAIL'}")
    out.append({"check": "offload-reload", "pass": ok, "d_external_kv_transfer": d})
    finish("needle", out)


# ---- vision ----
def png(w, h, pix):
    raw = b"".join(b"\x00" + b"".join(bytes(pix(x, y)) for x in range(w)) for y in range(h))
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def mode_vision():
    cases = [
        (png(128, 128, lambda x, y: (220, 20, 20) if x < 64 else (20, 20, 220)),
         "This image is split into a left and a right half. Name the two colors.", ("red", "blue")),
        (png(128, 128, lambda x, y: (20, 200, 20) if 32 <= x < 96 and 32 <= y < 96 else (255, 255, 255)),
         "What color is the square in the middle of this image?", ("green",)),
    ]
    out = []
    for i, (img, q, want) in enumerate(cases):
        uri = "data:image/png;base64," + base64.b64encode(img).decode()
        r = chat([{"role": "user", "content": [{"type": "image_url", "image_url": {"url": uri}},
                                               {"type": "text", "text": q}]}], 2048)
        txt = (r["choices"][0]["message"].get("content") or "").lower()
        ok = all(w in txt for w in want)
        print(f"vision {i}: {txt[:120]!r} -> {'PASS' if ok else 'FAIL'}")
        out.append({"case": i, "pass": ok})
    finish("vision", out)


MODES = {"nll": mode_nll, "agree": mode_agree, "gsm8k": mode_gsm8k, "tools": mode_tools,
         "needle": mode_needle, "vision": mode_vision}


def main():
    global A
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=[*MODES, "all"])
    ap.add_argument("--name")
    ap.add_argument("--compare")
    ap.add_argument("--port", type=int, default=18000)
    ap.add_argument("--model", default="qwen-3.8")
    ap.add_argument("--conc", type=int, default=4, help="gsm8k threads")
    A = ap.parse_args()
    A.base = f"http://127.0.0.1:{A.port}"
    print(f"cache_salt base: {SALT}")
    # all: cheap/short first, evicting needle last
    for m in (["vision", "tools", "gsm8k", "agree", "nll", "needle"] if A.mode == "all" else [A.mode]):
        print(f"\n=== {m} ===", flush=True)
        MODES[m]()


if __name__ == "__main__":
    main()
