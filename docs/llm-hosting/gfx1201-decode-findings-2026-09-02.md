# gfx1201 decode findings, 2026-09-02

Reference for the qwen38-27b-vllm deployment on control-1 (Radeon AI PRO R9700,
gfx1201, 32 GiB). Records what was measured, what was withdrawn, and the one
upstream change worth waiting for.

## Measurement methodology, learned the hard way

**This engine is bimodal until warmed.** Cold reps land ~29-32 tok/s aggregate at
M=2; warm reps land ~55. Same pod, same config, zero traffic, no restart. One
warmup run is NOT enough -- a TRITON arm sat at ~29 for three reps and jumped to
55.75 on the fourth.

Consequences, all of which produced wrong conclusions before being caught:

- A "+90% from the LDS gate" claim, built on a single cold baseline (28.64).
  Withdrawn; the isolated warm figure is **+26.5%**.
- An "AITER is ~10% faster than TRITON at M=2" claim, caused by the AITER arm
  running warm (it followed a full sweep) while TRITON started cold after a
  restart. Withdrawn; they are at parity.
- A "1.56x from tile tuning at M=4" claim. The gfx12x heuristic uses
  `(16,16,128,4)` for every `M <= 32` and the grid is `cdiv(M,16)=1` for
  M=1,2,4,6, so the M=4 and M=6 default launches execute identical work. A 50%
  gap between identical kernels was a cold sample. Real figure ~1.06x.

**Rules for any future A/B here:** equalise warming across arms (not one warmup
run); verify no in-flight traffic *during* each rep, not just before; and treat
any microbenchmark delta under ~1.5x as untrustworthy -- two runs of the same
kernel and shape disagreed by 43% on this rig.

Benchmarks use `bench/concsweep_real.py` (sweeps M=1..6, one point per real batch
size) rather than `bench/concsweep.py`, whose hardcoded `[1, 8, 16]` collapses to
M=1 and M=6-with-a-queue under `max_num_seqs: 5` -- two points masquerading as a
curve.

## Attention backend: AITER and TRITON are at parity

Warm, traffic-verified, identical protocol:

| | M=2 | M=3 |
|---|---|---|
| AITER (4 reps) | 54.35 55.96 55.46 55.48 -> **55.31** | 65.31 63.79 62.67 64.50 -> **64.07** |
| TRITON (5 warm reps) | 55.75 55.45 55.89 56.25 55.25 -> **55.72** | 64.06 64.18 65.67 65.67 64.83 -> **64.88** |

Differences are inside the within-arm spread. AITER runs ~6% lower sclk and ~4 C
cooler at equal output; that and maintenance cost are the only grounds to choose
between them. All the throughput gained on 2026-09-02 came from the LDS gate and
the cudagraph capture sizes, both backend-independent.

## MTP works, drafts well, and is unusable at batch

`--speculative-config '{"method":"mtp","num_speculative_tokens":3}'`. Weights ship
with the checkpoint as `model-mtp.safetensors` -- no download, no separate draft
model. **The llmkube `speculativeDecoding` CRD field does not work for vLLM**: it
emits `--spec-type`/`--spec-draft-max` (llama.cpp/SGLang flags) and vLLM silently
reports `speculative_config=None`. Use `extraArgs`.

| concurrency | baseline | MTP k=3 |
|---|---|---|
| 1 | 27.10 | **30.55** (+12.7%) |
| 5 | **91.27** | 50.22 (**-45%**) |

Acceptance is excellent and holds at batch: **49.83% / length 2.49** at conc 1
(positions 71.1 / 47.1 / 31.3), 44.13% / 2.32 at conc 5. For comparison, a
2x RTX 5060 Ti box running MTP K3 on the same benchmark reports 41.27% / 2.24.
**Our draft head is better than theirs.**

The loss is entirely in verification. At conc 5 the verify step costs ~4x a normal
decode step (ITL 48 -> 198 ms mean, P99 835 ms), swallowing the 2.32x multiplier.

**MTP is a single-stream optimization on this deployment.** It is why a comparison
box configured with 1 slot beats us on single-stream (45.53 vs 27.10 tok/s) while
we beat its ceiling in aggregate (91.27).

### Trap: MTP costs VRAM, so re-derive maxModelLen

