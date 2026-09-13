#!/usr/bin/env python3
"""Reasoning effort A/B on agent-shaped prompts: low vs medium, same prompt, greedy.

The Qwen3.8 template only knows xhigh/medium/low and the Claude Code path
already runs at medium (perf-plan-2026-09-13.md, #2). This measures what the
one remaining step down buys: reasoning tokens, total output tokens, wall
time, and a coarse sanity check (first action is a find_tool call with a
query; analysis turns produce a real answer). Two prompt shapes: first-turn
tool calls, and analysis turns over a pasted tool result, which is where the
reasoning share lives. Run it at a quiet hour; efforts are interleaved so
drift hits both arms equally.

Usage: effort_ab.py PORT MODEL [REPS] [all|tool|analysis]
"""
import json, sys, time, urllib.request

PORT, MODEL = sys.argv[1], sys.argv[2]
REPS = int(sys.argv[3]) if len(sys.argv) > 3 else 3
SHAPE = sys.argv[4] if len(sys.argv) > 4 else "all"
SYSTEM = ("You are an operations agent for a Kubernetes cluster. You have two tools: find_tool to "
          "discover tools by capability and call_tool to invoke one. Always discover before calling. "
          "Be concise.")
# same two-tool contract as toolbench.VMCP_TOOLS (not imported: toolbench runs its bench on import)
VMCP_TOOLS = [
    {"type": "function", "function": {"name": "find_tool", "description": "Search for available tools by capability",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {"name": "call_tool", "description": "Invoke a tool discovered via find_tool",
     "parameters": {"type": "object", "properties": {"tool_name": {"type": "string"}, "parameters": {"type": "object"}},
                    "required": ["tool_name", "parameters"]}}},
]
TOOL_PROMPTS = [
    "The qwen38-27b-vllm pod in namespace ai shows elevated decode latency. Find the right tool, pull "
    "its recent logs and resource usage, and say what you would check next.",
    "Ceph reports one OSD near full. Find a tool to list OSD utilisation, call it, and propose the "
    "least risky rebalancing step.",
    "A Flux Kustomization named llmkube-models is failing to reconcile. Find a tool that shows its "
    "status and events, call it, and explain the likely cause in two sentences.",
    "Jellyfin playback is stuttering for one client. Find a tool that shows recent pod restarts and node "
    "conditions, call it for namespace media, and summarise.",
    "Renovate opened a digest bump for the vllm image. Find a tool that lists open pull requests in "
    "the cluster repo, call it, and say whether the bump is safe to merge given a pinned custom kernel.",
]
ANALYSIS_PROMPTS = [
    "Here is a tool result. Node control-1: 12 vCPU, 62.8 GiB RAM, memory requests 61.2 GiB (98%), "
    "actual use 55.4 GiB, load5 6.2. Pods: vllm 28 GiB RSS req 24 GiB; ceph-osd 3.6 GiB req 4 GiB; "
    "jellyfin 1.2 GiB req 512 MiB; 14 other pods totalling 9 GiB RSS with 19 GiB requests. A new Job "
    "needs 4 GiB requested, 2 GiB actual, for 3 minutes. It is Pending with Insufficient memory. "
    "Decide: lower its request, raise another pod's, or evict something. Justify with the numbers "
    "and state the risk of your choice.",
    "A vLLM engine logs, every 10 s, prompt tok/s, generation tok/s, running and waiting requests. "
    "Over 80 minutes, 19% of 10 s windows had running >= 4 and generation < 5 tok/s with no prompt "
    "tokens booked. Prefill on 24 large cache misses accounted for 60% of all prefill compute. The "
    "scheduler has a per-request per-step prefill cap that was just set to 1600 tokens with a 4096 "
    "token step budget. Predict how the stalled-window share changes and what side effect to watch "
    "for on time-to-first-token of the large requests, with rough numbers.",
    "Two candidate checkpoints of the same 27B model: A keeps lm_head in bf16 (2.54 GB read per decode "
    "step), B packs it to int4 (0.68 GB). Measured per-step time at batch 4 is 44 ms with A. Decode is "
    "memory-bound at about 640 GB/s. Estimate the per-step saving with B, the tok/s gain per stream at "
    "batch 4, and name one reason the real gain could be smaller.",
]


def run(prompt, effort, tools):
    body = {"model": MODEL, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "temperature": 0, "max_tokens": 6000, "chat_template_kwargs": {"reasoning_effort": effort}}
    if tools:
        body["tools"] = VMCP_TOOLS
    rq = urllib.request.Request(f"http://127.0.0.1:{PORT}/v1/chat/completions", data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(rq, timeout=900) as r:
        d = json.load(r)
    dt = time.time() - t
    u = d["usage"]
    msg = d["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    first = calls[0]["function"]["name"] if calls else None
    if tools:
        ok = first == "find_tool" and bool(json.loads(calls[0]["function"]["arguments"]).get("query"))
    else:
        ok = len(msg.get("content") or "") > 200
    return u["completion_tokens"], (u.get("completion_tokens_details") or {}).get("reasoning_tokens"), dt, first, ok


rows = {"low": [], "medium": []}
for rep in range(REPS):
    for i, p in enumerate(TOOL_PROMPTS + ANALYSIS_PROMPTS):
        tools = i < len(TOOL_PROMPTS)
        if SHAPE != "all" and tools != (SHAPE == "tool"):
            continue
        for effort in ("low", "medium") if rep % 2 == 0 else ("medium", "low"):
            out, rt, dt, first, ok = run(p, effort, tools)
            rows[effort].append((out, rt, dt, ok, tools))
            print(f"rep{rep} {'tool' if tools else 'anal'}{i} {effort:6s} out={out:5d} reasoning={rt} {dt:6.1f}s "
                  f"first={first} ok={ok}", flush=True)

for shape, want in (("tool", True), ("analysis", False)):
    for effort, all_rows in rows.items():
        r = [x for x in all_rows if x[4] == want]
        n = len(r)
        if not n:
            continue
        print(f"{shape:8s} {effort:6s} n={n} out mean {sum(x[0] for x in r) / n:.0f} "
              f"reasoning mean {sum(x[1] or 0 for x in r) / n:.0f} wall mean {sum(x[2] for x in r) / n:.1f}s "
              f"ok {sum(x[3] for x in r)}/{n}")
