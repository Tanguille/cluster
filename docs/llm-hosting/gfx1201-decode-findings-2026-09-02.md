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
  `(16,16,128,4)` for every `M <= 32` and the grid is `cdiv(M,16)=1` for every
  M<=16, so the M=4 and M=6 default launches execute identical work. A 50% gap
  between identical kernels was a cold sample. Real figure ~1.06x.
- A "the M=3 and M=5 gains come from the cudagraph capture sizes" claim, which
  fell to its own argument. Padding M=3 to 4 leaves down_proj on Triton at the
  identical `(16,16,128)` tile and `grid=1` at both sizes, so it cannot move much;
  the apparent +65% was measured against the same withdrawn cold row. M=5->6
  padding IS a real mechanism (`MAX_SKINNY_BATCH_SIZE=5` pushes every W4A16
  projection to Triton at M=6), but its warm magnitude is **unmeasured**. The
  capture-size change is kept on that mechanism, not on a number.

**Rules for any future A/B here:** equalise warming across arms (not one warmup
run); verify no in-flight traffic *during* each rep, not just before; and never
compare a rep from one warming state against a rep from another -- cold and warm
are two populations, and every withdrawn claim above is a cross-population
comparison. Within one population the rig is tight (CV ~1%), so a warm-vs-warm
delta of a few percent is real; it is the 43% cold-vs-warm gap that is noise.

`bench/concsweep.py` now takes a concurrency list and defaults to one point per
real batch size. Its previous hardcoded `[1, 8, 16]` collapsed to M=1 and
M=6-with-a-queue under `max_num_seqs: 5` -- two points masquerading as a curve,
and the reason the M=2 cliff went unnoticed for so long. **Keep the top of the
sweep at or below `max_num_seqs`**; points above it re-measure the cap and their
per-stream column is just aggregate/N.

## Attention backend: AITER and TRITON are within ~2%

Warm, traffic-verified, identical protocol:

| | M=2 | M=3 |
|---|---|---|
| AITER (4 reps) | 54.35 55.96 55.46 55.48 -> **55.31** | 65.31 63.79 62.67 64.50 -> **64.07** |
| TRITON (5 warm reps) | 55.75 55.45 55.89 56.25 55.25 -> **55.72** | 64.06 64.18 65.67 65.67 64.83 -> **64.88** |

Differences are inside the within-arm spread (Welch t 1.1 at M=2, 1.3 at M=3):
**no detectable difference at n=4-5 warm**, which is not the same as proven
equality. Scope: decode, short prompts, M=2-3. The manifest still records ~10x
worse AITER prefill and a conc-16 wash from earlier builds; neither was re-measured
here. AITER runs ~6% lower sclk and ~4 C cooler at equal output; that and
maintenance cost are the only grounds to choose between them.

## MTP works and drafts well, but loses at our concurrency

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

At conc 5, ITL went 48 -> 198 ms mean, P99 835 ms. Originally attributed to verify
cost; the likelier cause is that conc 5 ran with no cudagraph at all.

The conc-5 row below was measured with a broken capture-size list and is superseded
by the retest in the next section; the conc-1 row and the acceptance figures stand.

### Trap: MTP costs VRAM, so re-derive maxModelLen

Enabling MTP drops the KV pool ~288.5K -> ~231.7K. A run at maxModelLen 219,000
(pool/cap ratio 1.06) returned 33/50 client-side timeouts; re-deriving to 198,000
(ratio exactly 1.17x) took failures to zero.

**That correlation is n=1 per arm and the mechanism is unknown.** Each arm
followed a restart, so cold-start admission stall through the offload connector's
serial lookup thread is an equally live explanation, and nothing in vLLM ties
`max_model_len` to admission beyond the startup fit check. vllm#49002 *is* cleanly
excluded: grammars attach only via `structured_outputs` or named/`required`
`tool_choice`, and the benchmark sent no tools. Re-derive maxModelLen when
enabling MTP anyway -- it is free -- but do not treat 1.17x as a proven hang
threshold.

## Why MTP collapsed at batch: our capture-size list, not upstream

