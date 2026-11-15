# llama.cpp services on the RTX 2070 Implementation Plan

**Created:** 2026-11-15
**Status:** In Progress (Chunks 1-4 done, Chunk 5 in PR; embedder cutover and 24 h checks open)
**Complexity:** High
**Estimated Chunks:** 6
**Branch:** `feat/2070-llamacpp`
**Worktree:** none: executed in a fresh clone at `../cluster-2070` (the main checkout's `.git` has corrupt loose objects)

---

## Overview

Move all three llmkube llama.cpp services (embeddinggemma-2, bge-reranker-v2-m3, d3-nano) from the control-2/3 iGPUs
(Vulkan, `squat.ai/dri`) to the RTX 2070 on control-1 (CUDA, `nvidia.com/gpu` time-slice). Evaluate a reranker
upgrade (Qwen3-Reranker-0.6B first) and a decision-model upgrade (d3-mini or a 2-4B alternative) on the real card
before switching either. Consumers address services by Service DNS name, so names stay and no consumer changes.

Base: `origin/main` after #6915 (the main checkout is stale at #6914 and its `.git` has corrupt loose objects: make
the worktree from a fresh clone or after `git fsck` repair, not from the main checkout).

---

## User Stories

### US-1: Services on the 2070

**As** the owner **I want** embedder, reranker and decision model on the 2070 **so that** the iGPU GTT leak, the
control-2/3 memory pressure (90% / 81% `kubectl top nodes`) and Vulkan workarounds go away.

#### Acceptance Criteria

- [ ] All three Deployments 1/1 on control-1, `nvidia.com/gpu` 3 of 8 allocated.
- [ ] `DCGM_FI_DEV_FB_USED` steady state at or below 6.5 GiB, 7d peak below 7.0 GiB (card: 7785 MiB free + 405 MiB reserved idle, DCGM query).
- [ ] memini: 0 `embed failed` lines, rerank fallback 0 over 24 h; qwen-3.8-auto callout errors 0; eightball decisions succeed.

### US-2: Better reranker if the numbers say so

- [ ] Switch only if Chunk 3's go criteria hold.

### US-3: Better decision model if the numbers say so

- [ ] Switch only if Chunk 4's go criteria hold.

---

## Technical Analysis

### Verified facts

| Fact | Evidence |
|------|----------|
| llmkube supports NVIDIA natively: `Model.spec.hardware.accelerator` enum includes `cuda`, `gpu.vendor: nvidia` defaults `resourceName` to `nvidia.com/gpu`, `InferenceService.spec.runtimeClassName` exists | `kubectl explain model.spec.hardware.*`, `kubectl explain inferenceservice.spec.runtimeClassName` |
| Default `gpuSharing.mode` is `exclusive`; on a time-sliced node one pod = one of 8 replicas. `shared` mode needs a chart pool selector and gives no runtime VRAM cap anyway | `kubectl explain inferenceservice.spec.resources.gpuSharing`, LLMKube `internal/controller/gpu_sharing.go` |
| llmkube only emits `--flash-attn on` when `spec.flashAttention: true`; its doc claims Ampere+ for NVIDIA, so control FA via `extraArgs` | `internal/controller/runtime_llamacpp_args.go:82-86` |
| Deployments are `Recreate`, priority `llmkube-normal` | `kubectl -n ai get deploy ... -o custom-columns` |
| Model cache is RWX CephFS (`llmkube-model-cache`, 16Gi), node move needs no re-download | `kubectl -n ai get pvc` |
| Current pods: embedder control-2, reranker + d3-nano control-3; iGPU VRAM+GTT 1.86 + 3.85 = 5.70 GiB | VictoriaMetrics `node_drm_memory_vram_used_bytes + node_drm_memory_gtt_used_bytes` |
| Host RSS per pod at most 0.35 GiB, CPU p99 at most 0.04 core over 7d | `container_memory_rss`, `container_cpu_usage_seconds_total` in VictoriaMetrics |
| control-1: 11 CPU / 59.3 GiB allocatable, requests 4.8 CPU (43%) / 37.9 GiB (63%), radiance running | `kubectl describe node control-1` |
| 2070 idle: FB used 0, free 7785 MiB, reserved 405 MiB, 30 C, 6.3 W | `DCGM_FI_DEV_FB_*`, `DCGM_FI_DEV_GPU_TEMP`, `DCGM_FI_DEV_POWER_USAGE` |
| Consumers use Service names only: memini, karakeep, vMCP optimizer, kritika (via agentgateway), embedding-cache nginx, agentgateway backend, qwen-3.8-auto callout, eightball | `grep -rnE "embeddinggemma-2\|bge-reranker-v2-m3\|d3-nano" kubernetes` |
| Embedder per-input cap must exceed 4105 tokens (kritika chunk) | `embeddinggemma-2.yaml` comment |
| d3-nano real prompts 160-805 tokens | `kubectl -n ai logs deploy/d3-nano` (`n_tokens`) |
| `ai` ingress CNP is namespace-scoped, no node or pod-label dependency | `kubernetes/apps/ai/networkpolicy.yaml` |
| UPS shed query reads `node_hwmon_power_average_watt{kubernetes_node="control-1"}` (R9700 hwmon); the 2070 has no hwmon (nvidia_drm not loaded), so the shed does not see it | `ups-load-shed.yaml:30-35` |
| GTT alerts are scoped to control-2/3 and stay valid (iGPUs keep other users) | `node-exporter/app/prometheusrule.yaml` |
| codingagent_v1 (166 queries) lives in memini's `cmd/bench`, not in this repo | memini HR comment, memory `project_reranker_igpu_tuning_2026_10_06` |
| Decision-model bench exists: 8-ball `8ball evaluate` / `dev/bench.toml` (spam, SST-2, Banking77, pinned splits) | `~/Documents/PriveProjecten/8-ball/docs/benchmark.md` |

