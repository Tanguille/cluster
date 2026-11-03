# Effort routing for `qwen-3.8-auto`

[`qwen-3.8-auto.yaml`](../../kubernetes/apps/ai/agentgateway/app/models/qwen-3.8-auto.yaml) is a virtual model whose
`callout` (agentgateway v1.7) asks D3-nano, a decision model, a `choice` question (low, medium or xhigh) at
llama.cpp's `/v1/systemone`. A `low` below 0.95 counts as `medium`. A surer `low` goes to `qwen-3.8-fast` (thinking
off, instruct sampling) unless the request has tools, which stays on `qwen-3.8` at `low`; `medium` and `xhigh` go to
`qwen-3.8`. The effort is merged into the client's `chat_template_kwargs` (`none` when fast), since radiance lets the
kwargs win over a top-level `reasoning_effort`. D3 answers with per-option probabilities in one forward pass instead
of generating text. The decision reads the first user message, minus Hermes's appended `<memory-context>` block, and
is cached on it for 24 hours, so a conversation keeps one effort and the 27B's prefix cache stays warm. If the callout
fails, plain `qwen-3.8` answers with the client's effort.

D3-nano runs on llama.cpp directly, not under the vLLM Semantic Router it ships with (#5879): the router's
golden-reference check needs AVX-512 BF16, which no node has, and it requires a runtime contract llama.cpp does
not report.

## Evidence, 2026-11-01

Same labels, same three criteria. D3-nano ran through `/v1/systemone` on llama.cpp CPU (laptop, podman); Qwen2B
ran with the production JSON-schema prompt on the iGPU.

| | 15 original prompts | 8 held-out prompts | Latency |
|---|---|---|---|
| D3-nano | 15/15 | 8/8 | 0.4-0.65 s |
| Qwen2B | 13/15 | 6/8 | 0.4-0.6 s warm |

Qwen2B's misses all rated one level too low (a landlord email `low`, a nightly Postgres replica lag `medium`). The
lowest D3 confidences were a PodDisruptionBudget explainer (medium, 0.66) and a vLLM vs llama.cpp comparison
(xhigh, 0.66).

## Why thinking off needs a sure `low`, 2026-11-03

A Hermes thread reporting a broken integration repeated `execute_code` with no reasoning until the guardrail
stopped it: 167 of 167 Hermes streaming requests that evening had zero reasoning tokens. The first message fixes the
effort, and D3 puts such reports at `low`. On the live criteria, greetings, facts and conversions score 0.97 or more,
simple tasks ("restart jellyfin", "check the ceph status") about 0.94, and problem reports ("kritika seems stuck",
"Kritika is not reviewing Renovate PRs in mesh-organiser") 0.64-0.86.

If D3 misjudges on real traffic, try Kev-4B (4B, same API) behind the same callout.
