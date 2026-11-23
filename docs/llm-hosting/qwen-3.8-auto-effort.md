# Effort routing for `qwen-3.8-auto`

[`qwen-3.8-auto.yaml`](../../kubernetes/apps/ai/agentgateway/app/models/qwen-3.8-auto.yaml) is a virtual model whose
`callout` (agentgateway v1.7) asks Perplexity Decider Flash v1.3 a `choice` question (low, medium or xhigh) at
llama.cpp's `/v1/systemone`. A `low` below 0.95 counts as `medium`. A surer `low` goes to `qwen-3.8-fast` (thinking
off, instruct sampling) unless the request has tools, which stays on `qwen-3.8` at `low`; `medium` and `xhigh` go to
`qwen-3.8`. The effort is merged into the client's `chat_template_kwargs` (`none` when fast), since radiance lets the
kwargs win over a top-level `reasoning_effort`. Flash answers with per-option probabilities in one forward pass instead
of generating text. The decision reads the first user message, minus Hermes's appended `<memory-context>` block, and
is cached on it for 24 hours, so a conversation keeps one effort and the 27B's prefix cache stays warm. If the callout
fails, plain `qwen-3.8` answers with the client's effort.

The Kubernetes Service retains the name `d3-nano` for existing clients. See the
[2070 Flash benchmark](../nvidia-gpu-control-1.md#decider-flash-v13-2026-11-23) for decision quality and latency.

## Evidence, 2026-11-01

The original D3-nano setup used llama.cpp directly (#5879). The vLLM Semantic Router's golden-reference check
needed AVX-512 BF16, which no node had, and required a runtime contract llama.cpp did not report.

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

Kev-4B (4B, same API) was another candidate for this callout.

## D3-mini and other decision models, 2026-11-15

On the RTX 2070 (llama.cpp b12407 `server-cuda` (CUDA 12.8; the shipped image is now `server-cuda13`)), d3-nano Q8_0 against vllm-d3-mini Q4_K_M
(ggml-org GGUF), same `/v1/systemone` call as the callout. 24 labelled prompts (the 8 held-out and
16 of the first set, recovered from the session log) plus 4 unlabelled live examples:

| | Correct | Agreement | Sure `low` (>= 0.95) | p50 / p95 warm | VRAM |
| --- | --- | --- | --- | --- | --- |
| d3-nano | 24/24 | 28/28 | 9 | 0.05 / 0.14 s | 2390 MiB |
| d3-mini Q4_K_M | 24/24 | 28/28 | 9 | 0.12 / 0.14 s | 3974 MiB |

Mini is less sure of the live problem reports ("kritika seems stuck" low 0.55 vs 0.70) and of
"restart jellyfin" (0.74 vs 0.94); neither reaches the 0.95 gate. 8-ball bench (`cargo run -p bench
-- --endpoints d3,mini --cascade d3,mini --reference d3`, test split, 1119 cases): nano 88.4%
correct of all (27 wrong), mini 87.5% (12 wrong, decides 89% vs 91%), difference -0.9 points
(95% CI -2.4 to +0.6); mean 33 vs 64 ms. Nano remained in service after that trial, which required a two-point gain.

Not run: Cloudflare clef (27B) and clef-flash (9B; ggml-org GGUF Q4_K_M is 6.7 GB) do not fit
the 2070 beside the other services. kakaocorp/kanana-2-v-decide-3b-2611 (3.67B, Kanana Open
License, released 2026-11-06) scores by cosine similarity between question and answer embeddings;
upstream llama.cpp does not serve that, the only GGUF (jaesang-lee) needs a fork branch that is not
public, transformers in fp16 (Turing has no bf16) needs ~7.3 GB of weights alone, and its card
benchmarks (Decision Index 0.4.1) do not compare with d3's typed-decisions numbers.
