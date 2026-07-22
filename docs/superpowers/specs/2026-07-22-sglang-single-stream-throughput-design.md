# SGLang Single-Stream Decode Throughput Experiment

**Status:** Draft for review
**Date:** 2026-07-22

## Goal

Improve raw single-stream decode throughput for Qwen3.6-27B AWQ on the AMD R9700 (gfx1201), while preserving long-context stability and avoiding any production restart or in-place deployment change during the experiment.

## Current Evidence

The current v0.5.15 image already contains the established dense-AWQ GEMV optimizations. Production measurements show substantially improved request latency after the rebuild, but no decode-throughput improvement:

- Six-hour mean TTFT: 93.5s → 18.1s.
- Six-hour mean E2E latency: 261.9s → 64.3s.
- Six-hour median decode: 16.61 → 13.26 tok/s.

Historical controlled results indicate approximately 16 tok/s single-stream decode on the current configuration. FP8 KV is retained because prior testing was faster than bf16 KV. HiCache and prefix reuse are capacity/latency features, not expected raw decode multipliers.

## Options Considered

### 1. Isolated kernel rebuild with patches 086 and 087 — recommended

Patch 086 changes AMD flash-decode KV-split behavior; patch 087 improves bf16 PV accumulation. Fork reports suggest the largest potential benefit at very long context, but those results were on a different model and are not evidence for Qwen3.6-27B. This option requires a separately built image and test pod, but does not require changing production.

### 2. Runtime KV-split sweep

Benchmark `SGLANG_KV_SPLITS_OVERRIDE` values 32, 48, 64, and 96 using the existing image. This is a lower-risk baseline and can identify whether occupancy is the limiting factor, but it may require a test pod and is unlikely to match a kernel-level improvement.

### 3. Further kernel profiling

Profile the remaining AWQ/GDN GEMV path and develop another targeted optimization. This has the highest effort and uncertainty, so it is deferred until options 1 and 2 establish whether flash-decode occupancy is the bottleneck.

## Experiment Design

Run an offline or isolated A/B comparison:

- **Control:** current production image and launch configuration.
- **Candidate:** image rebuilt with patches 086 and 087, with the runtime KV-split sweep available if needed.
- Same Qwen3.6-27B AWQ checkpoint, TP=1, FP8 KV, and model settings.
- Single-stream concurrency only.
- Context points: 2K, 32K, 128K, and approximately 180K tokens.
- Measure both cold and warm Triton-cache conditions.
- Use repeated runs at each point and report median plus tail behavior.

Record:

1. Decode tok/s, with context length and generated-token count.
2. TTFT and end-to-end latency.
3. VRAM usage and memory headroom.
4. Correctness/output validity.
5. OOMs, crashes, kernel errors, and cache compilation failures.

## Promotion Gates

The candidate is not eligible for production consideration unless it:

- Improves decode tok/s at the target long-context range on the exact production model.
- Does not regress short-context decode materially.
- Completes all test points without OOM, crash, or correctness failure.
- Does not consume unsafe VRAM headroom.
- Has reproducible results across repeated runs and both cache states.

If the candidate fails these gates, retain the current image and document the measured result. Do not infer benefit from results on another model.

## Safety and Scope

- Do not restart, reconcile, or mutate the production SGLang workload as part of this experiment.
- Do not change the production HelmRelease, image digest, or PVC contents.
- Use a separate image reference and isolated pod/workload for testing.
- Promotion, if justified by the gates, is a separate change requiring explicit approval.

## Expected Outcome

The experiment should determine whether the remaining decode limit is primarily flash-decode occupancy or model-specific AWQ/GDN compute. It is not intended to optimize aggregate concurrency, prefix-cache hit rate, or prefill throughput.
