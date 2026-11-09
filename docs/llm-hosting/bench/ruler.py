#!/usr/bin/env python3
"""RULER long-context accuracy (github.com/NVIDIA/RULER): RULER generates the samples and owns the metric, this sends
them to the engine with thinking off and prints a task x length table. Resumable: a rerun skips finished samples.

usage: $RULER_HOME/venv/bin/python ruler.py RUN [--port 18000] [--lengths 4096,...,131072] [--samples 10] [--tasks a,b]

Sequential on purpose: prefill is compute-bound, so a second stream adds load on production, not throughput.
Port-forward, not in the pod as prodshape: exec into the production pod is denied. The port-forward stalls after
30-60 min; restart it on a timer and the retries absorb the cut:
  while :; do timeout 1800 kubectl -n ai port-forward svc/qwen38-27b-vllm 18000:8000; sleep 1; done

Setup, once (RULER_HOME defaults to ~/.local/state/radiance-tune/ruler):
  git clone https://github.com/NVIDIA/RULER $RULER_HOME/RULER && git -C $RULER_HOME/RULER checkout db43f92
    # GIT_LFS_SKIP_SMUDGE=1 without git-lfs; the baseline's generators and metric are db43f92's
  uv venv --python 3.13 $RULER_HOME/venv && VIRTUAL_ENV=$RULER_HOME/venv uv pip install transformers nltk \
    wonderwords scipy numpy pyyaml tqdm tenacity html2text beautifulsoup4
  hf tokenizer.json, tokenizer_config.json and config.json of the served checkpoint -> $RULER_HOME/tokenizer
  in RULER/scripts/data/synthetic/json: english_words.json (LFS: media.githubusercontent.com), squad.json and
    hotpotqa.json (download_qa_dataset.sh), download_paulgraham_essay.py; nltk punkt and punkt_tab
Generated samples are seeded, but the essays come from live websites: keep $RULER_HOME/data to compare runs.
"""
import argparse, hashlib, http.client, importlib.util, json, os, subprocess, sys, time, urllib.request

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _metrics import sample  # noqa: E402
from prodshape import get, post  # noqa: E402

HOME = os.environ.get("RULER_HOME", os.path.expanduser("~/.local/state/radiance-tune/ruler"))
SCRIPTS = os.path.join(HOME, "RULER", "scripts")
# The sharp chat template rendered with enable_thinking false; RULER's answer prefix opens the reply.
WRAP = "<|im_start|>user\n{}<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n{}"


