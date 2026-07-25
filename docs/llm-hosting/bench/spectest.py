#!/usr/bin/env python3
"""Realistic spec-decode test: long prompt the output must quote back verbatim.
This is the coding-agent shape (resend file, ask for an edit) where prompt-lookup
n-gram should hit. A short-prompt/novel-output test would show ~zero acceptance.
"""
import json, time, urllib.request, sys
URL = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8000}/v1/completions"
CODE = "\n".join(f"def handler_{i}(request, context):\n"
                 f"    payload = request.get('payload_{i}')\n"
                 f"    if payload is None:\n"
                 f"        raise ValueError('missing payload_{i}')\n"
                 f"    return {{'status': 200, 'body': payload}}\n" for i in range(240))
prompt = (f"Here is a Python module:\n\n{CODE}\n\n"
          "Reproduce handler_0 through handler_12 EXACTLY as written above, verbatim:\n\n")
b = json.dumps({"model": "qwen-3.6", "prompt": prompt, "max_tokens": 400,
                "temperature": 0, "ignore_eos": True}).encode()
t0 = time.perf_counter()
rq = urllib.request.Request(URL, data=b, headers={"Content-Type": "application/json"})
with urllib.request.urlopen(rq, timeout=900) as r:
    d = json.load(r)
el = time.perf_counter() - t0
u = d["usage"]
print(f"prompt={u['prompt_tokens']} gen={u['completion_tokens']} wall={el:.1f}s "
      f"TG={u['completion_tokens']/el:.2f} tok/s")
print("---- first 200 chars of output ----")
print(d["choices"][0]["text"][:200])
