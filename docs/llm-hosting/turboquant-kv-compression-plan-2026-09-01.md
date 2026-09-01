# Plan: evaluate TurboQuant KV compression on qwen38-27b-vllm

**Status:** not started
**Branch:** `feat/turboquant-kv`
**Worktree:** `.claude/worktrees/turboquant-kv`
**Target:** `qwen38-27b-vllm` InferenceService, namespace `ai`, control-1 (R9700, gfx1201)
**Shape:** config change, GitOps. No source patch.

## Why this and not another kernel swap

KV capacity is the binding constraint behind nearly every dead end in the
2026-08-31/09-01 sessions:

- DFlash2 draft (1.19 GiB) did not fit in 1.05 GiB spare VRAM.
- `maxModelLen` is capped at 246,944 by the 1.17x pool ratio.
- Concurrency at 50K prompts is pool-limited to ~5 (6 x 50K = 300K > 288,508),
  which is why `parallelSlots` 5-vs-6 turned out to be a non-question.
- AITER unified attention, soaked from 2026-09-01, measured parity - it moves
  no capacity at all.

TurboQuant is the only lever found that moves the constraint itself.

## Measured, not assumed

`TurboQuantConfig.from_cache_dtype(<dtype>, head_size=256).slot_size_aligned`,
read from the running engine. Current `fp8_e4m3` stores K and V at 1 byte per
element = 256 + 256 = **512 bytes** per head per position.

| dtype | slot bytes | vs fp8 | projected pool (from 288,508) |
|---|---|---|---|
| `turboquant_k8v4` | 388 | 0.76x | ~380K tok |
| `turboquant_4bit_nc` | 262 | 0.51x | ~563K tok |
| `turboquant_k3v4_nc` | 230 | 0.45x | ~643K tok |
| `turboquant_3bit_nc` | 198 | 0.39x | ~746K tok |

Projections scale the token count by the slot ratio and are indicative only -
the real pool also depends on block accounting. **Re-read `GPU KV cache size`
from the engine log; do not carry these numbers forward as results.**

Note the module docstring shows `[100 bytes key | 512 bytes value] = 612` for
`turboquant_k3v4_nc` at head_dim 256, which reads as fp16 values and *larger*
than fp8. That is not what `slot_size_aligned` returns (230). The docstring
describes an unaligned illustrative layout; trust the computed value.

## Compatibility, verified against our model

| check | result |
|---|---|
| `supports_head_size(256)` | yes |
| `supported_dtypes` | fp16, bf16 - we run bf16 |
| `get_supported_kernel_block_sizes` | 16 / 32 / 64 / 128 |
| `supports_attn_type` | DECODER only |
| `supports_sink` / `supports_sliding_window` | False / False - we use neither |
| `supports_kv_connector()` | **True** |

## The hard constraint: mutually exclusive with the KV offload connector

`TurboQuantAttentionBackend.supported_kv_cache_layouts()` returns **`LBNHC`
only**. `OffloadingConnector.get_required_kvcache_layout()` returns **`LBHNC`**.
N and H transposed - a real data-layout incompatibility, *not* the missing
`supports_kv_connector` override that gated AITER. There is no one-line fix.

So this is a trade, not an addition:

- **Give up:** both offload tiers (CPU 22Gi + fs `/kvoffload`). Current external
  tier contributes 15.9% on the 17% the GPU cache misses; combined 85.7%
  (GPU 83.0%), against 91.2% documented before the tier was rebuilt.
- **Get:** 1.3x - 2.6x more GPU KV, depending on dtype.

The counter-argument worth testing rather than assuming: a 2x GPU pool should
raise the GPU prefix-cache hit rate on its own, shrinking what the external tier
was contributing. Whether the net is positive is the whole question.

**Also mutually exclusive with the AITER soak.** Both are DECODER backends;
exactly one is selected. Starting this ends that soak - let it finish first.

## Trap: `turboQuantBits` is not the lever

llmkube's InferenceService exposes `turboQuantBits`, but its own description says
*"for the oMLX runtime (3, 6, or 8)"*. We run `runtime: vllm`. Setting it does
nothing here. The vLLM lever is `--kv-cache-dtype <turboquant_*>` via
`extraArgs`, replacing `kvCacheDtype: fp8_e4m3`.

## Steps

### Step 1 - Do not start until the AITER soak concludes