**The spec-as-decode explanation first recorded here was wrong and is withdrawn.**
`supports_spec_as_decode` only widens `reorder_batch_threshold`, and only when the
backend passed a non-None threshold (`backend.py:629`). It does not gate cudagraph
capture. `TRITON_ATTN` is `AttentionCGSupport.ALWAYS` (`triton_attn.py:100`) and
never calls the function, so it captures FULL graphs at any query length. The flag
matters only to `UNIFORM_BATCH` backends like TurboQuant, which is what vllm#53410
addresses. It was never our blocker.

The actual mechanism, verified in source and in the boot log. With MTP k=3 the
uniform decode query length is 4, and MRV1 rounds every capture size up to a
multiple of it, dropping anything above `max_cudagraph_capture_size`
(`compilation.py:1548`), which is itself taken from the last element of our list:

    [1,2,3,4,5] -> round_up to 4 -> {4,4,4,4,8} -> filtered by <=5 -> [4]

**One** graph, at 4 tokens. Conc 1 is a 4-token batch and hits it (+12.7%). Conc 5
is a 20-token batch, gets `CUDAGraphMode.NONE` from the dispatcher, and runs 64
layers fully eager. That fits ITL 48 -> 198 ms mean far better than "the verify
step costs 4x". Today's non-MTP boot log shows the same code path behaving:
`max_cudagraph_capture_size: 5`, five FULL graphs captured.

**Retested 2026-09-02 20:19-21:05 CEST, and the mechanism was confirmed.** With
`"cudagraph_capture_sizes": [4, 8, 12, 16, 20]` the list survives rounding intact
(`max_cudagraph_capture_size: 20`) and the boot log captures **5 FULL graphs
instead of 1**. That recovered most of the loss:

| | conc 1 | conc 5 |
|---|---|---|
| MTP, capture `[1,2,3,4,5]` (one graph) | 30.55 | 50.22 |
| MTP, capture `[4,8,12,16,20]` (five graphs) | **~38** (34.7-43.5) | **~71** (68.0-74.9, n=7) |
| no MTP, current prod | 30.6-31.1 | **94.7-102.9** |

So the capture-size list cost ~41% at conc 5 and ~24% at conc 1. It was a real
self-inflicted bug and it is now measured, not inferred.

**MTP still loses at full concurrency, so it stays off.** Warm sweep, MTP vs the
no-MTP prod numbers at the same M:

| M | no MTP | MTP k=3, 5 graphs | |
|---|---|---|---|
| 1 | 30.6-31.1 | 34.7-43.5 | **+23%** |
| 2 | 54.4-56.3 | 47.8-48.1 | -13% |
| 3 | 61.8-65.1 | 69.5-72.5 | +12% |
| 4 | 76.1-84.4 | 90.5-90.6 | +10% |
| 5 | 94.7-102.9 | 68.9-74.1 | **-27%** |

Acceptance stayed excellent throughout (49.97% / length 2.50 at conc 5, positions
70.4 / 47.2 / 32.4), so this is not a draft-quality problem. It is the ordinary
spec-decode crossover: at M=5 the batch is already compute-bound, and drafting 3
tokens per step to accept 2.5 spends flops the GPU does not have spare. The M=2
dip and the M=3/M=4 gains are within the noise this rig has shown before and are
not claimed as structure.

**Verdict: reverted.** MTP is a real win below ~M=4 and a 27% loss at the cap we
actually run. Revisit only if production concurrency drops or slots change.

### Backend spec-as-decode table, for reference only

Kept because it took a while to establish, but it explains nothing about our MTP
result:

| backend | available on gfx1201 | supports_spec_as_decode |
|---|---|---|
| TRITON_ATTN | yes | no (never calls it; `ALWAYS` cudagraph support) |
| ROCM_AITER_UNIFIED_ATTN | yes, via our patch | no (same) |
| ROCM_ATTN | no -- `(2, num_blocks, ...)` layout is connector-incompatible | - |
| ROCM_AITER_FA | **no -- "compute capability not supported"** (CDNA-only) | yes |
| TURBOQUANT | yes, via 3 patches | False; vllm#53410 flips it |