### New Files

| File | Purpose |
|------|---------|
| none | Changes stay in the existing model manifests |

### Modified Files

| File | Changes |
|------|---------|
| `kubernetes/apps/ai/llmkube/models/embeddinggemma-2.yaml` | cuda/nvidia, CUDA image, `runtimeClassName: nvidia`, drop iGPU nodeSelector/affinity/groups, FA on, requests |
| `kubernetes/apps/ai/llmkube/models/bge-reranker-v2-m3.yaml` | same; or replaced by the winning reranker file (Chunk 5) |
| `kubernetes/apps/ai/llmkube/models/d3-nano.yaml` | same; or replaced by the winning decision model (Chunk 6) |
| `kubernetes/apps/ai/llmkube/app/helmrelease.yaml` | `prometheus.prometheusRule.rules.gpuHealth.enabled: true` (DCGM alerts), modelCache size comment |
| `kubernetes/apps/ai/memini/app/helmrelease.yaml` | only if reranker changes: `MEMINI_RERANK`, `MEMINI_RERANK_MODEL`, timeout comments |
| `kubernetes/apps/ai/agentgateway/app/models/qwen-3.8-auto.yaml`, `kubernetes/apps/ai/eightball/app/helmrelease.yaml` | only if the decision model's Service name changes |
| `docs/llm-hosting/qwen-3.8-auto-effort.md`, `docs/llm-hosting/reranker-cpu-vs-igpu.md` | evidence sections |

---

## Decisions & Trade-offs

### D1: How to target the 2070

**Chosen:** llmkube native: `accelerator: cuda`, `gpu.vendor: nvidia`, `count: 1`, no `resourceName`, `runtime` unset,
`InferenceService.runtimeClassName: nvidia`. No nodeSelector: only control-1 advertises `nvidia.com/gpu`.
Drop `supplementalGroups` 44/226 (DRI only); keep `runAsUser: 101`, `fsGroup: 102` (model cache).

### D2: Image