Record its verdict first. If AITER is kept, note that this plan removes it (both
cannot be selected); if reverted, this starts from the TRITON_ATTN baseline.

### Step 2 - Baseline, gated on an idle engine and idle GPU

Both PP and TG, never one alone. Capture before any change:

- `bench/concsweep.py 8000 qwen-3.8` - decode, one warmup discarded, then 2 runs
- engine-reported `Avg prompt throughput` for prefill - **not** TTFT-derived PP
  from `pp1.py`, which reads ~950 where the engine reads ~6390 because TTFT here
  is dominated by offload-lookup and admission
- `GPU KV cache size` and the 1.17x concurrency line from the engine log
- combined cache: `gpu + (1-gpu)*ext`, both tiers, from the log's two hit-rate lines
- **a fixed 20-prompt quality set, greedy, outputs saved** - see Step 4

### Step 3 - Target: `turboquant_4bit_nc`

Chosen deliberately over the conservative `k8v4`. Presets, from
`turboquant/config.py` (`_nc` = **norm correction**, not "no calibration"):

| preset | key bits | value bits | norm_correction |
|---|---|---|---|
| `turboquant_k8v4` | 8 (FP8) | 4 | False |
| `turboquant_4bit_nc` | 4 (MSE/Lloyd-Max) | 4 | True |
| `turboquant_k3v4_nc` | 3 | 4 | True |
| `turboquant_3bit_nc` | 3 | 3 | True |

vLLM's own study finds `k8v4` "offers no significant advantage over FP8" - it
costs throughput for capacity we can nearly get from what we already run. The
aggressive tiers are ruled out: `k3v4_nc` drops mrcr 45.8 -> 33.5 on Qwen3-30B
and both it and `3bit_nc` show ~20-point drops on hard reasoning tasks. That
leaves `4bit_nc` as the only preset worth the disruption.

Changes, all in `qwen38-27b-vllm.yaml`:
- remove `kvCacheDtype: fp8_e4m3`, add `--kv-cache-dtype turboquant_4bit_nc`
- remove the `--kv-transfer-config` block entirely
- keep `--kv-cache-memory 9663676416` so the pool change is attributable to the
  dtype alone
- keep the `kv-offload` PVC and `dshm` volume **defined but unmounted** so the
  revert is a one-line change, not a restore

### Step 3b - Published numbers, so we know what we are checking against

**Accuracy** (vLLM study, `4bit_nc`): 96% recovery on reasoning benchmarks;
long-context mrcr on Qwen3-30B BF16 45.8 -> FP8 43.1 -> `4bit_nc` 42.3. Against
**fp8**, which is what we actually run, that is ~0.8 points, not the 1-4 headline
(the headline is measured against BF16, which this deployment gave up long ago).

AMD tested **Qwen3.5 35B** - our family - and found it *"robust: hybrid attention
(75% linear layers) limits KV compression exposure"*. That is the single most
encouraging datapoint for this change, and it is architecture-specific rather
than a general claim.

**Throughput is the real cost, and it inverts the usual risk ordering:**

| source | figure |
|---|---|
| vLLM, `4bit_nc` latency overhead | **15-60%** |
| vLLM, TQ throughput vs BF16 | 75-80% |
| AMD, **stock open-source vLLM kernels** | **32% of BF16 throughput** |
| AMD, with custom Triton/HIP/FlyDSL kernels | 88-95% |

We run stock vLLM, so the 32% figure is the one that applies to us, not AMD's
headline. **Assume this makes us slower per token and size the experiment to
find out whether capacity repays it.**

**Not transferable:** every AMD number is MI355X (CDNA). No gfx12/RDNA4, no
Radeon, anywhere in their testing. Our hardware is untested for this feature.

**Why run it anyway:** AMD's agentic result - TTFT 13.9s -> 0.89s, cache hit rate
5.3% -> 67.7% - is our workload shape (long multi-turn agentic, KV-bound). Our
combined cache is 85.7%, concurrency is pool-limited to ~5 at 50K prompts, and
context is capped at 246,944. If capacity is genuinely the binding constraint, a
~2x pool can outweigh a 20-25% per-token cost. If it is not, this is pure loss.
That is the question the experiment answers.

### Step 4 - Quality gate (blocking)

This is a quantization change, so output quality gates everything downstream -
throughput numbers are meaningless if answers degrade.

