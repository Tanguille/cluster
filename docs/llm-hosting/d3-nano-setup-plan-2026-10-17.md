# Effort routing for `vllm-sr/auto`

Status, 2026-10-30: the D3-nano / vLLM Semantic Router rollout (#5879) is replaced by a CEL rule in
agentgateway. The rule is untested against live traffic. Phase 3 (Qwen2B retirement) stays pending.

## Why the router went

The router supervises d3-nano through `vllm-srun`, which verifies model answers against recorded golden
references. d3-nano only has a generic `cpu` reference recorded on AVX-512 BF16; control-1's Ryzen 7 5800X
emulates BF16, the check failed and the pod crash-looped. `dtype: float32` avoids the check but needs about
9 GiB of the roughly 9 GiB unrequested on control-1. llama.cpp runs D3 (GGUF `ggml-org/vllm-d3-nano-GGUF`,
about 3.5 GiB) but the router rejects it as an attached engine: it requires a runtime contract version
llama.cpp does not report.

## Current design

[`vllm-sr-auto.yaml`](../../kubernetes/apps/ai/agentgateway/app/models/vllm-sr-auto.yaml) is a concrete
AgentgatewayModel (virtual models cannot carry policies) on the qwen-3.8 server. Its `transformations` rewrite
`model` to `qwen-3.8` and set `chat_template_kwargs.reasoning_effort` from the first user message:

- `low`: a brevity request ("one word", "yes or no", "briefly", "tl;dr").
- `xhigh`: math, debugging, root-cause or multi-step wording, or a first message over 4000 characters.
- `medium`: everything else, including ordinary coding.

The first user message keeps the effort fixed for the whole conversation. The effort text opens the system
prompt, so a per-request change would bust the 27B's prefix cache. These are the rules of the Hermes
`qwen38-effort-router` plugin minus its code-plus-tools tier, so everyday coding stays `medium`. A failed
expression drops the field and the template default (`medium`) applies; list-form message content takes that path.

## Open checks

1. Replay prompts against the live gateway: the expression was only evaluated by reading, not run.
2. If the rule cannot judge difficulty well enough, revisit D3 on llama.cpp behind a small contract shim.
3. Cut Hermes over: main model `vllm-sr/auto`, drop the `qwen38-effort-router` plugin and profile effort pins.
4. Phase 3: move Hermes title generation to `qwen-3.8-fast`, then retire Qwen2B.
