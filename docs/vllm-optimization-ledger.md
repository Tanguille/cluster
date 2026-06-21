# vLLM Optimization Ledger — Qwen3.6-27B on R9700 (gfx1201)

## Hardware & Model (Fixed)
- **GPU**: AMD Radeon AI PRO R9700, 34.2 GiB GDDR6, gfx1201 (RDNA4), single GPU
- **Model**: `cyankiwi/Qwen3.6-27B-AWQ-INT4` (Qwen3_5ForConditionalGeneration, compressed-tensors AWQ INT4)
  - Architecture: 64 layers (16 full attention + 48 DeltaNet/GDN recurrent), 4 MTP heads
  - **Native context**: 262,144 tokens (max_position_embeddings in language_config)
  - KV only for 16 attention layers: 32 KB/token (8 KV heads × 128 dim × 2 × fp8)
  - Vision encoder present (depth=27 ViT, ~700 MB): avoid loading for text-only use
  - Available VRAM for KV (~7.9 GiB): ~197K tokens with max_num_seqs=16

## Benchmark Harness (Fixed — use every iteration)
- **Tool**: inline Python via `kubectl exec` → OpenAI `/v1/completions`
- **Prompt**: 420-token transformer explanation (fixed repeated prompt)
- **Output**: 200 tokens, temperature=0
- **Sweep**: C=1 (3 runs, report avg), C=4 (16 req), C=8 (24 req)
- **Metrics**: PP tok/s (from TTFT), TG tok/s (per-stream + aggregate), TTFT

## Quality Gate
- **Method**: 3-prompt deterministic check (temperature=0, exact format match)
- **Threshold**: All 3 outputs must be coherent English paragraphs (format gate, not exact match)
- **Baseline**: cyankiwi AWQ-INT4 with FP8 KV + MTP (current best, quality = baseline)

## Final Production Config (Converged)
**Engine**: vLLM kyuz0 0.22.1rc1 | **Image**: `docker.io/kyuz0/vllm-therock-gfx1201:latest`
**Model**: `cyankiwi/Qwen3.6-27B-AWQ-INT4` | **Native ctx**: 262,144 | **Achievable ctx**: **234,320** (89%)
- C=1 TG: **19.5 tok/s** | C=8 agg: **126.1 tok/s** | KV tokens: 234,320 | VRAM: 30.1 GiB

```
--max-model-len 234320
--max-num-seqs 8
--gpu-memory-utilization 0.95
--kv-cache-dtype fp8
--enable-prefix-caching
--spec-method mtp
--spec-tokens 4
--limit-mm-per-prompt '{"image": 0}'
--trust-remote-code
VLLM_ROCM_USE_AITER=0
FLASH_ATTENTION_TRITON_AMD_ENABLE=TRUE
```

**Why not native 262K?** The gap is 0.93 GiB (9.48 GiB needed, 8.55 GiB available at 0.95 util). Options exhausted: language_model_only=True patch made it worse; kv-offloading-size native doesn't extend the pool; cpu swap (--swap-space) flag not in kyuz0.
**Why not SGLang?** SGLang 0.5.13 doesn't support Qwen3_5ForConditionalGeneration's DeltaNet hybrid architecture (in_proj_ba parameters not recognized).
**Why not llama.cpp?** Better single-stream (39 vs 19.5 tok/s) but cannot match C=8 batched throughput (vLLM: 126 tok/s vs llama.cpp: ~30-40 tok/s estimated).

---

## Experiment Ledger

### EXP-001 — Baseline: FP8 KV + APC, max_seqs=32, ctx=32K (NO MTP)
**Hypothesis**: Establish baseline with fp8 KV + prefix caching
**Date**: 2026-06-20 | **Engine**: vLLM kyuz0 | **Image**: `kyuz0/vllm-therock-gfx1201:latest`
**Config**: `--kv-cache-dtype fp8 --enable-prefix-caching --max-num-seqs 32 --max-model-len 32768 --gpu-memory-utilization 0.93`
**Env**: `VLLM_ROCM_USE_AITER=0 FLASH_ATTENTION_TRITON_AMD_ENABLE=TRUE`

| Metric | Value |
|---|---|
| C=1 TG | 6.9 tok/s |
| C=8 agg TG | 47.1 tok/s |
| C=16 agg TG | 78.7 tok/s |
| C=32 agg TG | 128.9 tok/s (peak) |
| TTFT@C=1 | 0.33s |
| KV pool | 196,608 tokens |
| Max ctx | 32,768 |
| VRAM used | 29.2 GiB |

**Quality**: PASS | **Kept**: YES (baseline)

---