- Same 20 fixed prompts from Step 2, greedy (`temperature 0`, `top_p 1`, seed 0).
- Compare against the Step 2 baseline outputs.
- Different KV dtypes are **not** bit-identical, so exact match is the wrong
  gate. Establish the baseline's own run-to-run agreement first and use that as
  the floor - the same control that caught the cold-vs-warm error in the
  `parallelSlots` work.
- Read several full outputs by hand. Degenerate repetition, truncation, or
  drift-then-recover are the signatures of degraded KV and hide inside an
  aggregate score.

Context for the bar: we already run fp8 KV **uncalibrated at scale 1.0**, which
vllm#54623 measures at 92.3% GSM8K vs 93.7% calibrated and 94.5% bf16. We are
already spending ~1.4 points there. Spending more without measuring would be
trading accuracy for capacity blind.

**If this gate fails, revert and stop.** Do not proceed to benchmarks.

### Step 5 - Capacity and throughput (co-equal gate, not a formality)

Given the 32%-of-BF16 figure for stock kernels, treat a throughput regression as
disqualifying on its own terms, not as a footnote to the capacity win.

- Re-read `GPU KV cache size` - the actual pool, not the projection above.
- Re-run Step 2's decode and prefill measurements, same protocol.
- Compare combined cache honestly: the offload tiers are **gone**, so this is
  GPU-only against the previous combined 85.7%. A GPU-only rate above ~85.7% is
  the win condition; below it means the tiers were carrying more than the extra
  pool replaces.

### Step 6 - Re-derive the coupled invariants together

Three separate incidents came from doing exactly one of these:

- `maxModelLen` from the new pool via the 1.17x ratio (bisected 2026-08-19)
- litellm `maxInputTokens` + `maxOutputTokens` == `maxModelLen`, both
  `qwen-3.8` and `qwen-3.8-fast`
- Hermes `context_length` and `max_concurrent_sessions` (<= pool / typical prompt)

Hermes config lives on its PVC, not in git - it does not move with a revert.

### Step 7 - Revert criteria, decided up front

Revert if any of: the quality gate fails; GPU-only combined cache lands below
85.7%; decode or prefill regress beyond run-to-run noise; `DgpuVramLow` fires.

Revert = restore `kvCacheDtype: fp8_e4m3`, restore `--kv-transfer-config`,
remount the tiers, re-derive Step 6 back. The offload PVC is preserved
throughout, but its **content** is not - expect a cold tier and hours of
degraded admission after reverting, the same cost seen on 2026-08-31.

## Open questions this plan does not answer

- TurboQuant on a **GDN hybrid** is only partly evidenced. vLLM's study states
  it "supports only models with standard attention mechanisms (e.g. GQA)" and
  none of its four test models were hybrid; AMD separately reports Qwen3.5 35B
  working well. Those are in tension. Our model interleaves mamba layers with
  their own cache (`mamba_cache_dtype`), so verify it loads and serves before
  trusting any number.
- **RDNA4 / gfx1201 is entirely untested upstream** for this feature.
- Whether prefill uses the TurboQuant path at all, or only decode.
- Whether losing the fs tier changes cold-start behaviour after a restart.

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of the plan have been consolidated into existing documentation, the plan file can be removed. If there is no relevant existing documentation, the plan should be reworked into a reference document.

**Important**: Every prompt should verify the branch and worktree before doing any work.

---

# RESULT — executed 2026-09-01, WORKS on gfx1201, REVERTED on measurement

**Status: TurboQuant runs on gfx1201. Capacity claim confirmed. Rejected because
the capacity it buys is capacity this deployment is configured not to use.**

## It took three stacked patches to boot at all

Upstream does not support TurboQuant on gfx1201, and each blocker only became
visible after clearing the previous one:

1. **`supports_kv_connector() -> False`** inherited by
   `RocmAiterUnifiedAttentionBackend` — already merged as #4816. Not strictly
   needed for TurboQuant (which declares True) but present in the live stack.
2. **Segfault in `ck_tile::FmhaFwdKernel`** via `mha_varlen_fwd` during TurboQuant
   prefill at head_dim 256. `fa_utils.py` picks the flash-attn implementation by
   arch: `if on_gfx1250(): aiter.ops.triton.mha else: flash_attn` (CK). gfx1201
   takes the CK branch and it segfaults. Fixed by an import hook rebinding
   `fa_utils.flash_attn_varlen_func` to `aiter.ops.triton.attention.mha`.
   Verified safe first: signature covers every kwarg TurboQuant passes, and
   `get_flash_attn_version(head_size=256)` returns None so the `fa_version`
   kwarg Triton does not accept is never sent.
