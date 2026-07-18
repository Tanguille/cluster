# LLMKube vLLM Cutover Design

## Goal

Replace the production SGLang backend for `qwen-3.6` with an LLMKube-managed
vLLM service using `tcclaviger/vllm`, while preserving a simple Git rollback to
the current SGLang deployment.

## Scope

- Serve `cyankiwi/Qwen3.6-27B-AWQ-INT4` through LLMKube's native `vllm`
  runtime.
- Keep the existing LiteLLM public aliases: `qwen-3.6` and `qwen-3.6-fast`.
- Use one R9700 with tensor parallelism one, AWQ weights, FP8 KV cache, prefix
  caching, and chunked prefill.
- Retain the SGLang manifests and make rollback a Git revert.

## Non-goals

- Do not introduce low-bit q4/Hadamard KV compression; the selected vLLM image
  does not provide a validated implementation for this model and GPU.
- Do not enable MTP in the initial deployment. Its interaction with Qwen3.6
  hybrid prefix caching requires a separate correctness benchmark.
- Do not run SGLang and vLLM concurrently on the sole R9700.

## Architecture

1. Add a distinct LLMKube `Model` and `InferenceService` under
   `kubernetes/apps/ai/llmkube/models/`.
2. Configure the service with `runtime: vllm` and a Renovate-pinned
   `tcclaviger/vllm` image. The model uses the Hugging Face AWQ checkpoint.
3. Match the R9700 node placement, GPU device access, and model-cache pattern
   used by the existing AI workloads. Confirm the installed LLMKube CRD's
   runtime and AMD scheduling fields during implementation.
4. Repoint the two LiteLLM aliases to the LLMKube vLLM ClusterIP service.
5. Scale the SGLang workload to zero as part of the cutover so the R9700 is
   available before the vLLM pod starts.

Clients continue using LiteLLM. No public model alias or API contract changes.

## Runtime configuration

The initial vLLM configuration is deliberately conservative:

- tensor parallel size: `1`
- quantization: AWQ
- KV cache: FP8 E4M3
- prefix caching: enabled
- chunked prefill: enabled
- MTP/speculative decoding: disabled
- AMD environment: preserve the validated ROCm attention settings where the
  custom image requires them

The image must be immutable by the time it reaches Git; do not deploy Docker
Hub `latest` unpinned.

## Rollout and rollback

The cutover uses three GitOps phases because the two services cannot share the
sole GPU:

1. Add the LLMKube resources with vLLM scaled to zero; production traffic stays
   on SGLang.
2. During an approved maintenance window, scale SGLang to zero and vLLM to one.
   LiteLLM remains pointed at SGLang while vLLM loads and autotunes, so the
   interruption is explicit rather than silently serving an unvalidated engine.
3. After vLLM becomes ready and passes the approved smoke checks, repoint the
   LiteLLM aliases to vLLM.

If vLLM does not become ready or fails functional/benchmark validation, revert
the current phase. Reconciliation restores SGLang's replica and original
LiteLLM endpoint. No manual cluster edits are part of this design.

## Verification

Before a live reconciliation:

1. Render the changed LLMKube and LiteLLM manifests.
2. Inspect the generated vLLM Deployment for the image digest, model source,
   TP=1, AWQ, FP8 KV, prefix-cache, GPU resource, `/dev/kfd`/`/dev/dri` access,
   and health endpoint.
3. Confirm SGLang is the only workload being scaled down and that rollback
   restores its current configuration.

After an explicitly approved live reconciliation, compare vLLM and the
recorded SGLang baseline for cold startup, TTFT, token throughput at 1/2/4/8
sessions, prefix-cache warm TTFT, VRAM capacity, and multi-turn/tool-call
correctness. Test MTP separately only after the baseline is correct.
