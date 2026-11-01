# Effort routing for `qwen-3.8-auto`

[`qwen-3.8-auto.yaml`](../../kubernetes/apps/ai/agentgateway/app/models/qwen-3.8-auto.yaml) is a virtual model
whose `callout` (agentgateway v1.7) asks `qwen35-2b` for `{"effort": "low|medium|xhigh"}` with a JSON schema,
then sends the request to `qwen-3.8` with that top-level `reasoning_effort`. The decision reads the first user
message, minus Hermes's appended `<memory-context>` block, and is cached on it for 24 hours, so a conversation
keeps one effort and the 27B's prefix cache stays warm. If the callout fails, plain `qwen-3.8` answers with the
client's effort.

It replaced the vLLM Semantic Router with d3-nano (#5879): its golden-reference check needs AVX-512 BF16, which
no node has, and llama.cpp lacks the runtime contract the router requires of attached engines.

## Evidence, 2026-11-01

- Qwen2B alone, 15 labeled prompts: 13/15 correct in about 1.6 s each; both misses were trivial tasks rated
  `low` instead of `medium`. A keyword rule scored 6/15 on the same set.
- Through a temporary test copy, read from qwen-3.8's prompt token count against each fixed effort: greeting
  `low`, probe yaml `medium`, Ceph crashloop and race condition `xhigh`, with xhigh keywords in the memory
  block ignored. A repeated first message hit the cache.

If Qwen2B misjudges on real traffic, try D3 or Arch-Router behind the same callout.