def load(path):
    spec = importlib.util.spec_from_file_location(path, os.path.join(SCRIPTS, path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.TASKS


def rows(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(ln) for ln in f]


def retry(fn):
    """fn() through port-forward restarts: refused, reset, timeout, a body cut short, 5xx, or 429 (radiance's admission
    queue is full). Any other HTTP error is the request's fault and exits."""
    for attempt in range(30):
        try:
            return fn()
        except (OSError, http.client.HTTPException, ValueError) as e:  # HTTPError is an OSError
            if getattr(e, "code", 500) < 500 and e.code != 429:
                sys.exit(f"HTTP {e.code}: {e.read().decode()[:600]}")
            print(f"  retry {attempt + 1}: {e}", flush=True)
        time.sleep(10)
    sys.exit("30 failed attempts in a row")


def scrape(port):
    """KV pool tokens and the preemption counter, from one /metrics read."""
    out = {"radiance:kv_cache_max_tokens": 0.0, "vllm:num_preemptions_total": 0.0}
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=10) as r:
        for ln in r.read().decode().splitlines():
            name = ln.split("{")[0].split(" ")[0]
            if name in out:
                out[name] += float(ln.rsplit(" ", 1)[1])
    return out["radiance:kv_cache_max_tokens"], out["vllm:num_preemptions_total"]


def quiet(port):
    """Waits until the engine is idle on two scrapes 30 s apart."""
    while True:
        if retry(lambda: sample(port)).idle:
            time.sleep(30)
            if retry(lambda: sample(port)).idle:
                return
        time.sleep(30)


def preflight(base):
    """Exits unless WRAP is token-identical to the served no-thinking render (else new WRAP, new baseline)."""
    served = {"model": "qwen-3.8", "messages": [{"role": "user", "content": "Reply OK."}],
              "add_generation_prompt": True, "chat_template_kwargs": {"enable_thinking": False}}
    wrapped = {"model": "qwen-3.8", "prompt": WRAP.format("Reply OK.", "")}
    if retry(lambda: post(base, "/tokenize", served, 120))["tokens"] != retry(
            lambda: post(base, "/tokenize", wrapped, 120))["tokens"]:
        sys.exit("WRAP no longer matches the served chat template's no-thinking render")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("run")
    p.add_argument("--port", type=int, default=18000)
    p.add_argument("--lengths", default="4096,8192,16384,32768,65536,131072")
    p.add_argument("--samples", type=int, default=10)
    p.add_argument("--tasks", default="")
    a = p.parse_args()
    base = f"http://127.0.0.1:{a.port}"
    with open(os.path.join(SCRIPTS, "synthetic.yaml")) as f:
        cfg = yaml.safe_load(f)
    tasks = a.tasks.split(",") if a.tasks else list(cfg)
    gen, metric = load("data/synthetic/constants.py"), load("eval/synthetic/constants.py")
    lengths = [int(x) for x in a.lengths.split(",")]
    cells = [(n, t) for n in lengths for t in tasks]
    data = os.path.join(HOME, "data", str(a.samples))
    # prepare.py shells out to a bare `python`.
    env = dict(os.environ, PATH=os.path.dirname(sys.executable) + os.pathsep + os.environ["PATH"])
    for n, t in cells:
        # Skips a complete file; prints its generator's failure and still exits 0.
        log = subprocess.run([sys.executable, os.path.join(SCRIPTS, "data", "prepare.py"), "--save_dir",
                              os.path.join(data, str(n)), "--benchmark", "synthetic", "--task", t, "--tokenizer_type",
                              "hf", "--tokenizer_path", os.path.join(HOME, "tokenizer"), "--max_seq_length", str(n),
                              "--model_template_type", "base", "--num_samples", str(a.samples)],
                             env=env, capture_output=True, text=True)
        if len(rows(os.path.join(data, str(n), t, "validation.jsonl"))) != a.samples:
            sys.exit(f"prepare {t} {n} failed:\n{log.stdout[-2000:]}{log.stderr[-2000:]}")

    # A run is pinned to its samples and engine. Same order as `find data/N -name validation.jsonl | sort | xargs cat |
    # sha256sum`, the runbook's fingerprints.
    run = os.path.join(HOME, "runs", a.run)
    os.makedirs(run, exist_ok=True)
    meta_path = os.path.join(run, "meta.json")
    meta = {"cells": {}}
    if os.path.exists(meta_path):
        with open(meta_path) as f:
            meta = json.load(f)

    def save():
        with open(meta_path, "w") as f:
            json.dump(meta, f)

    digest = hashlib.sha256()
    for key in sorted(f"{n}/{t}" for n, t in cells):
        with open(os.path.join(data, key, "validation.jsonl"), "rb") as f:
            raw = f.read()
        digest.update(raw)
        h = hashlib.sha256(raw).hexdigest()[:16]
        if meta["cells"].setdefault(key, h) != h:
            sys.exit(f"{key}: samples differ from the ones {a.run} started with")
    save()
    print(f"{a.run}: data {digest.hexdigest()[:16]}", flush=True)

    checked, scores = False, {}
    for n, t in cells:
        kind = cfg[t]["task"]
        out = os.path.join(run, str(n), f"{t}.jsonl")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        done = {r["index"] for r in rows(out)}
        for s in rows(os.path.join(data, str(n), t, "validation.jsonl")):
            if s["index"] in done:
                continue
            pool, before = retry(lambda: scrape(a.port))
            # 128K, a quarter of the pool, ran beside agents without a preemption; 256K, half, preempted 3 sessions.
            if n * 3 > pool:
                quiet(a.port)
                pool, before = retry(lambda: scrape(a.port))
            # Checked per prompt: a Renovate bump rolls the pod when it idles, which is when prompts start.
            root = retry(lambda: get(base, "/v1/models"))["data"][0]["root"]
            if meta.setdefault("engine", root) != root:
                sys.exit(f"serving {root}, but {a.run} started on {meta['engine']}")
            if not checked:
                preflight(base)
                save()
                checked = True
            t0 = time.time()
            r = retry(lambda: post(base, "/v1/completions", {
                "model": "qwen-3.8", "prompt": WRAP.format(s["input"], s["answer_prefix"]),
                "max_tokens": gen[kind]["tokens_to_generate"], "temperature": 0}, 1800))
            pred, tok, dt = r["choices"][0]["text"], r["usage"]["prompt_tokens"], round(time.time() - t0, 1)
            preempted = retry(lambda: scrape(a.port))[1] - before
            with open(out, "a") as f:
                f.write(json.dumps({"index": s["index"], "pred": pred, "outputs": s["outputs"], "prompt_tokens": tok,
                                    "seconds": dt, "preempted": preempted}) + "\n")
            hit = metric[kind]["metric_fn"]([pred], [s["outputs"]])
            print(f"{n} {t} #{s['index']} {tok} tok {dt:.0f}s {hit:.0f} preempted {preempted:.0f} "
                  f"{pred.strip()[:80]!r}", flush=True)
        # Raw preds: RULER's postprocess (strip, control characters to newlines) cannot change a substring match
        # of its references, which hold neither.
        got = rows(out)
        scores[t, n] = metric[kind]["metric_fn"]([g["pred"] for g in got], [g["outputs"] for g in got])
    print(f"\n{a.run}, {a.samples} samples per cell, data {digest.hexdigest()[:16]}, engine {meta.get('engine')}\n")
    print("| task | " + " | ".join(f"{round(n / 1024)}K" for n in lengths) + " |")
    print("| --- |" + " --- |" * len(lengths))
    for t in tasks:
        print(f"| {t} | " + " | ".join(f"{scores[t, n]:.1f}" for n in lengths) + " |")
    avg = [sum(scores[t, n] for t in tasks) / len(tasks) for n in lengths]
    print("| **avg** | " + " | ".join(f"**{v:.1f}**" for v in avg) + " |")


if __name__ == "__main__":
    main()