Enabling MTP drops the KV pool ~288.5K -> ~231.7K. Leaving `maxModelLen` at a
value that puts the pool/cap ratio under the documented **1.17x** knee causes
request *hangs* (HTTP 000 timeouts, 33/50 failed), not just slowness. At
maxModelLen 198,000 the ratio is exactly 1.17x and failures go to zero. A first
attempt at 219,000 (ratio 1.06) produced the hangs and a false "the upstream
blockers are unresolved" conclusion.

## Root cause: no backend on this card supports spec-as-decode

Verify batches only get FULL cudagraphs when the attention backend declares
`_init_reorder_batch_threshold(..., supports_spec_as_decode=True)`.

| backend | available on gfx1201 | supports_spec_as_decode |
|---|---|---|
| TRITON_ATTN | yes | **no** (never sets it) |
| ROCM_AITER_UNIFIED_ATTN | yes, via our patch | **no** |
| ROCM_ATTN | no -- `(2, num_blocks, ...)` layout is connector-incompatible | - |
| ROCM_AITER_FA | **no -- "compute capability not supported"** (CDNA-only) | yes |
| TURBOQUANT | yes, via 3 patches | **False; vllm#53410 flips it** |

`ROCM_AITER_FA` is otherwise a perfect fit (head_size 256, fp8_e4m3, LBHNC,
`supports_kv_connector()` True with no patch needed) and is refused purely on
compute capability.

So **TurboQuant + vllm#53410 is the only route to batched MTP on this card** --
which is the real reason to care about TurboQuant, not its +92% KV pool (Hermes
caps at 5 sessions and cannot spend the capacity).

## What to watch, in priority order

1. **Any commit adding `supports_spec_as_decode=True` to `triton_attn.py` or
   `rocm_aiter_unified_attn.py`.** This would make MTP viable with no TurboQuant
   at all -- a config change instead of a five-patch stack. Best possible outcome.
2. **vllm#53410** -- TurboQuant verify batches as decodes with FULL cudagraphs.
3. A real `gfx1201-MHA-DEFAULT.json` in AITER. TurboQuant decode (-27% at M=6,
   -54% at M=1) was measured on a **borrowed gfx1151 config**, so some of that
   loss is likely untuned tiles rather than architecture.
4. The CK `mha_varlen_fwd` segfault at head_dim 256 on gfx12 -- fixing it removes
   one of the three TurboQuant patches.
5. **vllm#52619** -- the LDS-gate one-liner carried out-of-tree here; drop ours
   when it lands.

**Do not live-patch the whole stack.** Reaching batched MTP today needs three
out-of-tree TurboQuant patches plus a whole-file overlay of an unmerged PR plus
MTP config, coupled to an exact vLLM build that Renovate bumps every few days,
on a foundation measured with an untuned tile config.

## Corrected attributions

- `fused_gdn_decode_post_conv_mtp is not built` is **not** a finding. That kernel
  is gated by `num_spec_decodes > 0` -- it is the MTP-only path, and CUDA-only by
  CMake. `is_rdna_gdn_triton_kernels_available()` is True here, so the AITER RDNA
  fused Triton decode already runs. Nothing to gain.
- `vllm#45916` (split-KV paged decode, gfx12, head_dim 256) patches
  `chunked_prefill_paged_decode.py`, imported by `rocm_attn.py` only -- a backend
  this deployment cannot use. Its merging changes nothing here.
- `vllm#51453` (Triton W4A16 GEMM as custom op, +20.8% MI300 decode) patches
  `triton_w4a16.py`. This deployment uses `RDNAHybridW4A16LinearKernel`, which
  already had the custom-op boundary.
- `num_compute_units()` returns **32 on a 64-CU part** (HIP reports WGPs in WGP
  mode). Passing 64 is worth ~21% on down_proj at M=1 but only ~2% at M=2 where
  this deployment operates. Two live call sites, not ten. Low priority.

## Still unmeasured

- Output quality under the raised LDS gate, beyond smoke prompts.
- `lm_head` is 2.54 GB of bf16 per token, ~17% of decode bytes, unquantized.
  vLLM cannot quantize it at load, but re-running llm-compressor with `lm_head`
  out of `ignore` would land it on the same RDNA kernel. Estimated 7-10% at every
  M; needs an lm-eval gate on a 248K vocab.
- K-split of `down_proj` for M=3-5, where it still falls to Triton
  (K*M = 52224/69632/87040, all over 39321). Kernel-level test showed ~2x with
  correct results and better accuracy than the Triton path; chunk on group
  boundaries (48/48/40 groups keeps M=5 at 3 launches).