**Chosen:** `ghcr.io/ggml-org/llama.cpp:server-cuda@sha256:22bd49f4cf68236a63c5a187d7d1867ffb08472611ddfbf0392b6842a1b30d44`
(b12407, `crane digest ghcr.io/ggml-org/llama.cpp:server-cuda`, 2026-11-15; re-read at execution time). CUDA 12.8.1
(docker workflow matrix, `.devops/cuda.Dockerfile`); needs driver >= 570.124.06
([CUDA release notes](https://docs.nvidia.com/cuda/cuda-toolkit-release-notes/index.html)), we run 595.112.07.

- Turing is built only as PTX (`75-virtual` in `ggml/src/ggml-cuda/CMakeLists.txt`), so the first start JIT-compiles:
  67 s cold vs 4.4 s warm on an RTX 2060 ([#29513](https://github.com/ggml-org/llama.cpp/issues/29513), maintainer:
  no Turing SASS). Chunk 2 measures it; if cold start is over 10 s, set `CUDA_CACHE_PATH` to a dir on the RWX model
  cache PVC (group 102 writable) and `CUDA_CACHE_MAXSIZE` large enough, so a Recreate does not re-JIT.
- Not `server-cuda13` (CUDA 13.5): sm_75 crash "unsupported PTX toolchain" on a T4
  ([#31503](https://github.com/ggml-org/llama.cpp/issues/31503), open).
- Vulkan-on-NVIDIA rejected: needs nvidia_drm/ICD wiring, not loaded by design.
- Renovate: same rolling `server-cuda@sha256` pattern as `server-vulkan` (no build-tag pins).
- Known CUDA bugs: Qwen3-style decoder rerankers copy n_vocab logits per token, ~6x slower rerank on a x1 link
  ([#30422](https://github.com/ggml-org/llama.cpp/issues/30422), open, scores unchanged; bge is an encoder, not
  affected). Never run `--embeddings` and `--reranking` in one server ([#30046](https://github.com/ggml-org/llama.cpp/issues/30046)).

### D3: Flash attention

FA works on Turing through the mma-f16 path ([#28761](https://github.com/ggml-org/llama.cpp/issues/28761) is perf
only). EmbeddingGemma 2 (head_dim 512) fell back to CPU attention with `-fa on` and `-fa auto` disabled FA on an
RTX 2060 until [PR #30416](https://github.com/ggml-org/llama.cpp/pull/30416) (in b12407); its compute buffer at
`-ub 8192` with FA on is 1088 MiB ([#30398](https://github.com/ggml-org/llama.cpp/issues/30398)). So embedder:
`-fa on` explicitly. Reranker: re-measure on and off (`-fa off` was an iGPU finding). d3: default.
Keep FA only where Chunk 2 shows identical outputs.

### D4: Clean move vs iGPU standby

**Chosen:** clean move, iGPU manifests recoverable by `git revert`. A permanent standby doubles every service and
keeps the GTT leak and memory pressure that motivate the move. Owner decision O2 below.

### D5: Reranker

Candidates, in order: **Qwen3-Reranker-0.6B** Q8_0 ([ggml-org GGUF](https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF),
639 MB, Apache-2.0, 100+ languages; self-reported MTEB-R 65.80 vs 57.03, MTEB-Code 73.42 vs 41.38
[card](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B); third party: MTEB(eng,v2) 0.5940 vs 0.5526
[Ettin blog](https://huggingface.co/blog/ettin-reranker), so the general gain is small and the code gain is the
consistent one). Runner-up **gte-reranker-modernbert-base** (149M, English only, ~150 MB Q8_0, 0.5843 third party,
CoIR 79.99 self; llama.cpp PRs [#29627](https://github.com/ggml-org/llama.cpp/pull/29627) and
[#29692](https://github.com/ggml-org/llama.cpp/pull/29692), community GGUF only). Rejected: Jina v3/v3.5 and
ctxl-rerank-v2 (non-commercial), zerank-2 and Qwen3-Reranker-4B (4B, ~1.2 s per 15-pair call at 100% of peak, real
likely several s), Ettin (no merged head support, [#29628](https://github.com/ggml-org/llama.cpp/pull/29628) closed),
mxbai-v2 and nemotron-1b (no llama.cpp rank path found). Go only on Chunk 3 thresholds.

### D6: Decision model

d3 answers typed questions at llama.cpp `/v1/systemone` with per-option probabilities in one forward pass
([PR #29818](https://github.com/ggml-org/llama.cpp/pull/29818), vllm-d3 in [PR #30383](https://github.com/ggml-org/llama.cpp/pull/30383)).
Both consumers depend on it (qwen-3.8-auto reads `callout.body.answers.effort.choice`; eightball `kind = "systemone"`).
A general instruct model gets 501 there (server README), so it is no drop-in: eightball could use `kind = "logprob"`,
the callout would need a rewrite. Out.
**Candidate: d3-mini Q4_K_M** ([ggml-org/vllm-d3-mini-GGUF](https://huggingface.co/ggml-org/vllm-d3-mini-GGUF), 3708 MB;
Q8_0 5158 MB; same `qwen35` arch, same API, Apache-2.0). Independent typed-decisions run (400 cases, zero-shot):
mini 0.743 vs nano 0.629 accuracy, ECE 0.047 vs 0.113 ([leaderboard](https://huggingface.co/buckets/SaylorTwift/typed-decisions-evals)).
Vendor public-suite scores nano 42.22, mini 54.90, flash 57.79 are self-reported ([card](https://huggingface.co/vllm-sr/d3-mini)).
Alternative: Kev-4B ([card](https://huggingface.co/jaredpalmer/kev-4b), Q4_K_M 3033 MB, English only, self-reported
scores only). d3-lite (0.529 accuracy) is a downgrade. Mini Q8_0 does not fit (budget below).

---

## VRAM budget (8 GiB card, 7785 MiB usable at idle)

Estimates from before Chunk 2; the measured numbers are in Results below.

Weights are file sizes from the HF tree API (MB converted to GiB). CUDA context per process is unmeasured.

| Service | Weights | KV | Compute | CUDA ctx (est.) | Total est. |
|---|---|---|---|---|---|
| embeddinggemma-2 Q8_0, ubatch 8192, FA on | 0.29 | none (bidirectional) | 1.06 (#30398) | 0.3 | **1.65** |
| embeddinggemma-2, ubatch 6144, FA on | 0.29 | none | ~0.8 (scaled, unmeasured) | 0.3 | 1.4 |
| bge-reranker-v2-m3 Q8_0, ctx 2048 | 0.59 | small | ~0.2 | 0.3 | **1.1** |
| d3-nano Q8_0, 2 x 4096 | 2.38 | ~0.2 (hybrid GDN) | ~0.3 | 0.3 | **3.2** |
| **Sum today's three** | | | | | **~5.95 GiB of 7.6, spare ~1.65** |
| Qwen3-Reranker-0.6B Q8_0 (instead of bge) | 0.60 | small | ~0.3 | 0.3 | 1.2 (+0.1) |
| d3-mini Q4_K_M (instead of nano), ctx 2 x 2048 | 3.45 | ~0.2 | ~0.3 | 0.3 | **4.25** |
| d3-mini Q8_0 | 4.80 | ~0.3 | ~0.3 | 0.3 | 5.7: with the rest 8.5, **does not fit** |
| **Sum embedder (ub 6144) + Qwen3-Reranker + d3-mini Q4_K_M** | | | | | **~6.85 GiB, spare ~0.75** |

Headline: today's three fit with ~1.6 GiB spare. d3-mini only at Q4_K_M and only with the embedder at ubatch 6144
and d3 ctx 4096 total; spare ~0.75 GiB is under the 1 GiB rule, so it is an owner call (O3) after Chunk 2 numbers.
What to shrink, in order: embedder ubatch 8192 -> 6144 (still above kritika's 4105-token chunk); d3 ctx 8192 -> 4096
total (prompts max ~805 tokens in logs, callout caps input at ~2000 chars; whole prompt must fit `--ubatch-size`);
d3 slots stay 2 (eightball runs at concurrency 2).
No memory isolation between time-slice replicas: one runaway (the embedder's batch high-water mark, see memory
`igpu-request-vs-actual-gap`) can OOM the others. Mitigation: measured worst-case batch in Chunk 2, keep 1 GiB spare,
`DCGM_FI_DEV_FB_FREE` alert below 512 MiB.

---

## Risks

- **R1 (biggest): single failure domain.** Everything moves to control-1 (Ceph osd.3/mon, etcd, radiance, VM on the
  TrueNAS host that has reset itself). When control-1 is down the embedder is down: memini recall falls back to
  keyword-only, writes go vectorless, Hermes prefetch times out at 8 s; qwen-3.8-auto falls back to plain qwen-3.8
  (also on control-1, so already down). Today the embedder is on control-2, so this is a new coupling. Owner decision O2.
- **R2:** shared VRAM OOM (above).
- **R3:** UPS: the 2070 adds up to its board power when busy; small-model bursts are short. Not visible to the
  UPS shed query. Check UPS load before every bench (memory `ups-overload-llm-shed`).
- **R4:** thermals next to the R9700 (open item from the rollout: 5-min full-power recheck). Stop benches at R9700
  junction >= 97 C or 2070 >= 83 C.
- **R5:** CUDA scores differ from Vulkan scores (fp16 accumulation). Chunk 2 checks parity.
- **R6:** Recreate strategy: switching a Deployment in place has a gap of image pull + load (seconds to a minute).

---

## Execution Plan

### Progress Tracker

#### Wave 1 (sequential) — research inputs

- [x] Chunk 1: Pin image and candidates from upstream (no repo writes except this plan)

#### Wave 2 (sequential) — measure on the card (ask-first live)

- [x] Chunk 2: Throwaway pods on control-1: VRAM, latency, parity for the current three models

#### Wave 3 (parallel) — candidate evals (ask-first live, throwaway pods)

- [x] Chunk 3: Reranker eval (no go: bge stays)
- [x] Chunk 4: Decision-model eval (no go: d3-nano stays)

#### Wave 4 (sequential) — move

- [ ] Chunk 5: Move embedder, reranker (or winner), d3 (nano or winner) to the 2070; DCGM alerts
  (PR: d3-nano and bge moved, `embeddinggemma-2-cuda` side by side; routing switch and Vulkan removal after it is Ready)

#### Wave 5 (sequential) — docs

- [ ] Chunk 6: Evidence docs, memory, plan consolidation (docs done in the PR; memory and plan removal after cutover)

### Wave Conflict Matrix

| Chunk | Write Set | Dependencies | Wave |
|-------|-----------|--------------|------|
| 1 | this plan file | None | 1 |
| 2 | none in repo (bench scripts in /tmp) | 1 | 2 |
| 3 | none in repo | 2 | 3 |
| 4 | none in repo | 2 | 3 |
| 5 | 3 model yamls (+ new reranker/d3 yaml), models `kustomization.yaml`, llmkube HR, memini HR (if reranker), agentgateway `models/embeddinggemma-2.yaml` + `embedding-cache.yaml` (temporary side-by-side), qwen-3.8-auto.yaml + eightball HR (if d3 Service name changes) | 3, 4 | 4 |
| 6 | `docs/llm-hosting/*.md` | 5 | 5 |

---

### Chunk 1: Pin image and candidates

**Wave:** 1. **Complexity:** Low.

1. Re-read `crane digest ghcr.io/ggml-org/llama.cpp:server-cuda`; if it moved past b12407, recheck D2/D3 issue links.
2. Pin GGUF URLs at a commit SHA (`resolve/<sha>/...`, as the existing manifests do) for Qwen3-Reranker-0.6B Q8_0,
   gte-reranker-modernbert-base Q8_0 (community), d3-mini Q4_K_M; read each SHA from the HF API.
3. Check #30422 status; if still open, Chunk 3 measures Qwen3-Reranker latency with it in mind.

**Verification:** every value in this plan's Decisions has a link or command next to it.

### Chunk 2: Measure the current models on the 2070 (ASK FIRST, live)

**Wave:** 2. **Complexity:** Medium.

1. Check UPS load and that radiance is not mid-bench. Throwaway pods in `ai`, `runtimeClassName: nvidia`,
   `nvidia.com/gpu: 1`, CUDA image from Chunk 1, model cache PVC mounted read-only, same args as the live services
   plus `--verbose` (buffer sizes). Start one at a time; record `DCGM_FI_DEV_FB_USED` before/after each.
2. Embedder: worst-case batch (194 x 400 chars, the optimizer startup shape) and one 4105-token input, FA on and off;
   record FB high-water mark and cosine parity vs the live iGPU embedder (>= 0.999 per vector).
3. Reranker: 15 requests of 12 x 1024 chars (method of `reranker-cpu-vs-igpu.md`), FA on/off, score parity.
4. d3-nano: the 23 effort prompts from `qwen-3.8-auto-effort.md`, same answers and probabilities within 0.02.
5. All three loaded together: FB steady and peak.

**Go:** total peak <= 6.5 GiB with all three, parity holds. Else shrink per the budget table and repeat.
**Verification:** table of measured rows replaces the estimates above. Delete the throwaway pods.

### Chunk 3: Reranker eval (ASK FIRST, live, throwaway pods)

**Wave:** 3. **Complexity:** Medium.

1. Same 166 codingagent_v1 queries through memini `cmd/bench` (memini repo, pinned to the live chart's app version),
   pool 4 and pool 12, against bge (2070) and each candidate (2070).
2. Latency: per-call distribution at 10 and 15 docs x 1024 chars, 50 calls each, serial (MEMINI_RERANK_MAX_CONCURRENCY=1).
3. Dutch spot check: 20 Dutch memories/queries from memini, same top-3 or better by hand-label.

**Go:** R@3 >= bge + 1.0 point AND p95 at 15 docs <= 2.5 s (half the 5 s timeout) AND VRAM delta fits Chunk 2's spare.
Else stay on bge.

### Chunk 4: Decision-model eval (ASK FIRST, live, throwaway pods)

**Wave:** 3. **Complexity:** Medium.

1. 8-ball `8ball evaluate` with `dev/bench.toml` (spam, SST-2, Banking77 test split) against d3-nano and d3-mini
   Q4_K_M on the 2070, both `kind = "systemone"`, same specs.
2. Effort task: the callout cache is hashed and inputs are not logged, so use the 23 labelled prompts from
   `qwen-3.8-auto-effort.md` plus 50 new hand-labelled Hermes-style first messages (owner labels).
3. Report agreement with nano, accuracy vs labels, share of `low` >= 0.95 (it gates thinking off), latency p50/p95.

**Go:** correct-of-all >= nano + 2 points on the 8-ball bench, no regression on the 23 effort prompts, p95 <= 1.0 s,
measured all-in VRAM leaves >= 0.75 GiB spare with the other two. Else stay on nano.

### Chunk 5: Move (ASK FIRST for push and reconcile)

**Wave:** 4. **Complexity:** Medium.

1. Per service, in this order: d3 (callout fails open), reranker (memini falls back to fused order), embedder last.
2. Manifest pattern (block YAML):

   ```yaml
   hardware:
     accelerator: cuda
     gpu:
       enabled: true
       count: 1
       vendor: nvidia
   ```

   InferenceService: `image: ghcr.io/ggml-org/llama.cpp:server-cuda@sha256:<Chunk 1>`, `runtimeClassName: nvidia`,
   remove `nodeSelector`, `affinity`, `supplementalGroups`, the Vulkan `parallelSlots` warmup comment (re-test
   `parallelSlots` 1 for the reranker only if Chunk 2 showed no hang), `maxPodLifetimeSeconds` (GTT-leak mitigation,
   Vulkan-only; keep if Chunk 2 saw FB growth), memory request 512Mi / limit 1Gi (RSS <= 0.35 GiB + CUDA host ctx).
3. Embedder zero downtime: Recreate means a gap. Instead, apply a temporary second InferenceService
   `embeddinggemma-2-cuda` (same Model), verify, switch the agentgateway backend + embedding-cache upstream to it,
   then convert `embeddinggemma-2` to CUDA and switch back, delete the temp. Same vector space (same GGUF), parity
   checked in Chunk 2, so no re-embed.
4. Reranker winner: new Model/InferenceService file, memini `MEMINI_RERANK`/`MEMINI_RERANK_MODEL`, then remove bge.
5. llmkube HR: `gpuHealth.enabled: true` (DCGM alerts; check thresholds in the chart values first), update comments.
6. Validate: `bash .agents/skills/pr-review/scripts/validate-pr.sh`, kustomize build, flux-local per common operations.

**Verification:** US-1 criteria; `kubectl -n ai get deploy` 1/1; memini logs no `embed failed`; rerank results ok;
a qwen-3.8-auto request routes; `8ball` decision; 24 h FB peak.

### Chunk 6: Docs

**Wave:** 5. Update `qwen-3.8-auto-effort.md`, `reranker-cpu-vs-igpu.md`, `embedder-cpu-vs-igpu.md` with the
measured tables; update memories; consolidate this plan.

---

## Testing Strategy

- Parity and latency per Chunk 2-4; validate-pr script and kustomize build per chunk with repo writes.
- E2E: live consumer checks in Chunk 5.

## Rollback Plan

1. `git revert` the move commit(s): Vulkan manifests return to the iGPUs (images and GGUFs unchanged in the cache).
2. Reranker: revert memini env to bge; bge manifest restored by the same revert.
3. Decision model: revert to d3-nano file; consumers point at `d3-nano` Service.
4. Embedder: the agentgateway backend fails open to the direct Service; during the side-by-side, switch back to
   `embeddinggemma-2` on the iGPU before deleting anything.

## Owner decisions

- O1: reranker and decision-model go thresholds above (accept or change).
- O2: clean move vs keeping an iGPU embedder standby (adds a LiteLLM/agentgateway fallback group, keeps GTT risk).
- O3: d3-mini at Q4_K_M if Q8_0 does not fit, or stay on nano.
- O4: OK for throwaway bench pods on control-1 (owner-approved per brief, still ask before each run).

---

## Results (2026-11-15)

Throwaway pod `ai/bench-2070` on the card (deleted), `server-cuda@sha256:22bd49f4...` (b12407, re-read with
`crane digest`, unchanged), production flags, VRAM by `nvidia-smi --query-compute-apps`. Full tables:
[nvidia-gpu-control-1.md](../nvidia-gpu-control-1.md#llamacpp-services-on-the-2070),
[reranker-cpu-vs-igpu.md](../llm-hosting/reranker-cpu-vs-igpu.md),
[qwen-3.8-auto-effort.md](../llm-hosting/qwen-3.8-auto-effort.md).

| Service | VRAM MiB (measured) | Plan estimate |
| --- | --- | --- |
| embeddinggemma-2, ubatch 8192, FA on | 1388 | 1690 |
| bge-reranker-v2-m3, FA on (off: 740) | 510 | 1130 |
| d3-nano, 2 x 4096 | 2390 | 3280 |
| All three, concurrent load, peak | 4292 of 7786 (3494 spare) | 6090 |
| d3-mini Q4_K_M, 2 x 4096 (instead of nano) | 3974 | 4350 |
| Qwen3-Reranker-0.6B / gte-modernbert Q8_0 | 1060 / 290 | 1230 / - |

- CUDA context ~110 MiB per process; buffers allocated at load, flat under load.
- Cold start with an empty JIT cache: 59 s to load plus 27 s for the first request (embedder); warm 1.3 s and
  0.08 s. Fix applied: `CUDA_CACHE_PATH` in a per-service subPath (`cuda-cache/<service>`) of the RWX model-cache
  PVC, so only that directory is writable (149 MB for six models in one shared cache during the bench). A test pod
  as uid 101 / fsGroup 102 confirmed the kubelet-created subPath is group-writable.
- Parity vs the live iGPU services: embeddings cosine >= 0.9998 (194 x 400 chars, a 5041-token input, 40 mixed),
  bge scores within 0.11 logit, d3-nano identical on 8-ball (27 wrong of 1119) and the effort prompts.
- Real request sizes (VictoriaLogs, 7 d, `n_tokens`): embedder p99 1228, max 4071; reranker max 552; d3 max 885.
  Nothing needs shrinking: the 3.4 GiB spare is far above the 1 GiB rule, so the embedder keeps ubatch 8192 and
  d3 keeps 2 x 4096.
- Chunk 3 (memini `cmd/bench` v0.7.39, codingagent_v1, pool 4 / 12): bge R@3 88.0 / 88.0, Qwen3-Reranker 87.3 /
  86.7, gte 88.0 / 87.3; R@1 bge 65.1, Qwen3 59.6, gte 60.8. Latency is no constraint (p95 at 15 docs 0.44 /
  0.46 / 0.19 s). No go: the research's code-retrieval lead for Qwen3-Reranker does not show on this set. Dutch
  spot check skipped (no switch).
- Chunk 4: effort prompts 24/24 both, agreement 28/28; 8-ball correct of all nano 88.4%, mini 87.5% (-0.9, CI
  -2.4 to +0.6; mini wrong 12 vs 27 but decides less); p95 0.14 s both. No go (needed +2 points). The 50 new
  owner-labelled prompts were not made; the 24 labelled prompts were recovered from the 2026-11-01 session log.
- Other decision models: clef (27B) and Clef-Flash (9B, Q4_K_M 6.7 GB) do not fit beside the others.
  kanana-2-v-decide-3b-2611 exists (contrary to the earlier search): cosine-similarity head, upstream llama.cpp
  has no path for it, the GGUF's fork branch is not public, fp16 transformers needs ~7.3 GB; not benchmarked.

Decisions:

- O1: thresholds kept; both candidates failed them, so the incumbents stay.
- O2: clean move. The iGPU standby keeps the GTT leak and two copies; control-1 coupling is accepted, memini
  degrades to keyword recall and backfills vectorless writes, rollback is a revert.
- O3: moot (nano stays). Mini Q4_K_M would fit (spare ~1.9 GiB) but brings no gain.
- O4: done (owner-authorised throwaway pod, deleted).
- Embedder cutover: the PR adds `embeddinggemma-2-cuda` (new Model on the same GGUF, shared cache file) beside the
  Vulkan one; the follow-up points embedding-cache and the agentgateway direct provider at it and deletes the
  Vulkan manifest. Final Service name `embeddinggemma-2-cuda` instead of a switch back: one follow-up less, and
  consumers use the model name, which stays `embeddinggemma-2`.
- `maxPodLifetimeSeconds` dropped on the moved services (Vulkan GTT mitigation); `NvidiaVramLow` (< 512 MiB
  free) added; chart `gpuHealth` (DCGM Xid) alerts enabled by dropping the `false` override (chart default). Memory 512Mi request / 2Gi limit (RSS ~1 GiB, 0.3-0.4 GiB
  anonymous).

Open: 24 h checks from US-1 after merge, the embedder follow-up, the R9700 5-minute full-power thermal recheck
(the 2070 drew 165 W under the combined load).

---

## Review Report (self-review, 2026-11-15)

- CRIT-1 [FIXED]: image digest, CUDA version, sm_75 PTX-only build, FA status and candidate GGUFs filled from
  upstream with links (D2, D3, D5, D6). Chunk 1 re-reads them at execution time.
- HIGH-1 [FIXED]: Chunk 4 wave label corrected to Wave 3; Chunks 3 and 4 write nothing in the repo, no overlap.
- HIGH-2 [FIXED]: agentgateway embedder files and models `kustomization.yaml` added to Chunk 5's write set.
- HIGH-3: PTX-only Turing build can add ~1 min JIT per cold start; CUDA_CACHE_PATH on the PVC is the fix if Chunk 2
  confirms it. Unmeasured on this card.
- MED-1: VRAM rows are estimates (CUDA context, d3 KV) until Chunk 2. d3-mini Q4_K_M leaves ~0.75 GiB estimated.
- MED-3: Qwen3-Reranker's third-party general-retrieval gain over bge is small (0.594 vs 0.553); the case rests on code.
- MED-2: R1 failure domain is a real regression for the embedder; owner must accept it (O2).
- LOW-1: d3 effort task has no production input log; eval uses labelled prompts only.

---

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the plan's contents are consolidated into existing docs, remove the plan file; if no relevant docs exist, rework the plan into a reference document.

**Important**: Every prompt should verify the branch and worktree before doing any work.
