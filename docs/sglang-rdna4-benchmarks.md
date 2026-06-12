# Qwen3.6-27B serving on R9700 (gfx1201) — Benchmarks & Decision

## FINAL DECISION — llama.cpp / ROCm (gfx1201) + MTP

Production engine is **llama.cpp** on a custom gfx1201 **ROCm-7.13 (gfx120X-tuned)** build with
**MTP** (multi-token prediction). Measured single-stream decode **34 tok/s** (`UD-Q4_K_XL`,
q8_0 KV, `--spec-type draft-mtp --spec-draft-n-max 3`, `--no-mmap`) — **2.3× sglang** and 4–5×
vLLM. Prefix cache works on the GDN hybrid (**7.5× warm TTFT**) and **coexists with batching**
(unlike sglang's radix-vs-overlap exclusivity). Full **256K** context fits at q8_0 KV single
session (~31 GB). Deploy: `kubernetes/apps/ai/llama-server/`; image: `docker/llama-cpp-rocm/`.

| Engine | Decode (1-stream) | PP @2048 | Prefix cache | Batch+cache | Note |
|---|---:|---:|---|:---:|---|
| **llama.cpp ROCm + MTP** | **34** | ~1000 | ✅ 7.5× | ✅ | the pick (upstream's ROCm-7.2.1 image = only ~18) |
| sglang (AWQ, fp8 KV) | 15 | **2820** | ✅ 3.5× | ❌ exclusive | faster PP, but radix XOR batch |
| vLLM (4-bit, fp8 KV) | 6–8 | ~1130 | ✅ 6.3× | ✅ | batches to 47@C8 but slow 1-stream |

Retired trial configs (revival anchors) are in
[`ai-serving-trials-archive.md`](./ai-serving-trials-archive.md). The rest of this file is the
**historical sglang/vLLM measurement log** that led to the decision.

---

Performance log of every config benchmarked for the custom `sglang-rdna4` image. Companion to
`vllm-qwen3.6-rocm-benchmarks.md` (vLLM reference). Unless noted, runs use
`sglang.bench_serving --dataset-name random`, `temperature` default, on a single GPU.

> Status: measured in a debug pod, not estimated.

## Fixed environment (identical across all runs unless stated)

| Item | Value |
|---|---|
| GPU | AMD Radeon AI PRO R9700, 32 GB, gfx1201 (RDNA4), **single GPU (TP=1)** |
| Image | `ghcr.io/tanguille/sglang-rdna4:v0.5.12-gfx1201` (mattbucci fork), SGLang v0.5.12 |
| Torch | **2.12.0+rocm7.2** (see allocator fix below) |
| Model | `mattbucci/Qwen3.6-27B-AWQ-native-thinking-vision` (native int4 AWQ, gemm) |
| Architecture | DeltaNet+attention **hybrid**, 64 layers (16 full-attn + 48 GDN linear-attn) |
| KV dtype | `fp8_e4m3` (default; see Open experiments — bf16 KV likely faster on RDNA4) |
| Context | 131072 |
| Attention backend | triton (validated gfx1201 path) |
| CUDA graph | **disabled** (no decode speedup on gfx1201 — fork-verified, compute-bound) |

## CRITICAL FIX — torch 2.12 allocator (required to boot at all)

torch 2.12's **`expandable_segments` HIP allocator faults on RDNA4** — a HIP illegal-memory-
access (`hipErrorIllegalAddress`) at the first GPU op of weight init, crashing the scheduler
(exit -6) before any weights load. The image's `setup_rdna4_env` sets
`PYTORCH_HIP_ALLOC_CONF=expandable_segments:True` (a no-op on torch 2.11, which never
implemented it for ROCm; newly *implemented* and *broken* on 2.12 ROCm).

**Fix (no rebuild):** `PYTORCH_HIP_ALLOC_CONF=""`. Changes memory management only, not compute
(AWQ GEMM is bit-identical across torch 2.11/2.12 per the fork's own root-cause commits).
Output verified coherent. **Do not need to downgrade to torch 2.11** — decode matches the
fork's published 2.11 numbers (see below).

## Single-stream baseline (concurrency = 1, in 2048 / out 256)

| Metric | Value | Reference (fork, 2× R9700 TP=2, torch 2.11) |
|---|---|---|
| Decode TG | **14.96 tok/s** (TPOT 63 ms) | 14.5 tok/s (TPOT 69 ms) |
| Prefill PP | ~2820 tok/s (TTFT 725 ms @2048) | ~8000 tok/s @245K (TP=2) |
| Large-ctx PP (cache-miss) | ~3200 tok/s @ ~66K (TTFT 20.5 s) | — |

We **match the reference decode on a single GPU vs their two.** The ~15 tok/s ceiling is a
known fork "open regression": decode is GPU-compute-bound on the int4 `awq_gemv_bf16_kernel`
(~78% of decode time, runs ~5× under the memory roofline) and scales with layer count (all
64-layer models cluster ~14-15; 40-layer models hit ~24). Not fixable by KV/graph settings.

## Batching — THE hard tradeoff on ROCm

On RDNA4 + sglang v0.5.12, for this mamba/hybrid model, **RadixAttention (prefix cache) and
overlap scheduling (batching) are mutually exclusive by design.** The `extra_buffer` mamba
strategy that allows both asserts `is_cuda() or is_musa() or is_npu()` — ROCm is excluded
(`server_args.py:2520`). With radix cache on, sglang force-sets `disable_overlap_schedule=True`
(`server_args.py:2545`). So you pick one:

### Config A — radix cache ON (`no_buffer`, overlap OFF) — **the reference author's config**
in 2048 / out 256:

| Concurrency | Aggregate TG | Median TTFT | Median E2E |
|---|---|---|---|
| 1 | 14.96 tok/s | 0.73 s | 11.5 s |
| 4 | 9.00 tok/s | 33.8 s | 64.5 s |
| 8 | 9.12 tok/s | 80.5 s | 104.5 s |

Batching does **not** scale (overlap off → concurrent prefills serialize, TTFT explodes).

**Prefix-cache effectiveness (agentic reuse — shared ~7.5K-token prefix across tool iterations):**

| Iteration | Prompt tokens | Cached | TTFT |
|---|---|---|---|
| 1 (cold) | 7,511 | 0 | 6.62 s |
| 2 (reuse) | 7,522 | 7,511 (99.9%) | **2.13 s** |
| 3 (reuse) | 7,533 | 7,522 | **1.86 s** |

→ **3.5× TTFT speedup on reused context.** This is the agentic tool-calling win.

The fork author runs exactly this (no `--disable-radix-cache` anywhere in `launch.sh`;
`OVERLAP=""` for qwen36-27b). That is *why* all their published benchmarks are conc=1.

### Config B — `--disable-radix-cache` (overlap ON, no prefix cache)
in 2048 / out 256:

| Concurrency | Aggregate TG | Median TTFT | Median E2E |
|---|---|---|---|
| 1 | 14.93 tok/s | 0.72 s | 11.5 s |
| 4 | 16.67 tok/s | 1.26 s | 33.7 s |
| 8 | **28.81 tok/s** (peak 40) | 1.22 s | 32.3 s |

Batching **scales** (≈2× at C8, sweep ran 3.2× faster, TTFT stays ~1.2 s). But **every request
re-prefills the full prompt** — no prefix reuse (the 7.5K-token agentic prefix costs ~6.6 s TTFT
*every* call instead of ~1.9 s cached).

## KV dtype: fp8_e4m3 vs bf16 (single-stream, in 2048 / out 256, radix ON)

| KV dtype | Decode TG | TPOT | KV pool (tokens) | Fits 131K single req |
|---|---|---|---|---|
| **fp8_e4m3** | **14.96 tok/s** | 63 ms | 212,220 | yes |
| bf16 | 12.35 tok/s | 76 ms | 105,854 | no (req capped ~105K) |

**fp8 KV wins on sglang — faster AND 2× capacity.** This is the *opposite* of vLLM on the same
GPU (where fp8 KV halved decode), because the sglang fork ships **native gfx1201 fp8-KV kernels**
(patches 039/042/044) — fused, no dequant overhead — whereas vLLM falls back to an unfused Triton
dequant. Conclusion: **keep `--kv-cache-dtype fp8_e4m3`** (the fork default). bf16 KV is both
slower and lower-capacity here. (Closes the memo's "Step 5 A/B".)

## vLLM cross-reference (same model, same GPU — from `vllm-qwen3.6-rocm-benchmarks.md`)

| Engine | Quant | KV | Decode TG | PP@2K | PP@8K | Caching |
|---|---|---|---|---|---|---|
| vLLM-ROCm | GPTQ-4bit | fp16 | 11.0 | ~1100 | 1706 | APC ✅ |
| vLLM-ROCm | GPTQ-4bit | fp8 | 5.9 | 1133 | 1351 | APC ✅ |
| **sglang (A/B)** | AWQ-4bit | fp8 | **14.96** | ~2820 | — | RadixAttn ✅ (A only) |

- **sglang AWQ beats vLLM on single-stream decode** (14.96 vs 11.0 fp16 / 5.9 fp8) and prefill.
- vLLM proved **fp8 KV is slow on RDNA4** (11.0 fp16 → 5.9 fp8; no fused fp8-dequant kernel,
  Triton fallback dequants every step). **We run fp8 KV → bf16 KV likely faster (untested here).**
- vLLM's APC (prefix caching) + continuous batching are *not* mutually exclusive — a structural
  edge for vLLM if both-at-once is required.

## vLLM MEASURED on this rig — batching + caching together (fp8 KV, APC on)

Live test 2026‑06‑12 (debug pod `ai/vllm-dbg`, nothing committed). vLLM `0.19.1+rocm7.13`,
model `btbtyler09/Qwen3.6-27B-GPTQ-4bit`, `--quantization gptq --dtype float16
--kv-cache-dtype fp8 --max-model-len 131072 --enable-prefix-caching --max-num-seqs 16
--gpu-memory-utilization 0.94`. Bench = `sglang.bench_serving --backend vllm`, identical
methodology to the sglang tables above. **fp8 KV was *required* to boot at 131K: with fp16 KV the
GPTQ weights + vision tower (19.2 GiB) leave only ~7 GiB → can't fit even one 131K request.**
KV pool 8.33 GiB, **max concurrency @131K = 1.93×** (only the 16 full-attn layers cache KV; the 48
GDN layers use recurrent state, so KV/token is small and the pool stretches far).

### Batching sweep (random, in 2048 / out 256)

| Concurrency | Reqs | Aggregate TG | Median TTFT | TPOT | E2E |
|---|---|---|---|---|---|
| 1 | 8 | 8.43 tok/s | 1.43 s | 114 ms | 30.4 s |
| 4 | 16 | 29.21 tok/s | 2.07 s | 125 ms | 33.6 s |
| 8 | 32 | **47.04 tok/s** | 3.06 s | 148 ms | 39.4 s |
| 10 | 40 | 46.74 tok/s | 3.25 s | 199 ms | 54.4 s |

Batching **scales 5.6×** (8.4 → 47 tok/s @ C8) then saturates at C10 (TPOT 114→199 ms =
compute-bound). **47 @ C8 beats sglang's best-ever 28.8 (Config B) — and vLLM keeps prefix caching
ON while doing it** (sglang must disable radix to batch). TTFT stays 1.4–3.3 s under load vs sglang
Config A's 80 s collapse.

### Prefix cache effectiveness (controlled APC probe, ~6K-token shared prefix, max_tokens=1)

| Request | Prefill wall-time |
|---|---|
| cold (first sight) | 5.81 s |
| warm (APC hit) | **0.92 s** |
| warm again | 0.92 s |

→ **6.3× TTFT speedup on reused context** (vs sglang Config A's 3.5×) — *and it coexists with
batching.* A `generated-shared-prefix` run at C8 completed 8 reqs in 55.6 s at 36.8 tok/s with APC
on (cold≈warm because intra-run reuse warms the cache immediately), confirming **batching + prefix
caching run together without collapse** — the combination sglang/ROCm cannot do for this hybrid.

### The vLLM tradeoff vs sglang
vLLM uniquely satisfies **all four** requirements at once (batch-to-10 + 131K context + fp8 KV
compression + working prefix cache). Its **only** weakness is **single-stream decode (~6–8 tok/s)**
— vLLM's fp8-KV path is unfused (Triton dequant every step), where the sglang fork's native fused
gfx1201 fp8 kernels hit 15. So: **sglang = fast for 1 user (15 tok/s) but batching XOR caching;
vLLM = both-at-once + scales to 47 tok/s aggregate, but slow per-stream.** Pick by whether the
workload is latency-bound (few users → sglang) or throughput/agentic-reuse-bound (vLLM).

## Recommendation by production load

| Load profile | Best config | Why |
|---|---|---|
| **Single-user agentic / tool-calling** (low concurrency, big reused prefix) | **Config A (radix ON)** | 3.5× TTFT on reused context; batching irrelevant at conc=1. Matches the reference author. |
| **Many concurrent sessions** (high concurrency, low reuse) | **Config B (disable-radix)** | 28.8 tok/s aggregate, TTFT ~1.2 s under load. |
| **Both needed at once** (batch + cache) | **vLLM (fp8 KV, APC)** — CONFIRMED | 47 tok/s @ C8 *with* APC (6.3× cached TTFT); the only engine doing both on ROCm. Cost: slow single-stream (~8 tok/s). |
| **Raw single-user speed** | **sglang Config A** (15 tok/s) | 2× vLLM's per-stream decode via fused fp8 kernels; but batching XOR caching. |

torch: **stay on 2.12 + `PYTORCH_HIP_ALLOC_CONF=""`** in all cases (no rebuild; matches 2.11 perf).

## llama.cpp on gfx1201 — measured baseline + tuning research (2026-06-12)

**Engine:** lemonade-sdk `llamacpp-rocm` **b1292** (latest; ROCm 7.13 nightly), build `a66d505`.
**Model:** `Qwen3.6-27B-Q4_K_M.gguf` (16.8 GB). Card confirmed `gfx1201`, Wave Size 32.

### Measured baseline (`llama-bench`, FA on, q4_0 KV, full offload `-ngl 99`)

| test | flags | tok/s |
|---|---|---|
| **pp512** (prefill) | `-fa 1 -ctk q4_0 -ctv q4_0 -mg 0 -sm none` | **953.58 ± 106.93** |
| **tg128** (decode) | `-fa 1 -ctk q4_0 -ctv q4_0 -mg 0 -sm none` | **24.83 ± 0.24** |

This **exactly matches** truelies444's independent single-R9700 number (24.85 tg128, Q5_K_M) →
baseline is correctly measured, not anomalously low. Fastest single-stream decode of anything
tested here (sglang 15, vLLM fp8 ~8). **Caching/batching probe still pending** (cluster was mid
k8s-upgrade when this was captured).

### Version verdict (research, GitHub primary sources)

- **b1292 is the latest** lemonade build and already contains **every** GDN speed PR — fused
  `GATED_DELTA_NET` op (#19504, Mar 2026), chunked path (#20340), AR improvements (#20391).
  Building from `ggml-org/master` HEAD gives **nothing new** for this model/GPU.
- **The fused GDN kernel is a no-op on AMD**: it's a CUDA kernel cross-compiled via HIP; on RDNA it
  performs **identically to the CPU path** (#20354 — register spilling, NVIDIA-tuned warp/cache).
  **No RDNA-native GDN kernel exists upstream.** HIP also pays ~271 µs/dispatch on the per-token
  recurrent step (#20292) — the main PP penalty for this hybrid.

### Tuning levers to test (sweep, not re-baseline) — cheapest/highest-confidence first

Same-model gfx1201 datapoints reach **42–72 tok/s** on ROCm (yiwiz-sai 42.78 Q8_0+q4_0KV;
zedbytes 58–72 `-sm tensor`), so there is likely real headroom above 24.8.

| Lever | Claimed gain | Confidence | Notes |
|---|---|---|---|
| `-b 4096 -ub 2048` (we run defaults) | +15–29% prefill | High | runtime flag, safe |
| `rocm-smi --setperflevel high` + PCIe ASPM `performance` | +10.8% decode | High | **node-level** — needs GPU sysfs/privileged access from pod |
| `ROCBLAS_USE_HIPBLASLT=1` | unknown, 0 downside | Low | runtime env |
| **iGPU check** → if present, `ROCR_VISIBLE_DEVICES=0`, drop `-mg 0 -sm none`, try `-sm` variants | +60–190% (→40–72) | **Skeptical** — `-sm tensor` on a single GPU should be a no-op; verify topology first | the whole `-sm none` workaround only exists to dodge an iGPU segfault (lemonade #96) |
| **Vulkan backend** (RADV) instead of HIP | mixed (Agent1 big; Agent2 ROCm wins decode on 27B) | Medium | needs a Vulkan binary (lemonade ships ROCm-only) |
| `-DCMAKE_HIP_FLAGS="-mllvm --amdgpu-unroll-threshold-local=600"` | up to 3× **prefill** if regression active | Medium | compile-time only; unknown if b1292 already has it |
| `rocWMMA` flash-attn (`-DGGML_HIP_ROCWMMA_FATTN=ON`) | **negative** on gfx12 | High | **do NOT enable** (#15021, #13110) |

**Planned re-test order (post-upgrade):** (1) `rocminfo`/`rocm-smi` topology + iGPU check →
(2) baseline confirm → (3) `-b/-ub` → (4) `ROCBLAS_USE_HIPBLASLT=1` → (5) perf-level high →
(6) iGPU-hide + `-sm` variants if applicable → (7) cache probe + small-concurrency sweep on winner.

## Open / next experiments

- [x] **bf16 KV cache** — TESTED: *slower* (12.35 vs 14.96) and half capacity. fp8 KV wins on
      sglang (native gfx1201 fp8 kernels). Keep fp8_e4m3.
- [ ] vLLM batching benchmark (same model) — does APC + continuous batching give *both* on ROCm?
- [ ] `mamba_track_interval` / scheduler tuning under Config B for higher concurrency.
- [ ] Quality probe (logprob/eval) to fully close the fork's flagged torch-2.12 attention drift
      (basic coherence already passes).
- [ ] **llama.cpp gfx1201 tuning sweep** — `-b/-ub`, hipBLASLt env, perf-level, iGPU/`-sm`,
      Vulkan backend (see "llama.cpp on gfx1201" section). Then cache probe + concurrency sweep.

## Process Instructions

- After completing each step, update the relevant table with the current status.
- Pause for user confirmation before proceeding to next step.
- Keep this as a living reference; record the winning config + rationale once chosen and
  committed to the HelmRelease.
