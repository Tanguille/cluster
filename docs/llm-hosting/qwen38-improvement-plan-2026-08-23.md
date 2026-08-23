# qwen38-27b-vllm improvement plan — 2026-08-23

Sequenced list of further improvements to `qwen38-27b-vllm` (R9700, gfx1201,
shared with Jellyfin under a hard 2GiB VRAM reserve), reviewed by two
independent LLMs (Opus, GPT-5.6) specifically against our REAL workload shape
— not the published-benchmark shape most of this evidence originally came
from. See "Why order matters" before skipping ahead.

## Our workload, for calibrating every step below

- Primary consumer is Hermes (agentic assistant): mixed tool-calling +
  multi-turn reasoning, low bursty concurrency (2 running requests is a
  typical live snapshot), not sustained high-QPS batch traffic.
- Context is large and retention-heavy: 246,944 max-model-len, hierarchical
  KV offload (CPU tier + fs/Ceph tier) specifically to retain context across
  turns. Combined prefix-cache hit rate measured at 92-93% (see "Already
  resolved" below).
  `docs/llm-hosting/vllm-optimization-log-2026-08.md` has the full tuning
  history behind this state.
- Many requests are prefill-bound, not decode-bound (129:1-ish prompt:output
  in real traffic) — `maxNumBatchedTokens` is capped at 4096 specifically
  because raising it starves decode behind an over-large prefill batch.
- Tool-calling uses grammar-constrained generation. MTP speculative decoding
  is disabled today because it measured a ~100x regression (0.2 tok/s) under
  *concurrent* grammar-constrained tool traffic — a verify-step × grammar-mask
  interaction, not a generic MTP problem.
- The GPU has a **hard, non-negotiable 2GiB VRAM reserve** for Jellyfin
  transcoding on the same card. Any change that grows steady-state or peak
  VRAM use must justify itself against that, not a theoretical ceiling.

## Why order matters

Published spec-decode/throughput numbers (GSM8K sweeps, conc=32 batch
benchmarks, H200 datacenter runs) measure a workload shape we don't have.
Two independent reviews agreed the highest-value, lowest-risk moves are the
ones that improve *our actual traffic shape* (prefix-cache efficiency, prefill
cost) before touching anything that trades GPU-side complexity for a win that
mostly shows up in a different regime.

Those cheap moves have now been measured, and they came back **already
optimal** — see "Already resolved" below. That result is what makes the
remaining steps worth their cost: with 6.7% of prompt tokens left to compute,
there is no cheap prefill win left to take first, so the only headroom is
decode-side (Step 1) or a different engine (Steps 2-3). Remaining steps are
ordered cheapest/safest → most disruptive.

## Already resolved 2026-08-23 — measurement only, no restart, no config change

Three planned investigations (prefix-cache instrumentation, Hermes prefix
stabilization, and the fs-tier keep/drop decision) turned out to be answerable
from telemetry already being scraped. Results, not plans:

**Prefix caching is already near its ceiling.**
`vllm:prompt_tokens_by_source_total` exports the cache-outcome breakdown
server-side, so the `--enable-prompt-tokens-details` restart that was going to
be the prerequisite here is not needed for the token-level answer. Over 24h,
`service="qwen38-27b-vllm"`:

| source | prompt tokens (24h) | share |
|---|---|---|
| `external_kv_transfer` | 34,669,600 | 51.7% |
| `local_cache_hit` | 27,822,400 | 41.5% |
| `local_compute` | **4,503,759** | **6.7%** |

Only 6.7% of prompt tokens are actually recomputed. Cross-checks against
`vllm:prompt_tokens_cached_total / vllm:prompt_tokens_total` = **93.3%**, and
against the independent block-level combined rate of **92.1%** (GPU-local
40.7%, external 86.7%, `combined = gpu + (1-gpu) * ext`). The "85%" figure
carried in older docs was stale and understated this.

Consequence: **Hermes-side prefix stabilization is dropped.** Its gate was
"only pursue if the data supports it" — a perfect fix is bounded by that 6.7%,
and a 93.3% hit rate is evidence the prefix is already stable. Revisit only if
`local_compute` climbs past ~15%, which would signal something upstream started
perturbing the prefix. `--enable-prompt-tokens-details` would still buy
per-request TTFT-vs-cached-ratio correlation that the aggregate can't give;
that's now low-value, so fold it into some future restart rather than spending
one on it.

**Keep the fs offload tier.** Two independent lines of evidence, and the second
refutes the premise the drop-it question rested on:

1. Already settled experimentally, recorded in the manifest
   (`qwen38-27b-vllm.yaml:349-351`): removing the fs tier **halved
   single-stream decode** (15.5 vs 31 tok/s, exact-revert confirmed). Its hit
   rate was never the point — it keeps eviction asynchronous.
2. **The long-lookup tail is not fs-specific**, measured over 24h:

   | metric (24h, `service="qwen38-27b-vllm"`) | fs/tiering | CPU tier |
   |---|---|---|
   | `kv_offload_*lookup_async_delay_seconds_count` | 4,287 | 1,025 |
   | fraction `<= 10s` | 89.3% | 86.4% |
   | p50 delay | 0.23s | 0.26s |

   The CPU tier's >10s tail is *slightly worse*. Dropping fs would not remove
   the stall — it's the connector's serialized per-tier lookup thread, as
   root-caused in memory `vllm-kvoffload-lookup-stall`. The fs read path
   measures ≈**1.39 GB/s** (25.0 GB over 17.93s of read time), confirming the
   bottleneck is lookup scheduling, not disk. The only real action left is
   upstream; nothing to change in this repo.

   (The fs tier's chunk-level hit rate is 59.5% on 80,085 queries. That is a
   different measurement from the 4.5% figure in the manifest comment — don't
   conflate the two.)

## Step 1 — DFlash2 retest — **BLOCKED 2026-08-23: DFlash2 is broken in the current vLLM nightly**

Sizing boot attempted in a real GPU window (production scaled to 0, restored
after). **Both draft variants failed to load, identically**, before any
profiling happened:

```
ValueError: There is no module or parameter named 'layers.0.attention_conv'
in DFlash2Qwen3Model. The available parameters belonging to layers.0
(DFlashQwen3DecoderLayer) are: {...}
```

Image under test: `vllm/vllm-openai-rocm:nightly@sha256:3a064e7a78dd45df3de3db2a7fa10e46f88414324b6e814153ada059f0ac0088`
(what production runs today).

**This is not a quantization problem and not a gfx1201 problem** — the two
things this plan predicted the risk would be. The bf16 `z-lab/Qwen3.8-27B-DFlash2`
draft, the exact checkpoint #4651 ran successfully in production for 2.5h,
fails with the same error on the current image. Ruled out along the way:

- **Not a malformed checkpoint.** `syvai`'s and `z-lab`'s `dflash_config` are
  identical (`conv_kernel_size: 2`, `selector_rank: 256`, same
  `target_layer_ids`), both `architectures: ['DFlash2DraftModel']`.
- **Not a missing conv tensor.** Both checkpoints contain
  `layers.0.attention_conv.base_kernel` and `.kernel_projection.weight`.
- **Not the fused-vs-split QKV difference** between them (syvai ships 154
  tensors with fused `qkv_proj.weight_packed`; z-lab ships 81 with split
  `q_proj`/`k_proj`/`v_proj`) — bf16 fails too.

**Mechanism:** vLLM builds the draft out of `DFlashQwen3DecoderLayer` (no conv
modules) instead of `DFlash2Qwen3DecoderLayer` (has them —
`qwen3_dflash2.py:104,134`). `DFlash2Qwen3Model` declares
`decoder_layer_cls = DFlash2Qwen3DecoderLayer`, but the parent
`qwen3_dflash.py` hardcodes the base class at line 442 instead of consulting
it, so the override is dead code.

**Bisected to the exact commit** — `2f55ef254c70`, "[Model] Add Qwen3-Omni
DSpark support" (vllm-project/vllm#52560), 2026-08-22T22:35Z. It is the only
commit touching `qwen3_dflash.py` between the working nightly and ours:

| commit | date | `decoder_layer_cls` attr | line 442 |
|---|---|---|---|
| `b389ac29465b` (the DFlash2 PR) | 08-21 | present (line 380) | `self.decoder_layer_cls(` ✅ |
| `e9d1398d9edf` (nightly 08-22) | 08-22 03:23 | present | ✅ |
| **`2f55ef254c70` (#52560)** | **08-22 22:35** | **removed** | `DFlashQwen3DecoderLayer(` ❌ |
| `a3561ef8e49d` (nightly 08-23, current pin) | 08-23 04:34 | removed | ❌ |

**Still broken in upstream `main` as of 2026-08-23** — not just our pin.

**The last good build is `nightly-e9d1398d9edfd90fcc1cf783805240e3effec013`**
= digest `sha256:0539b7e121748a245859f58a519ec2fd4e77543cb04cae18124b204116ba5409`,
which is our own previous pin (#4644). vLLM builds nightlies once daily at
~05:30 UTC and the regression landed at 22:35, so no build exists between the
last good one and the first bad one. Confirmed still pullable.

## VERDICT 2026-08-23: DFlash2 is disqualified on VRAM. Measured, not modelled.

The sizing boot ran successfully on the last good image (`0539b7e1`) with the
bf16 draft. vLLM's own profiler, whole card, `gpu_memory_utilization 0.875`:

| component | GiB |
|---|---|
| consumed (weights + non-torch) | 22.52 |
| peak activation | 2.80 |
| CUDAGraph | 0.41 |
| **max KV that fits, entire card, zero reserve** | **5.92** |

vLLM's own line: *"`--kv-cache-memory=6353409024` (5.92 GiB) to fully utilize
gpu memory."* Production runs **9 GiB** today. Subtract the mandatory 2 GiB
Jellyfin transcode reserve and DFlash2 leaves **~3.9 GiB of KV — a 56% pool
cut**, i.e. roughly 125K tokens of pool and a `maxModelLen` near 107K, which is
**below Hermes' measured 112K peak**. That is not a tuning problem, it is a
ceiling.

Target checkpoint is 18.21 GiB, so 22.52 − 18.21 ≈ **3.6 GiB of draft weights**,
matching the 3.58 GiB measured on HF. The cost is *static weights*, which is
why it cannot be tuned away: draft `kv_cache_dtype` and draft `max_model_len`
(both real knobs in `SpeculativeConfig`) only affect draft **KV**, not weights.
The one mechanism that reduces weights is quantization — proven unloadable
above. There is no remaining lever.

Weigh this against what the pool buys us: a 93.3% token-level cache hit rate on
a 129:1 prefill-bound workload (see "Already resolved"). Halving the pool to buy
decode speed is the wrong trade for this workload even if it worked.

**Do not revisit** unless upstream both fixes the quantized-draft path *and*
the W4A16 draft's 2.39 GiB saving proves sufficient — which, against a 3.1 GiB
shortfall (9 → 5.92), it still would not be on its own.

### Earlier finding: the current nightly can't load any DFlash2 draft

**What this cost this window:** the sizing boot could not run on the current
image at all, which is why it ran on `0539b7e1`.

**Next actions:**
1. File upstream against `vllm-project/vllm` — the trace plus the bisect above
   is a clean, minimal repro, and `main` is still affected.
2. Pinning back to `0539b7e1` costs exactly **one** nightly (`a3561ef8e`,
   headline: a Mistral3 image-placeholder-grid fix, irrelevant here) — a day,
   not a backlog. Cheap enough to be a real option if DFlash2 proves out.
3. Any DFlash2 measurement must run on `0539b7e1` until upstream fixes this.

### Quantized DFlash drafts are structurally unsupported in vLLM

Tested on `0539b7e1` (the image *without* the regression above), the W4A16
draft still fails — with a different, deeper error:

```
AttributeError: 'QKVParallelLinear' object has no attribute 'weight'
  qwen3_dflash.py:490, in _build_context_kv_buffers
    kv_weights = [a.qkv_proj.weight[a.q_size :] for a in layers_attn]
```

DFlash builds its fused KV buffers by reading the draft's raw `.weight` tensor.
A compressed-tensors layer has no `.weight` — it has `weight_packed`,
`weight_scale`, `weight_shape`. So **no quantized DFlash draft can load on any
image**; the `decoder_layer_cls` regression was merely masking this. Not
gfx1201, not the checkpoint.

Note this also makes the plan's earlier "quantizing the draft is safe for
correctness by construction" argument moot: correctness was never the
obstacle — loading is. Worth an upstream feature request, but it is not a
blocker we can route around.

### Original framing (kept — still the protocol once unblocked)

## Step 1 — DFlash2 retest, gated on an explicit grammar-concurrency stress test

DFlash2 (PR #4651) showed a genuine win on real mixed traffic (TPOT -42%,
throughput +55-119%) before being reverted (PR #4652) for VRAM instability
under load, root cause not isolated at the time.

**Current production baseline** (verify against the manifest before trusting
any of this — it changes): `--kv-cache-memory` is **9663676416 (9Gi)**,
buying a 287,159-token pool at 1.30x concurrency multiplier, with 2.57GB free
VRAM measured at peak load. The live `maxModelLen` (246,944) is derived from
this same 9Gi pool, at a ~1.17x pool/ceiling ratio — the two numbers aren't
independent; changing the pool means re-deriving the ceiling from it, not
reusing 246,944 (see the retest protocol below for why this can't be a fixed
formula once `parallelSlots` also changes). The manifest's own comment
records that the *prior* 7Gi config left *more* headroom — 2.81GB free,
223,172-token pool (1.01x) — because a smaller KV-cache-memory budget
allocates less VRAM to KV
before DFlash2 enters the picture at all.

Investigation since the revert:
- The specific upstream bug initially suspected (an illegal-memory-access
  bug in FlashAttention's split-KV combine kernel, `vllm-project/vllm#52816`
  comment thread) **does not apply to our stack** — verified directly against
  `kubernetes/apps/ai/llmkube/models/qwen38-27b-vllm.yaml:248-253`: our KV
  connector forces `TRITON_ATTN`, never FlashInfer or the CUDA FlashAttention
  combine kernel named in that bug.
- The more likely cause is config arithmetic, and the numbers now confirm it:
  dropping to 7Gi alone buys back only ~0.24GB of free VRAM (2.81 vs 2.57GB),
  but #4651 additionally raised `parallelSlots` 6→8 in the same change. The
  revert PR recorded free VRAM falling under 500MB — DFlash2's draft model +
  per-slot scratch buffers, multiplied by the extra concurrency, consumed
  at least ~2.3GB beyond what the KV-memory cut freed. This is a real
  oversubscription, not a mystery.
- **New finding from this review round, not previously considered:** DFlash2
  has never been stress-tested against the specific failure mode that killed
  MTP — concurrent grammar-constrained tool-calling. The reverted 2.5h
  production run was low-concurrency and bursty; it likely never entered the
  regime that wedged MTP to 0.2 tok/s. DFlash2 is **not proven safe** from
  this, only untested against it.

**The trade this retest is actually proposing:** DFlash2 needs real KV-pool
budget freed for its own use, and that's a real cost of the feature, not a
free move — #4651's 9Gi→7Gi cut cost ~22% of the pool (287,159→223,172
tokens, concurrency multiplier 1.30x→1.01x). Whether the actual cut needs to
be that deep is exactly what the sizing boot below settles — don't assume
7Gi is the right number until it's measured for the draft variant chosen.
The ~1.17x pool/ceiling
ratio is specific to the *current* 9Gi/no-spec-decode config
(287,159/246,944 ≈ 1.16) — it is not a fixed constant to reapply. The
numbers don't hold once `parallelSlots` also changes: #4651's own 7Gi attempt
(223,172-token pool, `maxModelLen` dropped to 147,456, `parallelSlots` 6→8
in the same change) works out to 223,172/147,456 ≈ 1.51, a different ratio
entirely. **Do not apply a fixed ratio formula.** Re-derive `maxModelLen`
directly from the manifest and the sizing boot's profiler result for
whichever draft variant and `parallelSlots` value is actually selected, not
by scaling 246,944 by any ratio. Any `maxModelLen` change cascades to
litellm's `maxInputTokens` and Hermes' `context_length`, per the coupling
documented in `vllm-optimization-log-2026-08.md` — re-derive all three
together, don't change kv-cache-memory in isolation.

**Prerequisite: size the actual scratch cost before picking a kv-cache-memory
cut — don't reuse the old 7Gi figure blindly.**

The draft checkpoint's static weight footprint is directly measurable and
already surprising: `z-lab/Qwen3.8-27B-DFlash2/model.safetensors` is
**3,848,817,896 bytes (~3.58GiB, bf16)** — it doesn't carry its own
embedding/lm_head copies at the full 248,320-token vocab (those are shared
with the target at runtime), but 3.58GiB of static draft weights is still
larger than the ~2GiB the 9Gi→7Gi cut nominally frees, before any per-slot
scratch (candidate buffers, aux-hidden-state capture, conv state) is counted.
This is a plausible explanation for why that cut measured only 0.24GB of
actual free-VRAM gain instead of the full ~2GB the flag value changed by —
something was already eating into it.

Get the real number from a boot, not more arithmetic: deploy a standalone
throwaway pod (same pattern as the minisglang-rdna4 test — a Pod outside the
production InferenceService, not a config change to it) running the DFlash2
speculative-config **without** `--kv-cache-memory` set, so vLLM's own
profiler runs and logs the actual breakdown (target weights / draft weights
/ KV pool it chooses / non-KV reserved). Capture that log, delete the pod.
No sustained serving needed — this is a startup-profiling capture, minutes
not hours, though the GPU still needs to be briefly freed (single shared
card) the same as any other test here.

**In the same pass, compare a quantized draft against the bf16 default.**
Weight sizes measured from the HF API 2026-08-23, not estimated:

| checkpoint | weights | note |
|---|---|---|
| `z-lab/Qwen3.8-27B-DFlash2` (bf16) | 3,848,817,896 B (3.58 GiB) | baseline drafter |
| `syvai/Qwen3.8-27B-DFlash2-W4A16` | 1,280,633,960 B (**1.19 GiB**) | drafter, compressed-tensors |
| `lued/Qwen3.8-27B-INT8-W8A16-DFlash2` | 29,535,195,512 B (**27.51 GiB**, 6 shards) | **NOT a drafter** |

**The W8A16 repo is not a draft model** — at 27.51 GiB across 6 shards it is a
quantized full 27B *target*, despite the DFlash2 name. An earlier draft of this
plan listed it as a draft candidate; it is not one and must not be used as one.

That leaves W4A16 as the only real quantized drafter, and it is the one worth
testing: **1.19 GiB vs 3.58 GiB saves 2.39 GiB**, which is on the same order as
the ~2 GiB the 9Gi→7Gi KV cut was buying — i.e. it could avoid most of that cut
outright. **Existence and size confirmed; ROCm/gfx1201 support is NOT
confirmed** — treat it as unvalidated until the sizing boot actually loads and
runs it; a checkpoint existing on the Hub says nothing about whether its kernels
work on RDNA4, and it has no track record here. Speculative-decode verification stays exact against the target
regardless of draft precision — a worse draft only costs *acceptance rate*
(less speedup), never wrong output — so quantizing the draft is safe for
correctness by construction, but that's a claim about the algorithm, not
about whether these specific checkpoints load and run correctly on gfx1201.
What it changes if it works: W4A16 would take the ~3.58GiB draft down to
roughly ~1GiB, which could avoid most of the kv-cache-memory cut entirely.
**Prefix sharing between target and draft is already on; there is nothing to
configure.** Verified against the running build, not docs:
`vllm/v1/spec_decode/dflash.py:327-328` defaults `use_aux_hidden_state` to
`True`, so the drafter consumes the target's aux hidden states EAGLE3-style
instead of re-prefilling the prefix itself. There is no separate
automatic-prefix-caching (APC) knob for the drafter — `grep -r prefix_cach`
across `v1/spec_decode/` and `config/speculative.py` returns zero matches;
`--enable-prefix-caching` is engine-global and already set in production.

**Boot-time failure mode to watch for:** the same file errors out if the
attention backend lacks **non-causal** support, with a message pointing at
FlashAttention. We run TRITON_ATTN (forced by the KV connector, per
`rocm.py:703`). #4651 ran DFlash2 in production for 2.5h, so TRITON_ATTN
evidently satisfies this — but a boot that dies here is that assumption
breaking, not a mystery.

Run the sizing boot once per candidate draft variant (W4A16 first, bf16 as the
comparison baseline) — a load failure or crash on a given variant is itself a
valid, useful result, not just a successful measurement — and fold the same
4-6 concurrent grammar-burst comparison from steps 2-3 below into each for
any variant that does load: record **draft acceptance rate** alongside
PP/TG/TPOT this time, not just the first time, since acceptance
rate is the number a quantized draft can actually move. Pick whichever
variant clears the burst-test gate with the smallest KV-pool cost.

**Retest protocol (all of these, not a subset):**
1. `parallelSlots` back to **6** (not 8) — this is the actual overspend from
   #4651, revert it. `num_speculative_tokens` **5** (not 7). Size
   `kv-cache-memory`'s cut from the prerequisite sizing boot above (for
   whichever draft variant was chosen), not by reusing the old 7Gi figure —
   and re-derive `maxModelLen`/litellm `maxInputTokens`/Hermes
   `context_length` together for the resulting pool.
2. **Control run first, same day, same traffic shape:** run the identical
   4-6 concurrent grammar-constrained tool-calling burst against the
   *current, unmodified* baseline config and record PP tok/s, TG tok/s, and
   TPOT — not just a vibe check. Without this, a cliff in step 3 is
   unattributable (baseline-before-tuning discipline).
3. Then run the same burst against the DFlash2 config, before any real
   Hermes traffic touches it. Compare against step 2's control on the same
   three metrics. If it wedges (MTP-style tok/s cliff) or regresses sharply
   vs. the control, DFlash2 is disqualified the same way MTP was — full stop,
   revert.
4. Only if step 3 passes: allow real traffic, monitoring **minimum observed
   free VRAM** (not an average, and not p99 of a "free" metric — p99 of a
   free-space series is the *high* tail, the wrong direction for a risk
   signal; use the minimum, or equivalently p99 of *used* VRAM).
5. Deliberately trigger a Jellyfin transcode *during* the retest — the 2GiB
   reserve is only real if Jellyfin can actually claim it against vLLM's
   already-eager allocation. #4651's original test plan never exercised this.
6. Pre-committed abort criterion, written down before starting, not decided
   live: the `DgpuVramLow` alert itself fires at free VRAM < 2 GiB sustained
   10 minutes. Abort on **minimum free VRAM < 2.3 GiB** (a ~0.3GiB margin
   above that hard floor, and below the 2.57-2.81GB range observed as normal
   at baseline) — don't wait for the alert's 10-minute window, and don't
   attempt live tuning in production if it trips (that's what cost the
   previous window — see memory: `feedback-patience-inside-downtime-window`
   for the adjacent lesson about not cutting live *tests* short, and apply
   the same discipline here in reverse: don't linger past a pre-committed
   *abort* line either). Revert path: a revert PR mirroring #4652 — this
   config is Flux-managed, not a live-pr-test-style kubectl patch.
7. Expectation-setting: our real workload is often prefill-bound (129:1),
   and DFlash2 primarily accelerates decode. The end-to-end win in practice
   will likely be smaller than the -42% TPOT headline — budget for that
   rather than treating a smaller real gain as a failure.

**Gate:** none — independent of everything resolved above. Can run whenever
there's a suitable window; give it real patience once started (see the linked memory above),
don't self-impose a downtime cap that isn't in this protocol.

## Step 2 — minisglang-rdna4: bounded spike only, not a production candidate

`ghcr.io/patcarter883/minisglang-rdna4` — retry the live test aborted this
week (aborted prematurely at ~25 min during what was likely legitimate
first-run kernel JIT compilation, a process error, not a technical dead
end). Give it **45-60 minutes** of patience this time before any verdict.

Scope this strictly as "does it boot and serve a coherent response" — not a
production evaluation. The maintainer's own validation (TP=2 on 16GB cards,
`cyankiwi/Qwen3.6-27B-AWQ-INT4`) doesn't transfer to our TP=1/32GB/Qwen3.8
setup, and a clean boot wouldn't come close to justifying a swap away from
the tuned vLLM stack (hierarchical KV offload, 92-93% prefix-cache hit rate,
246,944 ctx, working tool parser) — none of which minisglang-rdna4 has been
shown to replicate.

**Gate:** none required, but sequence last — it's the least likely to pay
off and costs a full downtime window per attempt, same idle-window scarcity
problem as before.

## Step 3 — Official SGLang gfx1201 image: watch only

No official image exists (only gfx942/gfx950 CDNA targets). The community
fork (`mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference`) is active but
trails upstream (v0.5.17-era as of this writing, v0.5.18 is current
upstream). Nothing to do here now — revisit if/when an official or
actively-maintained community image targeting gfx1201 appears.

## Explicitly deprioritized / not pursuing

- Raising `parallelSlots` or `maxNumBatchedTokens` further, or general
  high-concurrency throughput tuning — optimizes a traffic shape (sustained
  high QPS) we don't have, and `maxNumBatchedTokens` in particular already
  has a measured, confirmed-correct ceiling (4096).
- Re-attempting MTP — root-caused and disqualified, not worth re-litigating
  without a structural change (e.g. reduced context) that isn't currently
  planned.
- KV-cache quant/quality sweeps and other never-validated-but-not-broken
  open questions in `vllm-optimization-log-2026-08.md` — real gaps, but
  lower expected value than the steps above; tracked there, not duplicated
  into this plan.

## Process Instructions

- After completing each step, update this plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of
  the plan have been consolidated into existing documentation, the plan file
  can be removed. If there is no relevant existing documentation, the plan
  should be reworked into a reference document.

**Important**: Every prompt should verify the branch and worktree before
doing any work.