## What to watch, in priority order

1. ~~A retest of MTP with corrected capture sizes.~~ **Done 2026-09-02**: the
   capture-size bug was real and worth ~41% at conc 5, but MTP is still -27% at
   M=5. Reverted. Nothing further to watch upstream for this.
2. **vllm#53410** -- TurboQuant verify batches as decodes with FULL cudagraphs.
   Only relevant if the capture-size retest fails.
3. A real `gfx1201-MHA-DEFAULT.json` in AITER. TurboQuant decode (-27% at M=6,
   -54% at M=1) was measured on a **borrowed gfx1151 config**, so some of that
   loss is likely untuned tiles rather than architecture.
4. The CK `mha_varlen_fwd` segfault at head_dim 256 on gfx12 -- fixing it removes
   one of the three TurboQuant patches.
5. **vllm#52619** -- the LDS-gate one-liner carried out-of-tree here; drop ours
   when it lands.

**Do not live-patch the whole stack** *if* the capture-size retest fails. The
TurboQuant route needs three out-of-tree patches plus a whole-file overlay of an
unmerged PR plus MTP config, coupled to an exact vLLM build that Renovate bumps
every few days, on a foundation measured with an untuned tile config. The retest
is a two-line config change and must be tried first.

## Corrected attributions

- `fused_gdn_decode_post_conv_mtp is not built` is **not** a finding, but not for
  the reason first recorded. `hasattr(_C, ...)` on that symbol is the probe for the
  *entire* CUDA fused GDN decode family, plain decode included -- not an MTP-only
  path. It is absent because the family is CUDA-only by CMake, so it is **not
  applicable on ROCm** rather than "nothing to gain".
  `is_rdna_gdn_triton_kernels_available()` is True here and the AITER RDNA fused
  Triton decode runs instead.
- `vllm#45916` (split-KV paged decode, gfx12, head_dim 256) patches
  `chunked_prefill_paged_decode.py`, imported by `rocm_attn.py` only -- a backend
  this deployment cannot use. Its merging changes nothing here.
- `vllm#51453` (Triton W4A16 GEMM as custom op, +20.8% MI300 decode) patches
  `triton_w4a16.py`, which the RDNA path never calls; this deployment uses
  `RDNAHybridW4A16LinearKernel`, already behind a custom op. Merged 2026-09-02
  and changes nothing here either way.
- `num_compute_units()` returns **32 on a 64-CU part** (HIP reports WGPs in WGP
  mode). Passing 64 is worth ~21% on down_proj at M=1 but only ~2% at M=2 where
  this deployment operates. Two live call sites, not ten. Low priority.

## Decode is bandwidth-bound, and every quantized GEMM is already at the ceiling

Measured 2026-09-02 in-container. Achievable bandwidth on this card is
**566 GB/s** (pure streaming read of a 485 MiB tensor). Per-decode-step GEMM
budget, ms, all 64 layers, through the real dispatch:

| | M=1 | M=2 | M=3 | M=5 | eff. BW at M=1 |
|---|---|---|---|---|---|
| gate_up | 10.87 | 10.82 | 10.69 | 11.92 | **557 GB/s** |
| down_proj | 5.60 | 5.80 | 15.74 | 14.38 | **537 GB/s** |
| gdn in_qkvz | 2.85 | 2.89 | 2.95 | 3.19 | ~540 |
| gdn out_proj | 1.59 | 1.67 | 1.79 | 2.01 | ~540 |
| qkv + o (attn) | 1.18 | 1.24 | 1.29 | 1.40 | ~540 |
| **lm_head (bf16)** | **6.34** | ~6.3 | ~6.3 | 6.02 | **405 GB/s** |
| total | 28.4 | 28.7 | 38.8 | 38.9 | |

Measured step time is ~32.3 ms at M=1, so GEMMs are ~88% of it.

**This is the headline result: every W4A16 projection already runs at 95-98% of
the card's achievable bandwidth.** There is no kernel work left on them - not
tile tuning, not cu_count, not a better dispatch. The model is memory-bound and
the memory system is saturated. The only way to make those layers faster is to
move fewer bytes.

