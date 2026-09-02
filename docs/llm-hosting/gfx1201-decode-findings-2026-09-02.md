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

## Decode is bandwidth-saturated. Every GEMM, lm_head included, is at the ceiling

Standalone per-op timings taken through the real dispatch, x layer count:

| | M=1 | M=2 | M=3 | M=5 |
|---|---|---|---|---|
| gate_up | 10.87 | 10.82 | 10.69 | 11.92 |
| down_proj | 5.60 | 5.80 | 15.74 | 14.38 |
| gdn in_qkvz | 2.85 | 2.89 | 2.95 | 3.19 |
| gdn out_proj | 1.59 | 1.67 | 1.79 | 2.01 |
| qkv + o (attn) | 1.18 | 1.24 | 1.29 | 1.40 |

At M=1 those imply 523-557 GB/s per projection, i.e. 92-98% of a 566 GB/s
pure-read figure. **The conclusion holds: the model is memory-bound and the
memory system is saturated, so no kernel work remains on these layers.** But
treat the absolute ms as an upper bound only -- see the correction below.

### Correction: standalone timings overstate cost, verified the hard way

`lm_head` looked like the one exception. A standalone proxy measured it at
**6.29 ms and 405 GB/s** against 489 GB/s for the same bytes in a contiguous
`[H,V]` layout, implying ~1.1 ms/step of free win from transposing it.

**That was wrong, and shipping it proved so.** An import hook storing lm_head
transposed (in `process_weights_after_loading`, before the KV cache and
cudagraphs are allocated; free VRAM 13.87 -> 11.50 GiB, KV pool unchanged at
288,508 tokens) measured **-0.5% e2e at every M**:

| M | stock | transposed |
|---|---|---|
| 1 | 31.07 | 30.92 |
| 3 | 65.31 | 64.97 |
| 5 | 103.21 | 102.64 |

Instrumenting the actual call with cuda events, same code path both ways, found
why:

| lm_head, in situ | mean | effective BW |
|---|---|---|
| stock `F.linear` | **4.00 ms** | **~636 GB/s** |
| transposed | 4.20 ms | ~605 GB/s |

**lm_head was already at ~100% of this card's memory bandwidth** (~640 GB/s
spec). The standalone proxy overstated it by 57% and invented a layout problem
that did not exist. The 566 GB/s "ceiling" from `w.sum()` was itself low -- a
reduction, not a pure stream.

Two rules follow, both learned by shipping the wrong thing:

- **A standalone microbenchmark of one op does not predict its in-engine cost.**
  Per-call timing carries launch and sync overhead the captured cudagraph does
  not. Instrument the real call site before believing a budget.
- Every absolute figure in the table above is inflated by the same effect. Their
  *ratios* survived (each projection sits at a similar fraction of bandwidth);
  their millisecond values did not.

### What this leaves for lm_head

It is bf16 because the checkpoint keeps it in `ignore` (as it does the GDN
`in_proj_a`/`in_proj_b`), and it reads 2.543 GB every step regardless of batch
size: **4.00 ms, ~12% of the M=1 step**. Layout is exhausted; only fewer bytes
help. W4A16 g128 would cut the read 3.76x to ~0.68 GB, so at the same bandwidth
~1.1 ms -- saving **~2.9 ms, ~9% at M=1**, less at higher M. Not the ~16% an
earlier revision of this doc projected off the bad proxy.

Cost: a re-quantized checkpoint, a new Model CR and cache PVC, and the quality
question below.

### The 4-bit lm_head quality estimate, and why two proxies were junk

RTN-quantizing the real lm_head to W4A16 g128 asymmetric gives **logit MAE
0.081** (std ~0.10). Converting that to a token error rate needs the real
top1-top2 logit gap distribution, and two proxies got it badly wrong:

- random N(0,1) hidden states -> 68.8% top-1 agreement
- rows of `embed_tokens` as hidden states -> 81.2%

Both produced near-uniform logits (mean top-1 probability **0.0004**), where the
top two are tied and argmax flips on noise. Sampled from the live server with
`logprobs=5` over 8 prompts (n=490): **median gap 3.88, mean top-1 probability
0.810**. Modelling the perturbation as N(0, 0.101) per logit gives an **expected
greedy flip rate of 1.15%**, concentrated where the median gap is 0.125.

That is a noise model, not an eval. It justifies running lm-eval; it does not
replace one.

### lm-eval: no detectable accuracy regression from a 4-bit lm_head

