# Embedder: CPU vs iGPU (Vulkan)

Decision record for how the Qwen3 embedders run on the iGPU nodes, and why the
GTT leak is handled by native pod-lifetime recycling rather than moving off the GPU.

## Context

Both ggml-Vulkan embedders (`qwen3-embedding`, `vmcp-embedding`) leak pinned
iGPU GTT memory that cgroups cannot see, twice wedging control-2 into NotReady
(2026-07-04, 2026-07-20). These are the two control-2 NotReady incidents that
motivated the native lifetime guard. One proposed fix
(PR #4082) was to move both embedders to `Model.spec.hardware.accelerator: cpu`,
which removes the GTT path entirely and makes memory cgroup-visible. Before
adopting it, we benchmarked the cost.

## Method

Same model and settings on both sides; only the accelerator differs.

- **Model**: Qwen3-Embedding-0.6B, Q8_0.
- **Server args** (identical): `--ctx-size 8192 --batch-size 4096 --ubatch-size 2048 --parallel 2 --embedding --pooling last`.
  - iGPU: `ghcr.io/ggml-org/llama.cpp:server-vulkan`, `--n-gpu-layers 99` (the live `qwen3-embedding` InferenceService).
  - CPU: `ghcr.io/ggml-org/llama.cpp:server`, `--threads 4`, pod limited to `cpu: 4`.
- **Driver**: in-cluster `curl` pod, `POST /v1/embeddings`, 3-request warmup discarded.
- **Workloads**: `short` = one ~200-token input (repeated, 20×); `batch` = ~46 KB
  multi-input payload (5×). `time_total` from curl.

## Results (seconds)

| workload | iGPU (Vulkan) | CPU (4 threads) | iGPU advantage |
|---|---|---|---|
| short, single embed (median) | **0.016** | 0.58 | ~35× |
| short (min / max) | 0.015 / 0.021 | 0.53 / 0.62 | |
| batch ~46 KB (warm mean) | **10.6** | 50.5 | ~4.8× |
| batch, first (cold) | 13.8 | 50.7 | |

## Interpretation

- Single short embeds are ~35× slower on CPU — the Radeon 660M offload
  dominates the tiny 0.6B forward pass, while 4 CPU threads do not.
- The batch gap narrows to ~4.8× because both sides become compute-bound on a
  large input; that ratio reflects raw throughput.
- Embeddings are dragonfly-cached (#3646), so the penalty only lands on cache
  *misses* — but a 35× hit on interactive miss latency (e.g. Hermes tool
  selection) is still material.

## Decision

CPU is 5–35× slower — not worth the regression. **PR #4082 closed.** The iGPU
stays, along with the nodeSelectors / affinity rules that only make sense while
the embedders are GPU-pinned.

The leak is bounded by LLMKube's native `maxPodLifetimeSeconds: 86400` on both
InferenceServices. This support landed in upstream PR #1182 and is available
in chart/CRD version 0.9.10; the chart and CRDs must be upgraded before these
model fields are reconciled. LLMKube copies the lifetime to the generated
Deployment pod template, so each Vulkan embedder is recycled after 24 hours,
including startup and model load time, without bespoke controller logic.
node-exporter's drm collector plus alerts on pinned GTT >4 GiB, MemAvailable
<2 GiB, and missing GTT telemetry are the burst-leak backstop.

`qwen35-2b` (generative, same `server-vulkan` image on the same iGPU) is *not*
enrolled: measured at 32 MiB GTT after 7 h versus the embedders' 2+ GiB, so the
leak is specific to `mode: embedding`, not the Vulkan runtime.

## Alternatives considered

- **llmkube's native eviction watchdog** (`spec.evictionProtection` + the
  metal-agent reaper) remains the reactive node-pressure equivalent and could
  catch burst wedges before the 24-hour lifetime. It is not enabled: the
  metal-agent DaemonSet isn't deployed (only `controllerManager` is), so it
  would add a privileged node agent rather than use the existing CRD field.
- **Upstream driver fix** remains the real resolution: the ggml-Vulkan GTT leak
  itself (ggml-org/llama.cpp #12531 / #15054, open). Native lifetime recycling
  is the current mitigation until that leak is fixed.
