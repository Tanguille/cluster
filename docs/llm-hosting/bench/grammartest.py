#!/usr/bin/env python3
"""Does grammar-constrained tool calling explain the decode gap?

Production decodes at 3.8 tok/s. A plain completion at conc 1 with a 48K
context measures 11.5 tok/s. The untested difference is that production traffic
is tool-calling, which engages grammar-constrained decoding - the same path
that wedged MTP 100x on this hardware.

Holds context length, concurrency and generation length fixed; the ONLY
variable is whether a tool schema is attached. Two tools, matching the vmcp
gateway's actual 2-tool contract (find_tool + call_tool).
"""
import json, sys, time, urllib.request

URL = "http://127.0.0.1:18000/v1/chat/completions"
GEN = 64
WORDS = ("storage replication consensus quorum latency throughput partition ledger "
         "checkpoint compaction manifest snapshot heartbeat gossip shard rebalance "
         "durability coordinator epoch lease tombstone compress index segment").split()

TOOLS = [
    {"type": "function", "function": {
        "name": "find_tool",
        "description": "Search for a tool by capability.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "capability to search for"},
            "limit": {"type": "integer", "description": "max results"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "call_tool",
        "description": "Invoke a tool by name with parameters.",
        "parameters": {"type": "object", "properties": {
            "tool_name": {"type": "string"},
            "parameters": {"type": "object"}},
            "required": ["tool_name", "parameters"]}}},
]


def make_prompt(nwords, salt):
    rnd, parts = salt, []
    for i in range(nwords):
        rnd = (rnd * 1103515245 + 12345 + i) & 0x7FFFFFFF
        parts.append(WORDS[rnd % len(WORDS)])
        if i % 18 == 17:
            parts.append(".\n")
    return f"Run {salt} unique. Technical corpus:\n" + " ".join(parts) + \
           "\n\nSummarize the corpus above in one sentence."


def run(nwords, with_tools, label):
    payload = {
        "model": "qwen-3.8",
        "messages": [{"role": "user", "content": make_prompt(nwords, int(time.time_ns() % 10**9))}],
        "max_tokens": GEN, "temperature": 0.7, "stream": True,
        "stream_options": {"include_usage": True},
    }
    if with_tools:
        payload["tools"] = TOOLS
        payload["tool_choice"] = "auto"
    req = json.dumps(payload).encode()
    t0 = time.perf_counter()
    ttft, n, usage = None, 0, {}
    r = urllib.request.Request(URL, data=req, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=1800) as resp:
        for raw in resp:
            if not raw.startswith(b"data: "):
                continue
            c = raw[6:].strip()
            if c == b"[DONE]":
                break
            d = json.loads(c)
            if d.get("usage"):
                usage = d["usage"]
            ch = (d.get("choices") or [{}])[0]
            delta = ch.get("delta") or {}
            # count any token-bearing delta. This engine emits thinking tokens
            # under `reasoning` (NOT `reasoning_content`) - verified against a
            # live stream; missing it counts zero tokens and voids the run.
            got = bool(delta.get("content")) or bool(delta.get("reasoning")) \
                or bool(delta.get("reasoning_content")) or bool(delta.get("tool_calls"))
            if got:
                if ttft is None:
                    ttft = time.perf_counter() - t0
                n += 1
    # usage is authoritative: an SSE delta is not guaranteed to carry exactly one token.
    n = usage.get("completion_tokens") or n
    total = time.perf_counter() - t0
    decode = total - ttft if ttft else 0
    rate = n / decode if decode else 0
    print(f"  {label:>26}: prompt={usage.get('prompt_tokens','?'):>7} tok  "
          f"TTFT={ttft if ttft else 0:7.2f}s  decode {n:>3} tok in {decode:6.2f}s = {rate:5.2f} tok/s",
          flush=True)
    return rate


if __name__ == "__main__":
    ctx = int(sys.argv[1]) if len(sys.argv) > 1 else 38000
    print(f"conc=1, gen={GEN}, ctx~{ctx} words. Only variable: tool schema attached.", flush=True)
    plain = run(ctx, False, "no tools (plain chat)")
    tools = run(ctx, True, "WITH tools (grammar)")
    if plain and tools:
        print(f"\n  grammar cost on decode: {plain/tools:.2f}x", flush=True)