### EXP-002 — MTP×4, FP8 KV + APC, max_seqs=16, ctx=32K
**Hypothesis**: Built-in MTP draft heads accelerate single-stream TG
**Date**: 2026-06-21 | **Engine**: vLLM kyuz0
**Change**: Added `--spec-method mtp --spec-tokens 4`, reduced max_seqs 32→16

| Metric | Value | vs EXP-001 |
|---|---|---|
| C=1 TG | **18.4 tok/s** | **+2.6×** |
| C=4 agg TG | 64.8 tok/s | +2.5× |
| C=8 agg TG | **126.8 tok/s** | +2.7× |
| C=16 agg TG | 129.9 tok/s (near-sat) | +1.7× |
| TTFT@C=1 | 0.29s | −12% |
| TTFT@C=8 | 1.00s | −66% |
| KV pool | 196,608 tokens | same |
| Max ctx | 32,768 | same |

**Quality**: PASS | **Kept**: YES — new best (C=1 and C≤16)
**Note**: `qwen3_next_mtp` is deprecated alias; use `--spec-method mtp`

---

### EXP-003 — MTP×4, FP8 KV + APC, max_seqs=32, ctx=32K
**Hypothesis**: More concurrency slots find higher throughput ceiling
**Date**: 2026-06-21 | **Change**: max_seqs 16→32

| Metric | Value | vs EXP-002 |
|---|---|---|
| C=1 TG | 19.2 tok/s | +4% |
| C=8 agg TG | 130.9 tok/s | +3% |
| C=16 agg TG | 134.2 tok/s | +3% |
| C=24 agg TG | **134.9 tok/s** (PEAK) | — |
| C=32 agg TG | 130.3 tok/s (declining) | — |
| KV pool | 118,468 tokens | **−40%** |
| Max ctx (effective) | 32,768 | same |

**Quality**: PASS | **Kept**: NO — KV pool drops 40% for only 4% C=1 gain
**Note**: CUDA graph capture for max_seqs=32 + MTP consumes extra ~2 GiB VRAM

---

### EXP-004 — MTP×4, FP8 KV + APC, max_seqs=16, ctx=131K, limit-mm image=0
**Hypothesis**: 131K context + disable image inputs to save encoder cache memory
**Date**: 2026-06-21 | **Change**: max_model_len 32768→131072; added `--limit-mm-per-prompt '{"image": 0}'`

| Metric | Value | vs EXP-002 |
|---|---|---|
| C=1 TG | **18.3 tok/s** | −1% (noise) |
| C=4 agg TG | 70.4 tok/s | +9% |
| C=8 agg TG | **129.6 tok/s** | +2% |
| KV pool | 197,918 tokens | same |
| Max ctx per req | **131,072** | **+4×** |
| Max concurrency @131K | 1.51× | limited by ctx |
| Encoder cache | 12,288 (still allocated) | not affected |
| VRAM used | 31.7 GiB | same |

**Quality**: PASS | **Kept**: YES — free 131K context, same performance
**Note**: `--limit-mm-per-prompt '{"image": 0}'` does NOT skip vision encoder loading — encoder cache is the DeltaNet/GDN recurrent state cache, NOT a vision ViT. The model (Qwen3_5ForConditionalGeneration) does have a ViT (~700 MB), but setting image=0 only blocks inference, not loading. `language_model_only: False` in config.json.
**Current best context**: 131,072 tokens | **Native context**: 262,144 tokens

---

---

### EXP-005 — 0.95 gpu_util + max_seqs=8, ctx=262K (ATTEMPTED)
**Hypothesis**: Lower max_seqs reduces CUDA graph overhead, higher gpu_util expands KV pool
**Date**: 2026-06-21 | **Change**: max_seqs 16→8, gpu_util 0.93→0.95, max_model_len=262144

**Result**: FAILED — OOM at 262K
- Available KV: **8.55 GiB** (234,320 tokens max at 0.95 util + max_seqs=8)
- Needed for 262K: 9.48 GiB
- Gap: **0.93 GiB** — exactly the ViT encoder size, but it's packed into the safetensor weight shards
- vLLM reported: `estimated maximum model length is 234320`

**Key learning**: 0.95 gpu_util + max_seqs=8 is the optimal GPU-only config — gives 234K context (~89% of native).

---

### EXP-007 — language_model_only=True patch (ATTEMPTED)
**Hypothesis**: Patching config.json `language_model_only: True` skips ViT, frees ~700MB VRAM for KV
**Date**: 2026-06-21 | **Change**: initContainer patches config.json before vLLM starts

**Result**: FAILED — made things WORSE (8.04 GiB vs 8.55 GiB without patch)
- Available KV: **8.04 GiB** (218,160 tokens max)
- The patch caused the encoder cache to allocate MORE memory (16,384 tokens vs 12,288)
- Root cause: `language_model_only=True` doesn't skip the ViT in vLLM. The ViT weights are embedded in the same compressed-tensors safetensor shards as the LM. The `language_model_only` flag changes the recurrent state (GDN "encoder") cache budget — it expanded rather than contracted.

