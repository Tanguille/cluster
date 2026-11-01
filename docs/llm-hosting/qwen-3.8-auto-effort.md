# Effort routing for `qwen-3.8-auto`

[`qwen-3.8-auto.yaml`](../../kubernetes/apps/ai/agentgateway/app/models/qwen-3.8-auto.yaml) is a virtual model
whose `callout` (agentgateway v1.7) asks D3-nano, a decision model, a `choice` question (low, medium or xhigh) at
llama.cpp's `/v1/systemone`, then sends the request to `qwen-3.8` with that top-level `reasoning_effort`. D3
answers with per-option probabilities in one forward pass instead of generating text. The decision reads the
first user message, minus Hermes's appended `<memory-context>` block, and is cached on it for 24 hours, so a
conversation keeps one effort and the 27B's prefix cache stays warm. If the callout fails, plain `qwen-3.8`
answers with the client's effort.

D3-nano runs on llama.cpp directly, on the iGPU Qwen2B used to hold. The vLLM Semantic Router it ships with
(#5879) could not run here: its golden-reference check needs AVX-512 BF16, which no node has, and it requires a
runtime contract llama.cpp does not report. Hermes title generation moved from Qwen2B to `qwen-3.8-fast`.

## Evidence, 2026-11-01

Same labels, same three criteria. D3-nano ran through `/v1/systemone` on llama.cpp CPU (laptop, podman); Qwen2B
ran with the production JSON-schema prompt on the iGPU.

| | 15 original prompts | 8 held-out prompts | Latency |
|---|---|---|---|
| D3-nano | 15/15 | 8/8 | 0.4-0.65 s |
| Qwen2B | 13/15 | 6/8 | 0.4-0.6 s warm |

Qwen2B's misses all rated one level too low (a landlord email `low`, a nightly Postgres replica lag `medium`). The
lowest D3 confidences were a PodDisruptionBudget explainer (medium, 0.66) and a vLLM vs llama.cpp comparison
(xhigh, 0.66). Earlier, through a temporary test copy, the Qwen2B callout set greeting `low`, probe yaml `medium`
and Ceph crashloop and race condition `xhigh`, read from qwen-3.8's prompt token count.

If D3 misjudges on real traffic, try Kev-4B (4B, same API) behind the same callout.
