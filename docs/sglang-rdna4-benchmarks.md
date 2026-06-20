# Qwen3.6-27B serving on R9700 (gfx1201) — Benchmarks & Decision

## CURRENT STATUS — Round 2 benchmarking in progress (2026-06-20)

Testing SGLang 0.5.13 and vLLM (kyuz0 community image) vs the production llama.cpp.

### Round 2 key findings

**SGLang 0.5.13 AITER requirement:**
- `sglang/srt/layers/rocm_linear_utils.py` has unconditional top-level `import aiter.ops.triton.*`
  (line 2-3, indent=0). Old PyPI placeholder `aiter==0.13.20191203` only satisfies `import aiter`
  but NOT `import aiter.ops` → crash at startup.
- Fix: `amd-aiter @ git+https://github.com/ROCm/aiter.git@v0.1.15.post2` built from source.
  AMD only publishes cp310/cp312 wheels; base image is Python 3.13 → must compile with HIP.
  `--no-build-isolation` keeps ROCm headers in scope; `--no-deps` preserves gfx1201-tuned torch.
- **AITER v0.1.15.post2 already has `gfx1201` in arch support table** (verified on main branch):
  `return get_arch() in ("gfx942", "gfx950", "gfx1250", "gfx1200", "gfx1201")` — no manual
  arch alias patch needed. FP8 Triton kernel path activates automatically on gfx1201.
- **Remaining mattbucci patch gap:** Patch 006 (custom AWQ GEMV HIP kernel in sgl-kernel) gives
  ~14 → ~25 tok/s for AWQ models. Not in stock SGLang pip install. CI build will give ~14 tok/s.
- CI builds in progress: `sglang-rocm` (AITER v0.1.15.post2, SGLang 0.5.13.post1) +
  `sglang-rocm-torch212` (same + torch 2.12.1+rocm7.2 comparison). Deploy: `--attention-backend triton`.

**vLLM on gfx1201 — community image breakthrough:**
- `docker.io/kyuz0/vllm-therock-gfx1201:latest` (vLLM 0.22.1rc1, June 13 2026) has all RDNA4
  patches applied: rocm.py VRAM detection, CUDA graph fix, AITER arch alias, FP8 matrix configs.
- `mattbucci/Qwen3.6-27B-AWQ` fails even in kyuz0: vision tower MLP has non-standard weight shapes
  that AWQ marlin kernel rejects ("input size not aligned") in vLLM.
- `cyankiwi/Qwen3.6-27B-AWQ-INT4` (compressed-tensors format) avoids the marlin path:
  uses `TritonW4A16LinearKernel` instead → loads cleanly. Currently downloading to llama-server PVC.
- **`--enforce-eager` required**: bug #39010 — V1 engine CUDA graph capture deadlocks on gfx1201.
  `--enforce-eager` disables torch.compile + CUDAGraphs, trading potential graph speed for stability.