**Reverted**: config.json restored to `language_model_only: False` for subsequent experiments.

---

### EXP-005c — 0.95 gpu_util + max_seqs=8 + --swap-space 8, ctx=262K
**Hypothesis**: CPU KV swap covers the 0.93 GiB gap to native context; GPU pool handles hot tokens, CPU handles oldest blocks of ultra-long sessions
**Date**: 2026-06-21 | **Status**: IN PROGRESS

---

---

### EXP-008 — SGLang 0.5.13 + cyankiwi compressed-tensors (ATTEMPTED)
**Hypothesis**: SGLang mainline on ROCm after flashinfer stub fix
**Date**: 2026-06-21 | **Image**: `ghcr.io/tanguille/sglang-rocm-gfx1201:gfx1201@sha256:ab04a7ec`
**Config**: `--attention-backend triton --quantization compressed-tensors --kv-cache-dtype fp8_e4m3`

**Result**: FAILED — `NameError: name 'gptq_marlin_repack' is not defined`
- `compressed_tensors_wNa16.py:250` calls `gptq_marlin_repack` (CUDA Marlin kernel, CUDA-only)
- `sgl_kernel` for ROCm has `gptq_gemm`/`gptq_shuffle` but NOT `gptq_marlin_repack`
- Fix requires: different model format, or patching the sglang compressed-tensors weight loader

---

### EXP-009 — SGLang 0.5.13 + mattbucci/Qwen3.6-27B-AWQ (ATTEMPTED)
**Hypothesis**: mattbucci uses plain `awq` format (not compressed-tensors), avoiding Marlin repack
**Date**: 2026-06-21 | **Config**: `--quantization awq --dtype float16 --kv-cache-dtype fp8_e4m3`

**Result**: FAILED — DeltaNet architecture not supported
- Weights loaded but 48 DeltaNet layers gave: `Parameter model.layers.X.linear_attn.in_proj_ba.weight not found in params_dict`
- DeltaNet `in_proj_ba` + GDN-specific parameter naming not in SGLang 0.5.13's Qwen3.5 model loader
- With `--disable-cuda-graph`: same outcome — model loads with zero-initialized DeltaNet weights, worker crashes
- SGLang 0.5.13 **does not support Qwen3_5ForConditionalGeneration's hybrid DeltaNet architecture**
- Fix requires: SGLang adding a DeltaNet-aware model loader for Qwen3.5

**Final verdict on SGLang 0.5.13**: NOT VIABLE for Qwen3.6-27B on gfx1201. Model architecture not supported in either mainline OR mattbucci fork. Would require significant SGLang development effort to support DeltaNet/GDN layers.

---

### EXP-011 — vLLM 234K context: max_seqs=8, 0.95 gpu_util, MTP×4
**Hypothesis**: Best achievable context with GPU-only KV — 234K = 89% of native, max_seqs=8 meets C≥8 requirement
**Date**: 2026-06-21 | **Config**: vLLM kyuz0, cyankiwi AWQ-INT4, FP8 KV, APC, MTP×4, gpu_util=0.95

| Metric | Value | vs EXP-004 (131K) |
|---|---|---|
| C=1 TG avg | **19.5 tok/s** | +6% |
| C=4 agg TG | 61.2 tok/s | −13% |
| C=4 per-stream | 15.3 tok/s | −13% |
| C=8 agg TG | **126.1 tok/s** | −3% (noise) |
| C=8 per-stream | 15.8 tok/s | −3% |
| KV pool | 234,320 tokens | +18.4% |
| Max ctx per req | **234,320** | **+79%** |
| Max concurrent @234K | **1.0×** | — |
| Max concurrent @32K | 7.1× | (typical user budget) |
| Max inflight (max_seqs) | **8** | −50% vs 131K's 16 |
| VRAM used | 30.1 GiB | same ballpark |

**Quality**: PASS (deterministic temperature=0 completions, coherent English)
**Kept**: YES — **FINAL PRODUCTION CONFIG**

**Verdict**: 234K context at near-native model length, essentially same C=8 throughput, better C=1 TG. Trade-off: max_seqs=8 limits inflight requests vs 131K's 16 — for C=4 workloads this reduces throughput 13%. For the stated C≥8 objective, this config wins.

---

## Planned Experiments (ordered by expected impact)

| # | Hypothesis | Variable | Risk |
|---|---|---|---|
| EXP-012 | Chunked prefill (`--enable-chunked-prefill`) improves PP throughput | prefill batching | May conflict with MTP |
| EXP-013 | spec_tokens=5 or 6 — does deeper MTP help at C=1? | MTP depth | Diminishing returns likely |
