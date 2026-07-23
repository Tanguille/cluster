# SGLang Single-Stream Decode Throughput Experiment

**Status:** Approved, amended for a controlled maintenance window
**Date:** 2026-07-22

## Goal

Improve raw single-stream decode throughput for Qwen3.6-27B AWQ on the AMD R9700 (gfx1201), while preserving long-context stability. Preparation must not affect production; the same-GPU A/B runs only in an explicitly approved maintenance window and production is restored afterward.

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

Run a sequential A/B comparison during an approved maintenance window because the cluster has one R9700:

- **Control:** current production image and launch configuration.
- **Candidate:** a per-dispatch image rebuilt with patches 086 and 087, identified only by its unique run-token tag at its captured immutable `tag@sha256:digest`.
- Stop the native InferenceService through its GitOps-managed `spec.replicas: 0` state before enabling the benchmark Deployment.
- In the same GitOps transition, temporarily health-check `apps/v1` Deployment `qwen36-27b-decode-ab` instead of `InferenceService/qwen36-27b`, while retaining the existing InferenceService `healthCheckExprs`; restore the original health check before cleanup is complete.
- Run control and candidate as sequential revisions of a Flux-managed `Recreate` Deployment on the same GPU; never run them concurrently.
- Before either arm, fail closed unless the benchmark Pod is the single non-terminating Running/Ready Pod with the expected image, exact command and ordered args, and arm-specific Triton PVC; preserve logs, Pod status/description, and VRAM evidence on every exit.
- Mount the existing model-cache PVC read-only and use experiment-only Triton-cache PVCs.
- Same Qwen3.6-27B AWQ checkpoint, TP=1, FP8 KV, and model settings.
- Single-stream concurrency only.
- Context points: 2K, 32K, 128K, and approximately 180K tokens.
- Measure both cold and warm Triton-cache conditions using the same PVC within
  each arm; cold starts with a newly created PVC and warm follows an approved
  same-image Pod-template restart.
- Run three independent one-prompt processes at each context in both cache
  states, with fixed seed/input/output validation, and report median plus tail
  behavior while preserving each raw result.

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

- Building the candidate and validating manifests must not restart, reconcile, or mutate production.
- The benchmark interface accepts one argument for control and an arm plus full candidate `tag@sha256:digest`; validate all arm-specific state before issuing benchmark requests.
- Live execution requires explicit approval for the maintenance window, GitOps stop, benchmark resources, cleanup, and production restore.
- Do not change the production image digest or mutate the model-cache and production Triton-cache PVC contents.
- Use a separate image reference, isolated Pods, and experiment-only Triton caches.
- The temporary benchmark health check keeps `llmkube-models` and dependent Kustomizations healthy while production is intentionally `Stopped`; restore the original `InferenceService/qwen36-27b` health check before closing the window.
- Restore `spec.replicas: 1` through GitOps after cleanup and verify production readiness before closing the window.
- Promotion, if justified by the gates, is a separate change requiring explicit approval.

## Expected Outcome

The experiment should determine whether the remaining decode limit is primarily flash-decode occupancy or model-specific AWQ/GDN compute. It is not intended to optimize aggregate concurrency, prefix-cache hit rate, or prefill throughput.
