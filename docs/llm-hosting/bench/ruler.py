#!/usr/bin/env python3
"""RULER long-context accuracy (github.com/NVIDIA/RULER): RULER generates the samples and owns the metric, this sends
them to the engine with thinking off and prints a task x length table. Resumable: a rerun skips finished samples.

usage: $RULER_HOME/venv/bin/python ruler.py RUN [--port 18000] [--lengths 4096,...,131072] [--samples 10] [--tasks a,b]

Sequential on purpose: prefill is compute-bound, so a second stream adds load on production, not throughput.
Port-forward, not in the pod as prodshape: exec into the production pod is denied. The port-forward stalls after
30-60 min; restart it on a timer and the retries absorb the cut:
  while :; do timeout 1800 kubectl -n ai port-forward svc/qwen38-27b-vllm 18000:8000; sleep 1; done

Setup, once (RULER_HOME defaults to ~/.local/state/radiance-tune/ruler):
  git clone https://github.com/NVIDIA/RULER $RULER_HOME/RULER   # GIT_LFS_SKIP_SMUDGE=1 without git-lfs
  uv venv --python 3.13 $RULER_HOME/venv && VIRTUAL_ENV=$RULER_HOME/venv uv pip install transformers nltk \
    wonderwords scipy numpy pyyaml tqdm tenacity html2text beautifulsoup4
  hf tokenizer.json, tokenizer_config.json and config.json of the served checkpoint -> $RULER_HOME/tokenizer
  in RULER/scripts/data/synthetic/json: english_words.json (LFS: media.githubusercontent.com), squad.json and
    hotpotqa.json (download_qa_dataset.sh), download_paulgraham_essay.py; nltk punkt and punkt_tab
Generated samples are seeded, but the essays come from live websites: keep $RULER_HOME/data to compare runs.
"""
import argparse, importlib.util, json, os, subprocess, sys, time

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prodshape import post  # noqa: E402

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


def complete(base, prompt, n):
    for attempt in range(30):
        try:
            return post(base, "/v1/completions", {"model": "qwen-3.8", "prompt": prompt, "max_tokens": n,
                                                  "temperature": 0}, 1800)
        except OSError as e:  # HTTPError too; refused, reset, timeout: the port-forward restarting
            if getattr(e, "code", 500) < 500:
                sys.exit(f"HTTP {e.code}: {e.read().decode()[:600]}")
            print(f"  retry {attempt + 1}: {e}", flush=True)
        time.sleep(10)
    sys.exit("30 failed attempts in a row")


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
    # prepare.py shells out to a bare `python`.
    env = dict(os.environ, PATH=os.path.dirname(sys.executable) + os.pathsep + os.environ["PATH"])
    scores = {}
    for length in lengths:
        data = os.path.join(HOME, "data", str(a.samples), str(length))
        for task in tasks:
            src = os.path.join(data, task, "validation.jsonl")
            # Skips a complete file; prints its generator's failure and still exits 0.
            log = subprocess.run([sys.executable, os.path.join(SCRIPTS, "data", "prepare.py"), "--save_dir", data,
                                  "--benchmark", "synthetic", "--task", task, "--tokenizer_type", "hf",
                                  "--tokenizer_path", os.path.join(HOME, "tokenizer"), "--max_seq_length", str(length),
                                  "--model_template_type", "base", "--num_samples", str(a.samples)],
                                 env=env, capture_output=True, text=True)
            if len(rows(src)) != a.samples:
                sys.exit(f"prepare {task} {length} failed:\n{log.stdout[-2000:]}{log.stderr[-2000:]}")
            kind = cfg[task]["task"]
            out = os.path.join(HOME, "runs", a.run, str(length), f"{task}.jsonl")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            done = {r["index"] for r in rows(out)}
            for s in rows(src):
                if s["index"] in done:
                    continue
                t = time.time()
                r = complete(base, WRAP.format(s["input"], s["answer_prefix"]), gen[kind]["tokens_to_generate"])
                pred, tok, dt = r["choices"][0]["text"], r["usage"]["prompt_tokens"], round(time.time() - t, 1)
                with open(out, "a") as f:
                    f.write(json.dumps({"index": s["index"], "pred": pred, "outputs": s["outputs"],
                                        "prompt_tokens": tok, "seconds": dt}) + "\n")
                hit = metric[kind]["metric_fn"]([pred], [s["outputs"]])
                print(f"{length} {task} #{s['index']} {tok} tok {dt:.0f}s {hit:.0f} {pred.strip()[:80]!r}", flush=True)
            # Raw preds: RULER's postprocess (strip, control characters to newlines) cannot change a substring match
            # of its references, which hold neither.
            got = rows(out)
            scores[task, length] = metric[kind]["metric_fn"]([g["pred"] for g in got], [g["outputs"] for g in got])
    print(f"\n{a.run}, {a.samples} samples per cell\n")
    print("| task | " + " | ".join(f"{n // 1024}K" for n in lengths) + " |")
    print("| --- |" + " --- |" * len(lengths))
    for task in tasks:
        print(f"| {task} | " + " | ".join(f"{scores[task, n]:.1f}" for n in lengths) + " |")
    avg = [sum(scores[t, n] for t in tasks) / len(tasks) for n in lengths]
    print("| **avg** | " + " | ".join(f"**{v:.1f}**" for v in avg) + " |")


if __name__ == "__main__":
    main()