Run 2026-09-02 23:00-23:55 CEST. Method worth reusing: a fake-quant import hook
applies the same RTN math (per-group min/max -> 4-bit level -> dequantize) to
lm_head at load while leaving the weights bf16, so the logits are identical to a
real W4A16 lm_head. **The quality question is answerable without building a 19 GB
re-quantized checkpoint.** Harness: lm_eval 0.4.13, `local-completions` against
the live server, model's own tokenizer copied out of the pod.

Aggregates, full sets:

| task | metric | stock | 4-bit lm_head | delta |
|---|---|---|---|---|
| arc_challenge | acc | 0.5674 | 0.5606 | -0.68 pp |
| arc_challenge | acc_norm | 0.5811 | 0.5785 | -0.26 pp |
| winogrande | acc | 0.7632 | 0.7648 | **+0.16 pp** |

**The baseline reproduced bit-exactly across two runs on different pods**
(0.5674 / 0.5811 / 0.7632 both times), so run-to-run variance is zero and every
delta above is caused by the quantization rather than noise. That also means the
paired comparison below is the right instrument, not the +/-1.45 pp aggregate
stderr.

Paired, same items under both arms:

| task / metric | correct->wrong | wrong->correct | net | items changed | McNemar p |
|---|---|---|---|---|---|
| arc_challenge acc | 16 | 8 | -8 | 2.05% | 0.152 |
| arc_challenge acc_norm | 18 | 15 | -3 | 2.82% | 0.728 |
| winogrande acc | 13 | 15 | +2 | 2.21% | 0.851 |

**No significant regression on any metric.** The perturbation is real and
measurable -- the highest-scoring option changes on **6.91%** of arc_challenge
items and **4.97%** of winogrande items -- but it is symmetric, so accuracy
survives. With ~24-33 discordant pairs the accuracy effect is bounded within
roughly +/-0.8 pp.

Note the earlier noise-model estimate predicted a **1.15% greedy-token** flip
rate. That is a different quantity from the 5-7% option-change rate here:
multiple-choice scoring sums loglikelihoods over a whole continuation, so it
accumulates perturbation across many tokens. The two do not contradict each
other and neither confirms the other.

**Generative eval, 2026-09-03.** The multiple-choice result above says nothing
about generation, which is most of what this deployment serves, so gsm8k
(5-shot, greedy, n=400) and a 20-prompt greedy-divergence capture were run on
both arms.

**Generation on this engine is deterministic.** Three stock captures of the same
20 prompts at temperature 0, one of them on a different pod, were **20/20
byte-identical** to each other. So every difference below is caused by the
quantization, not run-to-run noise.

*Behaviourally the model changes a lot:* **19 of 20** greedy continuations
differ under the 4-bit lm_head, diverging anywhere from character 1 to ~350 of
a few hundred. Nearly every response is different text.

*But accuracy does not degrade -- it goes up*, paired on identical items:

| gsm8k filter | correct->wrong | wrong->correct | net | changed | McNemar p |
|---|---|---|---|---|---|
| flexible-extract | 6 | 28 | **+22** | 8.5% | 0.0002 |
| strict-match | 7 | 23 | **+16** | 7.5% | 0.0052 |

Aggregates: flexible 0.6575 -> 0.7125, strict 0.6200 -> 0.6600.

**Do not read that as "quantization improves reasoning."** An unexplained +5.5 pp
from adding noise to a weight matrix is far more likely to mean the benchmark is
measuring something other than reasoning. The plausible mechanism, untested, is
formatting: gsm8k scores a number extracted from generated text, 95% of
generations changed, and this is a thinking model whose stray `<think>` output
can defeat extraction -- so a formatting shift alone could move both filters.
The result is recorded because it is what was measured, not because the
mechanism is understood.

**What the three tasks jointly support:** no quality regression was detected
anywhere (arc_challenge, winogrande, gsm8k). What they do *not* support is
"behaviour is unchanged" -- it is changed almost everywhere. For an agentic
workload that matters independently of benchmark scores: tool-call arguments,
code, and structured output would all differ from today's model. Nothing here
tested tool-calling directly.

Cost if it does ship: a re-quantized checkpoint, a new Model CR and cache PVC,
for ~9% at M=1.

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
  4-bit lm_head ships. It is the only lever left that is not already at the
  memory ceiling, and it is worth ~9% at M=1.
- In-situ (event-instrumented) timings for the other projections. Only lm_head
  was measured at its real call site; the rest are still standalone upper bounds.