3. **`FileNotFoundError: aiter/ops/triton/configs/gfx1201-MHA-DEFAULT.json`.**
   AITER ships MHA tuning configs for gfx1151 / gfx1250 / gfx942 / gfx950 only.
   Supplied by mounting the **gfx1151** config under the gfx1201 name — same RDNA
   family, 32-wide wavefronts (gfx942/950 are CDNA at 64-wide). Triton still
   compiles for the real arch; only tile tuning is borrowed, so the decode
   numbers below are on an untuned config.

With all three, the engine reached `1/1 Running`, zero restarts, and served
correct output (`finish_reason: stop`, coherent completion).

## Measured

Both arms: warmup discarded, two measured runs, idle engine, `dev199+g7c5dc571c`.

| metric | fp8_e4m3 + TRITON + offload | TurboQuant 4bit_nc | delta |
|---|---|---|---|
| GPU KV pool | 288,508 tok | **552,612 tok** | **+92%** |
| concurrency @ 246,944 | 1.17x | **2.24x** | +92% |
| prefill (engine-reported) | ~6390 tok/s | **6378.7 tok/s** | **parity** |
| decode conc-1 | 31.49, 31.68 | **14.54, 14.55** | **-54%** |
| decode conc-8 | 53.90, 64.97 | 43.98, 44.36 | -26% |
| decode conc-16 | 78.22, 77.03 | **57.10, 56.39** | **-27%** |
| KV offload tiers | present | **lost** | layout LBNHC vs required LBHNC |

The +92% pool matches the slot-size arithmetic predicted before the run
(0.51x slot -> 1.95x pool) to within 2%. **Prefill is untouched**, which was the
main worry going in and turned out not to be the problem.

## Why it was rejected

Not because the numbers are bad — because the win lands where we cannot spend it.

A single 50K request: prefill stays ~7.8s, but ~1200 decode tokens go from ~38s
to ~82s. **Request latency roughly doubles.** Against that, the 2.24x concurrency
is unusable as configured: Hermes runs `max_concurrent_sessions: 5`, and the
extra headroom only pays above that. We would pay 27-54% decode for capacity we
have deliberately capped ourselves out of using.

**This verdict is conditional, and flips if either changes:**
- Hermes's session cap is raised well past 5, or
- context needs to grow substantially beyond the current 246,944 ceiling.

Then a ~2x pool is real value and the decode cost buys something.

## NOT measured — do not read this as complete

- **Output quality at 4-bit.** Never tested. Published figures suggest ~0.8
  points vs fp8 on long-context mrcr and AMD found Qwen3.5's hybrid attention
  tolerant, but we have no measurement of our own. The plan's Step 4 quality
  gate was never reached, because the throughput result disqualified the change
  first.
- **Decode with a tuned gfx1201 MHA config.** The numbers above use the gfx1151
  config. A real gfx1201 tuning could narrow the decode gap; how much is unknown.

## Operational lesson: suspending a child Kustomization does not hold

Three CR patches were silently reverted mid-test before the cause was found:
`llmkube-models` is itself defined in git and reconciled by the parent
`flux-system` Kustomization, which resets `spec.suspend` back to the git value.
`kubectl patch kustomization llmkube-models --type merge -p '{"spec":{"suspend":true}}'`
therefore holds only until the parent next reconciles.

Durable live-patching of this app requires suspending **both** levels — and
`flux-system` suspended means *nothing in the cluster* reconciles, which is a far
larger blast radius than one app and must not be left running. Both were resumed
immediately after measurement. Prefer a git commit over live patching for
anything that needs to survive more than a few minutes.

## Upstream value

gfx1201 is untested territory for TurboQuant — every published AMD result is
MI355X (CDNA). Two reportable defects, both with confirmed workarounds:

- CK `mha_varlen_fwd` segfaults at head_dim 256 on gfx1201; AITER's Triton MHA
  works and is gated to gfx1250 for no reason that applies here.
- No `gfx1201-MHA-DEFAULT.json` ships, so the Triton path cannot load even when
  selected.
