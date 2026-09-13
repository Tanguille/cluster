#!/usr/bin/env python3
"""Decode rate under grammar-constrained tool calling, optionally concurrent.

Production traffic on this model is tool calling, and that regime is exactly
where MTP + spec decode wedged to ~0.2 tok/s (100x) on this hardware. Any
speculative config has to be cleared here, not just on plain completions.

Reports wall-clock decode rate with TTFT separated, plus the spec-decode
counters, so a wedge shows up as a collapsed rate rather than a plausible-
looking average. CONC > 1 also exercises the seqs x (spec+1) batch shape that
single-stream benchmarks never reach.

SYSTEM_FILE loads a system prompt (e.g. the assembled Hermes one) so the
measurement includes the long stable prefix production actually sends. PRESET
picks the tool schema: `deployment` is a wide synthetic schema that stresses the
grammar, `vmcp` is the real two-tool contract (find_tool + call_tool) this
cluster's gateway exposes.

Usage: toolbench.py PORT MODEL [CONC] [REPS] [SYSTEM_FILE] [PRESET]
"""
import json, sys, time, urllib.request, statistics as st
from concurrent.futures import ThreadPoolExecutor

from _metrics import sample, wait_idle

PORT, MODEL = int(sys.argv[1]), sys.argv[2]
CONC = int(sys.argv[3]) if len(sys.argv) > 3 else 1
REPS = int(sys.argv[4]) if len(sys.argv) > 4 else 2
SYSTEM_FILE = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] != "-" else None
PRESET = sys.argv[6] if len(sys.argv) > 6 else "deployment"
SYSTEM = open(SYSTEM_FILE).read() if SYSTEM_FILE else None
GEN = 400

# A schema wide enough that the grammar actually constrains a long generation;
# the argument names are what a drafter/lookup would try to predict.
TOOLS = [{
    "type": "function",
    "function": {
        "name": "create_deployment",
        "description": "Create a Kubernetes deployment manifest",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "namespace": {"type": "string"},
                "image": {"type": "string"},
                "replicas": {"type": "integer"},
                "cpu_request": {"type": "string"},
                "memory_request": {"type": "string"},
                "cpu_limit": {"type": "string"},
                "memory_limit": {"type": "string"},
                "env_vars": {"type": "array", "items": {"type": "string"}},
                "volume_mounts": {"type": "array", "items": {"type": "string"}},
                "node_selector": {"type": "string"},
                "service_port": {"type": "integer"},
                "readiness_path": {"type": "string"},
                "liveness_path": {"type": "string"},
                "notes": {"type": "string"},
            },
            "required": ["name", "namespace", "image", "replicas", "cpu_request",
                         "memory_request", "cpu_limit", "memory_limit", "env_vars",
                         "volume_mounts", "node_selector", "service_port",
                         "readiness_path", "liveness_path", "notes"],
        },
    },
}]

# The real gateway contract: vmcp exposes exactly these two tools, and the
# argument key is `parameters`. Narrow schema, so the grammar constrains far
# less than the preset above -- which is the point of testing both.
VMCP_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_tool",
            "description": "Search for available tools by capability",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "call_tool",
            "description": "Invoke a tool discovered via find_tool",
            "parameters": {
                "type": "object",
                "properties": {
                    "tool_name": {"type": "string"},
                    "parameters": {"type": "object"},
                },
                "required": ["tool_name", "parameters"],
            },
        },
    },
]

VMCP_ASK = ("The qwen38-27b-vllm pod in namespace ai is showing elevated decode "
            "latency. Investigate: find the right tool, then call it to pull the "
            "pod's recent logs and its current resource usage, and summarise what "
            "you would check next.")

ASK = ("Create a deployment named qwen38-27b-vllm in namespace ai using image "
       "vllm/vllm-openai-rocm:nightly with 1 replica, 2 CPU and 32Gi memory "
       "requests, 4 CPU and 48Gi limits, env vars VLLM_ROCM_USE_AITER=1 and "
       "HIP_VISIBLE_DEVICES=0, volume mounts /cache and /kvoffload, node "
       "selector amd.com/gpu=true, service port 8000, readiness and liveness "
       "both on /health. Add a detailed notes field explaining the choices.")


def one(salt):
    """One tool call; returns (ttft, decode_s, tokens)."""
    vmcp = PRESET == "vmcp"
    msgs = []
    if SYSTEM:
        msgs.append({"role": "system", "content": SYSTEM})
    msgs.append({"role": "user", "content": f"[run {salt}] {VMCP_ASK if vmcp else ASK}"})
    payload = {
        "model": MODEL,
        "messages": msgs,
        "tools": VMCP_TOOLS if vmcp else TOOLS,
        "max_tokens": GEN, "temperature": 0.7, "stream": True,
        "stream_options": {"include_usage": True},
    }
    if not vmcp:
        # Forced, so every rep generates the same wide structured payload.
        payload["tool_choice"] = {"type": "function",
                                  "function": {"name": "create_deployment"}}
    body = json.dumps(payload).encode()
    t0 = time.perf_counter()
    ttft, usage = None, {}
    rq = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions",
                                data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(rq, timeout=300) as resp:
        for raw in resp:
            if not raw.startswith(b"data: "):
                continue
            chunk = raw[6:].strip()
            if chunk == b"[DONE]":
                break
            d = json.loads(chunk)
            if d.get("usage"):
                usage = d["usage"]
            if ttft is None and d.get("choices"):
                delta = d["choices"][0].get("delta") or {}
                if delta.get("content") or delta.get("tool_calls"):
                    ttft = time.perf_counter() - t0
    total = time.perf_counter() - t0
    return ttft or 0, total - (ttft or 0), usage.get("completion_tokens", 0)


print(f"conc={CONC}  {'rep':>4} {'TTFT s':>8} {'decode s':>9} {'toks':>6} "
      f"{'agg tok/s':>10} {'per-stream':>11} {'accept%':>8}", flush=True)
aggs = []
for r in range(REPS):
    # One scrape serves both the idle gate and the pre-run counter baseline.
    before = wait_idle(PORT)
    if not before.idle:
        print(f"       {r + 1:>4}  SKIPPED -- engine not idle, would contaminate",
              flush=True)
        continue
    w0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=CONC) as ex:
        res = list(ex.map(one, [f"{time.time_ns()}-{i}" for i in range(CONC)]))
    wall = time.perf_counter() - w0
    after = sample(PORT)
    toks = sum(x[2] for x in res)
    ttft = st.median([x[0] for x in res])
    dec = st.median([x[1] for x in res])
    agg = toks / wall if wall else 0
    drafted = after.draft_tokens - before.draft_tokens
    acc = after.accepted - before.accepted
    aggs.append(agg)
    print(f"       {r + 1:>4} {ttft:>8.2f} {dec:>9.2f} {toks:>6} {agg:>10.2f} "
          f"{agg / CONC:>11.2f} {100 * acc / drafted if drafted else 0:>8.1f}",
          flush=True)

if not aggs:
    raise SystemExit("no clean reps -- engine never went idle, nothing measured")
print(f"\nn={len(aggs)} clean   median aggregate tok/s: {st.median(aggs):.2f}"
      f"   per stream: {st.median(aggs) / CONC:.2f}", flush=True)