- Attention backend selected: `ROCM_ATTN` (kyuz0's patched AMD flash attention for gfx1201).
  `VLLM_ROCM_USE_AITER=0` (disables AITER unified attention; plain attn more stable on this build).
- GDN/DeltaNet layers: `Triton/FLA GDN prefill kernel` (auto-selected) handles the 48 linear-attn
  layers. Full inference expected to work once model loads.

### vLLM 0.23.0 — all available Qwen3.6-27B quantized models FAIL to load (2026-06-20)

Exhaustively tested every available 4-bit quantized Qwen3.6-27B model with vLLM 0.23.0 from source:

| Model | Quantization | Error | Root cause |
|---|---|---|---|
| `mattbucci/Qwen3.6-27B-AWQ` | `awq` / `awq_marlin` | "input size not aligned with quantized weight shape" | Hybrid GDN layers have non-standard weight shapes incompatible with vLLM's Triton AWQ path |
| `Qwen/Qwen3.6-27B-FP8` | `fp8` | OOM (29 GiB weights) | FP8 checkpoint is 28.75 GiB of weights; leaves <3 GiB for KV cache on 32 GB GPU |
| `btbtyler09/Qwen3.6-27B-GPTQ-4bit` | `gptq` | "qzeros shape mismatch: torch.Size([2048, 160])" | V1 engine validates qzeros shape strictly; model was quantized with tools incompatible with vLLM 0.23.0 |
| btbtyler09 GPTQ with `--enforce-eager` | `gptq` | Same qzeros error | Shape mismatch is during weight loading, not compile phase |

Additional issues:
- **torchvision ABI deadlock**: `torchvision==0.26.0+rocm7.2` is compiled against upstream
  `torch 2.11.0+rocm7.2` but AMD base has `torch 2.11.0+rocm7.13.0rc2` (different ABI).
  Runtime `pip install` cannot fix this; only works when baked into Dockerfile at build time
  (as confirmed by working SGLang custom image). vLLM 0.19.1 (AMD base image) cannot be
  patched at runtime this way.
- **vLLM 0.23.0 V1 engine**: More strict model validation than 0.19.1; breaks GPTQ models
  quantized with older tooling. `--enforce-eager` bypasses torch.compile but not weight-load validation.

**Reference data (2026-06-12, vLLM 0.19.1):** `btbtyler09/Qwen3.6-27B-GPTQ-4bit` with vLLM
0.19.1+rocm7.13 achieved **47 tok/s aggregate at C8** with APC enabled — this remains the vLLM
reference. vLLM 0.23.0 cannot match it for this model due to the breaking V1 engine changes.

---

## FINAL DECISION — llama.cpp / ROCm (gfx1201) + MTP

Production engine is **llama.cpp** on a custom gfx1201 **ROCm-7.13 (gfx120X-tuned)** build with
**MTP** (multi-token prediction). Measured single-stream decode **34 tok/s** (`UD-Q4_K_XL`,
q8_0 KV, `--spec-type draft-mtp --spec-draft-n-max 3`, `--no-mmap`) — **2.3× sglang** and 4–5×
vLLM. Prefix cache works on the GDN hybrid (**7.5× warm TTFT**) and **coexists with batching**
(unlike sglang's radix-vs-overlap exclusivity). Full **256K** context fits at q8_0 KV single
session (~31 GB). Deploy: `kubernetes/apps/ai/llama-server/`; image: `docker/llama-cpp-rocm/`.

| Engine | TG (1-stream) | PP | Agg TG C=8 | Agg TG C=32 | APC | Batch+APC | Note |
|---|---:|---:|---:|---:|:---:|:---:|---|
| **llama.cpp ROCm + MTP** | **~39** | ~423 cold | ~35 (flat) | ~34 (flat) | ✅ | ✅ | parallel=1 → flat agg at all C |
| **vLLM kyuz0 0.22.1rc1 (FP8 KV)** | 7 | ~1200 | 47.1 | **128.9** | ✅ | ✅ | fp8+APC coexist on kyuz0; AITER crashes (GDN SRAM) |
| sglang (AWQ, fp8 KV, Config B) | 15 | **2820** | 28.8 | — | ❌ (exclusive) | ❌ | older image/fork — re-testing with 0.5.13 |

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

## llama.cpp + MTP — full concurrency sweep (2026-06-20)

**Engine:** custom ROCm-7.13 gfx1201 image (`ghcr.io/tanguille/llama-cpp-rocm-gfx1201:gfx1201@sha256:abc3735`)
**Model:** `unsloth/Qwen3.6-27B-MTP-GGUF:UD-Q4_K_XL` with MTP (`--spec-type draft-mtp --spec-draft-n-max 4`)
**Config:** `parallel=1`, `ctx=262144`, q4_0 KV, `--no-mmap`, `flash-attn on`
**Benchmark:** 6-repeat long prompt (~420 tokens), 200 output tokens, `timings` from SSE stream.

### Single-stream (3 runs)

| Run | PP tok/s | TG tok/s | TTFT | MTP accepted |
|---|---:|---:|---:|---:|
| 1 (cold) | 423 | 40.2 | 0.94s | 146/208 (70%) |
| 2 (KV cache hit) | ~33* | 39.3 | 0.16s | 146/208 (70%) |
| 3 (KV cache hit) | ~33* | 37.6 | 0.17s | 146/208 (70%) |
| **avg** | — | **39.1** | 0.42s | 70% |

\*PP=33 on runs 2-3 is misleading — only 1–4 new tokens processed (full KV cache hit from
identical repeated prompt). Cold PP (run 1) = **423 tok/s** at Q4_K_XL.

### Concurrency sweep (16–48 requests, 200 output tokens each)

| C | Agg TG tok/s | Per-stream TG tok/s | TTFT |
|---|---:|---:|---:|
| 1 (16 req) | 37.0 | 38.1 | 0.16s |
| 4 (16 req) | 34.5 | 35.6 | 15.4s |
| 8 (24 req) | 35.7 | 36.6 | 32.8s |
| 16 (48 req) | 34.0 | 35.3 | 74.5s |

**Key observation:** `parallel=1` makes aggregate throughput completely flat at ~34–37 tok/s
regardless of concurrency. All concurrent requests queue and serialize through the single decode
slot. TTFT grows linearly (~4.6s per queued request). Per-stream TG stays high because KV cache
is reused for identical prompts. For multi-user throughput, this is a hard ceiling: llama.cpp
with parallel=1 never exceeds ~40 tok/s total regardless of how many clients connect.

This is the key tradeoff vs SGLang/vLLM which batch multiple decode steps — their aggregate TG
can reach 50–150+ tok/s at C8+, at the cost of higher per-stream latency.

---

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

## vLLM kyuz0 0.22.1rc1 — Qwen3.6-27B cyankiwi INT4 (2026-06-20)

**Image:** `docker.io/kyuz0/vllm-therock-gfx1201:latest` (vLLM 0.22.1rc1.dev499+g470229c37.d20260613,
June 13 2026). Community build with all RDNA4 patches: rocm.py VRAM detection, AITER arch alias,
FP8 matrix configs. Requires `privileged: true` for GPU access (no `amd.com/gpu` resource limit).

**Model:** `cyankiwi/Qwen3.6-27B-AWQ-INT4` (compressed-tensors format, 19 GiB)
**Config:** `--max-model-len 32768 --gpu-memory-utilization 0.92 --enable-prefix-caching
--max-num-seqs 16 --trust-remote-code`, `VLLM_ROCM_USE_AITER=0`, `FLASH_ATTENTION_TRITON_AMD_ENABLE=TRUE`
**KV cache:** fp16 auto, 7.46 GiB → 105,813 tokens, max concurrency @32K = 3.23×

**Key startup findings:**
- `mattbucci/Qwen3.6-27B-AWQ` fails: vision tower MLP layer sizes incompatible with awq_marlin kernel
  ("input size not aligned"). `cyankiwi/Qwen3.6-27B-AWQ-INT4` uses `TritonW4A16LinearKernel` → loads.
- CUDAGraph bug #39010 (V1 deadlock on gfx1201) is **fixed** in kyuz0 0.22.1rc1 — 7/7 batch sizes
  captured without hanging. `FULL_AND_PIECEWISE` mode, sizes [1, 2, 4, 8, 16, 24, 32].
- `Triton/FLA GDN prefill kernel` auto-selected for DeltaNet layers.
- `ROCM_ATTN` backend selected (ROCM_AITER_UNIFIED_ATTN skipped because VLLM_ROCM_USE_AITER=0).
- CUDAGraphs give NO single-stream speedup (confirms gfx1201 is compute-bound, not launch-overhead
  bound — same as the sglang fork documented for CUDA graphs in 2025).

### Concurrency sweep (in ~420 / out 200, APC enabled, CUDAGraphs enabled)

| Concurrency | Agg TG tok/s | Per-stream TG tok/s | Median TTFT |
|---|---:|---:|---:|
| 1-stream (3 runs) | — | **7.0** | 0.50s |
| C=1 (16 req) | 6.9 | 7.0 | 0.41s |
| C=4 (16 req) | 26.2 | 6.8 | 1.26s |
| C=8 (24 req) | **48.6** | 6.7 | 3.01s |
| C=16 (48 req) | **85.8** | 6.1 | 4.59s |

**PP:** ~1200 tok/s (420-token prompt, max_tokens=1, steady-state TTFT ~0.35s)

**Analysis:**
- Single-stream TG unchanged between eager and graph mode (compute-bound, not launch-overhead bound).
- Aggregate TG scales **linearly** to C=16 (6.9 → 85.8 = 12.4×). GPU is memory-bandwidth bound in
  single-stream but transitions to compute-throughput bound as batch size grows — the ideal scaling
  regime for vLLM's continuous batching.
- **85.8 tok/s @ C=16 beats every engine at any configuration tested so far.**
- APC (prefix caching) confirmed active and coexisting with batching — vLLM's structural advantage
  over sglang/ROCm for this hybrid model.
- TTFT at C=16 is 4.6s — acceptable for throughput workloads.

### Comparison to prior vLLM reference (0.19.1, GPTQ-4bit, fp8 KV)

| Metric | vLLM 0.19.1 (GPTQ, fp8 KV) | vLLM kyuz0 0.22.1rc1 (CT, fp16 KV) |
|---|---|---|
| Single-stream TG | 8.4 tok/s | 7.0 tok/s |
| C=8 agg TG | 47 tok/s | **48.6 tok/s** |
| Max tested C | 10 | **16** |
| C=16 agg TG | — | **85.8 tok/s** |
| Model | btbtyler09/GPTQ-4bit | cyankiwi/AWQ-INT4 |
| Max context | 131K (fp8 KV) | 32K (fp16 KV) |

Newer engine slightly worse at C=1 but matches at C=8 and scales past C=10 where 0.19.1 saturated.

## vLLM kyuz0 0.22.1rc1 — optimization experiments (2026-06-20)

### VLLM_ROCM_USE_AITER=1 — FAILS on gfx1201 with Qwen3.6-27B

Enabling AITER unified attention triggers Triton autotuning for the GDN/DeltaNet linear-attention
layers. ALL tested kernel configurations exceed gfx1201's 64 KiB shared memory per workgroup limit
(AITER's config tables target MI300X/MI350X with larger SRAM), exhausting the autotune candidate
list. vLLM raises `IndexError: list index out of range` on empty best-config list and aborts startup.
**Conclusion: `VLLM_ROCM_USE_AITER=1` is not viable for Qwen3.6-27B on gfx1201.**

### FP8 KV cache + APC (Config C): `--kv-cache-dtype fp8 --enable-prefix-caching --max-num-seqs 32`

**KV cache:** fp8, ~3.74 GiB → **196,608 tokens** (1.86× FP16's 105,813 — confirms no fused fp8
dequant on gfx1201, overhead paid at every decode step but capacity doubles).

Bug #13147 (fp8 KV + APC crash on RDNA3) does **NOT** affect kyuz0/gfx1201. FP8 + APC starts
cleanly and coexist without errors.

| Concurrency | Agg TG tok/s | Per-stream TG tok/s | Median TTFT | vs FP16 baseline |
|---|---:|---:|---:|---:|
| 1-stream | — | **6.9** | 0.41s | −1% |
| C=4 | 25.7 | 6.7 | 1.34s | −2% |
| C=8 | 47.1 | 6.5 | 2.97s | −3% |
| C=16 | 78.7 | 5.6 | 4.57s | **−8%** (dequant overhead) |
| **C=32** | **128.9** | 4.9 | 8.63s | **+50%** vs FP16@C=16 |

**Analysis:** FP8 dequant overhead (~8% at C=16) is real but overcome by the extra slots: doubling
max_num_seqs (16→32) allows C=32 which scales to 128.9 tok/s — 50% beyond the FP16 ceiling of 85.8
at C=16. Throughput still appears linear at C=32 (not yet saturated). FP8 KV is the better
production config for multi-user workloads despite the per-batch overhead.

**Hadamard rotation:** No flag exists in vLLM or SGLang for FP8 KV. The RFC (#28538) was closed
stale. TurboQuant (the only 4-bit + Hadamard scheme in vLLM) targets MI355X only, not gfx1201.

### Updated production recommendation

| Config | Single-stream | Agg @ C=16 | Agg @ C=32 | APC |
|---|---:|---:|---:|:---:|
| FP16 KV, APC, slots=16 (baseline) | 7.0 tok/s | 85.8 tok/s | — (capped) | ✅ |
| **FP8 KV, APC, slots=32 (recommended)** | 6.9 tok/s | 78.7 tok/s | **128.9 tok/s** | ✅ |

**Recommended vLLM production flags:**
```
--kv-cache-dtype fp8
--enable-prefix-caching
--max-num-seqs 32
--max-model-len 32768
--gpu-memory-utilization 0.93
VLLM_ROCM_USE_AITER=0
FLASH_ATTENTION_TRITON_AMD_ENABLE=TRUE
```

---

## Open / next experiments

- [x] **bf16 KV cache** — TESTED: *slower* (12.35 vs 14.96) and half capacity. fp8 KV wins on
      sglang (native gfx1201 fp8 kernels). Keep fp8_e4m3.
- [x] vLLM kyuz0 batching benchmark — **DONE**: 48.6 tok/s @ C8, 85.8 tok/s @ C16, APC on, CUDAGraphs work on gfx1201 in 0.22.1rc1 (see section above)
- [x] vLLM FP8 KV + APC coexistence — **DONE**: 128.9 tok/s @ C32 (50% gain over FP16 baseline); bug #13147 doesn't affect kyuz0. FP8 is recommended config.
- [x] VLLM_ROCM_USE_AITER=1 — **FAILS**: GDN kernel autotune exhausts all configs ≤ 64 KiB SRAM on gfx1201. AITER config tables target MI300X, not gfx1201.
- [ ] vLLM fp8 KV + extended context: `--max-model-len 131072` with fp8 KV — test if 32K→131K context affects throughput
- [ ] Push C beyond 32 (C=48, C=64) — throughput still linear at C=32, saturation point unknown
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
