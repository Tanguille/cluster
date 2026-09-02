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