**The single exception is `lm_head`**, which is bf16 (it sits in the checkpoint's
`ignore` list, as do the GDN `in_proj_a`/`in_proj_b`) and runs at 405 GB/s, 72%
of ceiling. It is 2.543 GB read every step regardless of batch size, ~20% of the
M=1 step.

Two independent gains are available there, measured with a scaled proxy
(V=49664, only 2.6 GiB free) and extrapolated linearly - per-1k-vocab cost was
constant to within 3%, so the extrapolation is sound:

| lm_head variant | M=1 | vs today | quality |
|---|---|---|---|
| `F.linear`, `[V,H]` (today) | 6.29 ms | - | - |
| same weights, stored `[H,V]` | 5.18 ms | **-1.1 ms** | identical |
| W4A16 g128 (RDNA kernel) | **1.17 ms** | **-5.1 ms** | ~1.15% greedy flips |

The transposed-layout win is free arithmetic: `a @ wt` hits 489 GB/s where
`F.linear` gets 405.

### The 4-bit lm_head quality estimate, and why the first two attempts were junk

RTN-quantizing the real `lm_head` to W4A16 g128 asymmetric gives **logit MAE
0.081** (std ~0.10). Turning that into a token-level error rate needs the real
top1-top2 logit gap distribution, and two proxies got this badly wrong first:

- random N(0,1) hidden states -> 68.8% top-1 agreement
- rows of `embed_tokens` as hidden states -> 81.2%

Both are artifacts: each produced a near-uniform logit distribution (mean top-1
probability **0.0004**), where the top two logits are tied and argmax flips on
noise. Real distributions are nothing like that. Sampled from the live server
with `logprobs=5` over 8 prompts (n=490 positions): **median gap 3.88, mean top-1
probability 0.810**, and only 4.9% of positions carry >1% flip risk. Modelling
the perturbation as N(0, 0.101) per logit gives an **expected greedy flip rate of
1.15%**, concentrated where the median gap is 0.125 - positions the model is
genuinely undecided on.

That is an estimate from a noise model, not an eval. It justifies running
lm-eval; it does not substitute for one.

## K-split of down_proj: real at the kernel, unaffordable in practice

Warm, interleaved arms (a first attempt with 10 warmup iterations gave garbage
and was discarded): down_proj K-split onto the HIP skinny GEMM is **1.98x at M=3,
1.72x at M=4, 1.33x at M=5**, with better accuracy than Triton (0.0047 vs 0.0095).

It cannot be used. `wvSplitK_int4_g` **ignores strides**: a non-contiguous K-slice
runs without error and returns garbage (relative error 4.77 vs 0.0047). So chunks
must be materialised contiguously, duplicating down_proj at 44.5 MB x 64 layers =
**2.85 GB**. Free VRAM is **2.62 GiB**, and lm_head quantization would only return
~2.2 GB of it.

The zero-memory alternative - storing the weight chunk-major so every M goes
chunked - was measured across all M: +96%/+70%/+33% at M=3/4/5, but **-10% at M=1,
-17% at M=2, -10% at M>=6**. Against the real traffic mix below that nets ~+4-5%
e2e with a regression at the second-most-common batch size, for a load-time
repack plus a dispatch rewrite carried out-of-tree. **Not worth it.**

### Production batch-size distribution (vmsingle, share of busy time)

| M | 7d | 30d |
|---|---|---|
| 1 | 38.7% | 29.1% |
| 2 | 25.4% | 21.1% |
| 3 | 19.8% | 25.5% |
| 4 | 11.1% | 14.9% |
| 5 | 4.1% | 6.6% |

Two thirds of busy time is M=1-2. Optimizations that only pay above M=3 are worth
roughly a quarter of their headline number.

## Still unmeasured

- Output quality under the raised LDS gate, beyond smoke prompts.
- An actual lm-eval run behind the 1.15% greedy-flip estimate above, before any
  4-bit lm_head ships.
- Whether the transposed-lm_head layout can be applied at load time. It needs
  both copies live during the transpose (~4.7 GB); the KV cache is explicitly
  sized and allocated after weights, so the headroom may exist at that moment.
