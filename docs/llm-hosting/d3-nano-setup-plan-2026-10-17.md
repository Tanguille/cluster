# Effort routing for `vllm-sr/auto`

Status, 2026-11-01: Qwen2B picks qwen-3.8's reasoning effort through an agentgateway callout. The D3-nano /
vLLM Semantic Router rollout (#5879) is gone, and Qwen2B stays as the decision model.

## Why the router went

The router supervises d3-nano through `vllm-srun`, which verifies model answers against recorded golden
references. d3-nano only has a generic `cpu` reference recorded on AVX-512 BF16, which no node has (5800X,
6600H); the check failed and the pod crash-looped. `dtype: float32` avoids the check but needs about 9 GiB,
nearly all control-1 has unrequested. llama.cpp runs D3 (GGUF `ggml-org/vllm-d3-nano-GGUF`, about
3.5 GiB) but the router rejects it as an attached engine: it requires a runtime contract version llama.cpp
does not report.

## Current design

[`vllm-sr-auto.yaml`](../../kubernetes/apps/ai/agentgateway/app/models/vllm-sr-auto.yaml) is a virtual model
whose `callout` (agentgateway v1.7) asks `qwen35-2b` for `{"effort": "low|medium|xhigh"}` with a JSON schema,
then sends the request to `qwen-3.8` with that top-level `reasoning_effort`. The decision reads the first user
message, minus Hermes's appended `<memory-context>` block, and is cached on it for 24 hours, so a conversation
keeps one effort and the 27B's prefix cache stays warm. The field is top-level because vLLM lets it beat
`chat_template_kwargs`, and Hermes sends a top-level `medium` on every turn.

`qwen-3.8-auto-rule` decides with keywords when the callout fails (its `fallback`) or Qwen2B answers outside
the enum (the `model` transformation): `low` for brevity requests, `xhigh` for math, debugging, root-cause or
multi-step wording or a first message over 4000 characters, else `medium`.

## Evidence, 2026-11-01

- Qwen2B alone, 15 labeled prompts: 13/15 correct in about 1.6 s each; both misses were trivial tasks rated
  `low` instead of `medium`. The keyword rule scored 6/15 on the same set.
- Through a temporary `vllm-sr/auto-test` copy, read from qwen-3.8's prompt token count against each fixed
  effort: greeting `low`, probe yaml `medium`, Ceph crashloop and race condition `xhigh` (the rule says
  `medium` for all four), with xhigh keywords in the memory block ignored. A repeated first message hit the
  cache. With the callout URL broken, the keyword rule answered.

## Open checks

1. If Qwen2B misjudges on real traffic, try D3 or Arch-Router behind the same callout.
2. Hermes title generation still uses Qwen2B, which now stays.
