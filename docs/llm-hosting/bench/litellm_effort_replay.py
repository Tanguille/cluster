#!/usr/bin/env python3
"""Replay the request shapes our clients send at a local litellm; the mock backend logs what arrives."""
import json, urllib.request, urllib.error

L = "http://127.0.0.1:14001"
H = {"Authorization": "Bearer sk-test", "Content-Type": "application/json", "anthropic-version": "2023-06-01"}
msgs = [{"role": "user", "content": "hi"}]


def post(path, body, tag):
    rq = urllib.request.Request(L + path, data=json.dumps(body).encode(), headers=H)
    try:
        with urllib.request.urlopen(rq, timeout=30) as r:
            r.read()
        print(f"SENT {tag}: ok", flush=True)
    except urllib.error.HTTPError as e:
        print(f"SENT {tag}: HTTP {e.code} {e.read()[:140]!r}", flush=True)


post("/v1/chat/completions", {"model": "qwen-3.8", "messages": msgs}, "chat qwen-3.8 plain")
post("/v1/messages", {"model": "qwen-3.8", "max_tokens": 100, "messages": msgs}, "messages qwen-3.8 no thinking")
post("/v1/messages", {"model": "qwen-3.8", "max_tokens": 100, "messages": msgs,
                      "thinking": {"type": "adaptive"}}, "messages qwen-3.8 thinking adaptive")
post("/v1/messages", {"model": "qwen-3.8", "max_tokens": 100, "messages": msgs,
                      "thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"}},
     "messages qwen-3.8 adaptive + output_config.effort=medium")
post("/v1/messages", {"model": "qwen-3.8", "max_tokens": 100, "messages": msgs,
                      "thinking": {"type": "enabled", "budget_tokens": 8000}}, "messages qwen-3.8 budget 8000")
post("/v1/messages", {"model": "qwen-3.8", "max_tokens": 100, "messages": msgs,
                      "thinking": {"type": "enabled", "budget_tokens": 1024}}, "messages qwen-3.8 budget 1024")
post("/v1/messages", {"model": "qwen-3.8", "max_tokens": 100, "messages": msgs,
                      "thinking": {"type": "disabled"}}, "messages qwen-3.8 thinking disabled")
post("/v1/messages", {"model": "qwen-3.8-fast", "max_tokens": 100, "messages": msgs}, "messages qwen-3.8-fast")
post("/v1/responses", {"model": "qwen-3.8", "input": "hi"}, "responses qwen-3.8 plain")
