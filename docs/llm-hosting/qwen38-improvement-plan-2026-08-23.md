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
  turns. External prefix-cache hit rate recently measured at 85%.
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
Two independent reviews (below) agreed the highest-value, lowest-risk moves
are the ones that improve *our actual traffic shape* (prefix-cache
efficiency, prefill cost) before touching anything that trades GPU-side
complexity for a win that mostly shows up in a different regime. Sequence
below goes cheapest/safest → most disruptive, with later steps gated on
earlier ones where that's cheap to check.

## Step 1 — Instrument TTFT by prefix-cache outcome (near-zero risk, one small config prerequisite)

**Not a job for `ttftsweep.py` as-is** — that script deliberately salts every
prompt to *defeat* prefix caching (it's a cache-cold-only tool for
`maxNumBatchedTokens`-style sweeps), so it structurally cannot produce a
cache-hit sample.

**Prerequisite, corrected from the original draft of this plan:** the manifest
does not currently pass `--enable-prompt-tokens-details`, which vLLM defaults
to `False` — meaning `prompt_tokens_details.cached_tokens` is absent from
production responses entirely right now, not just unmeasured. This step
therefore isn't the zero-config-change step it was first written as: flip
that flag on first (small, low-risk, additive-only — it only adds a field to
the usage payload, changes nothing about serving behavior), then correlate
per-request TTFT against `prompt_tokens_details.cached_tokens` from vLLM's
own request logs/metrics over a fixed recent window.

**Bucket thresholds, made executable, not a vague `≈`:**
- **Full hit**: `cached_tokens / prompt_tokens >= 0.95`
- **Partial hit**: `0 < cached_tokens / prompt_tokens < 0.95`
- **Miss**: `cached_tokens == 0`, or the field is present with `cached_tokens: 0`
- **Unavailable**: `prompt_tokens_details` missing from the response entirely
  (shouldn't occur once the flag above is set, but don't silently coerce a
  missing field to a miss if it does — that conflates "we don't know" with
  "confirmed cold")

Also: **the 85% figure is a token-level hit rate, not "15% of requests
miss"** — don't conflate the two when writing up results; a request can be a
majority-cached partial hit and still pay real prefill cost on the uncached
remainder.

Query this via the existing datasource-proxy validation path, not a new
Grafana dashboard — dashboard creation is blocked (403 on `dashboards:create`
in this Grafana instance).

**Gate:** none — do this first regardless of what else gets picked up.

## Step 2 — Hermes-side prefix stabilization (gated on Step 1)

If Step 1 shows cache misses dominate p95 TTFT: investigate whether Hermes'
system-prompt / tool-schema serialization order is stable turn-to-turn. An
unstable prefix (e.g. tool list reordered, timestamp/nonce injected near the
front) defeats prefix caching for reasons that have nothing to do with the
engine. This is a free win (zero VRAM cost, no GPU-side risk) if it's the
cause.

**Gate:** only pursue if Step 1's data supports it — don't guess.

## Step 3 — Reassess the fs-tier KV-offload cost/benefit

We already have a root-caused, documented issue with the fs secondary
offload tier: lookups serialize through a single background thread per tier
and have caused `vllm:kv_offload_lookup_async_delay_seconds` spikes up to
373s under memory pressure (see memory: `vllm-kvoffload-lookup-stall`, and
`vllm-optimization-log-2026-08.md`'s note that the tier is "load-bearing for
decode speed, not just its hit rate").

**Decision metrics, not a vague comparison:** the fs tier's contribution to
the *combined* hit rate (`combined = gpu + (1-gpu) * ext`, per the
methodology lesson in `vllm-optimization-log-2026-08.md` — never read either
tier alone) versus `kv_offload_lookup_async_delay_seconds` p99 over the same
window. Do NOT use either tier's `usage_perc` gauge — already documented as
measuring active/pinned blocks, not cached content, and has produced two
wrong conclusions before. If the fs tier's combined-hit-rate contribution is
small relative to the CPU tier alone, and the p99 lookup delay is
recurring (not a one-off memory-pressure event), that's grounds to drop the
tier; if the contribution is large, the tax is probably worth it and the fix
belongs upstream (single-thread lookup serialization) rather than here.

**Gate:** independent of Steps 1-2; can run in parallel with them. Pure
measurement, no config change until a decision is made.

## Step 4 — DFlash2 retest, gated on an explicit grammar-concurrency stress test

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
**Correction from the original draft of this plan:** the ~1.17x pool/ceiling
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
Community quants exist on Hugging Face — checked 2026-08-23, both repos
present: `lued/Qwen3.8-27B-INT8-W8A16-DFlash2` and
`syvai/Qwen3.8-27B-DFlash2-W4A16` are the closest match to our existing
compressed-tensors/AWQ pattern. **Existence confirmed; ROCm/gfx1201 support
is NOT confirmed** — treat both as unvalidated until the sizing boot actually
loads and runs them; a checkpoint existing on the Hub says nothing about
whether it loads cleanly on this stack, and neither has any track record
here. Speculative-decode verification stays exact against the target
regardless of draft precision — a worse draft only costs *acceptance rate*
(less speedup), never wrong output — so quantizing the draft is safe for
correctness by construction, but that's a claim about the algorithm, not
about whether these specific checkpoints load and run correctly on gfx1201.
What it changes if it works: W4A16 would take the ~3.58GiB draft down to
roughly ~1GiB, which could avoid most of the kv-cache-memory cut entirely.
Run the sizing boot once per candidate draft variant (bf16 baseline, W4A16,
optionally W8A16) — a load failure or crash on a given variant is itself a
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

**Gate:** independent of Steps 1-3. Can run whenever there's a suitable
window; give it real patience once started (see the linked memory above),
don't self-impose a downtime cap that isn't in this protocol.

## Step 5 — minisglang-rdna4: bounded spike only, not a production candidate

`ghcr.io/patcarter883/minisglang-rdna4` — retry the live test aborted this
week (aborted prematurely at ~25 min during what was likely legitimate
first-run kernel JIT compilation, a process error, not a technical dead
end). Give it **45-60 minutes** of patience this time before any verdict.

Scope this strictly as "does it boot and serve a coherent response" — not a
production evaluation. The maintainer's own validation (TP=2 on 16GB cards,
`cyankiwi/Qwen3.6-27B-AWQ-INT4`) doesn't transfer to our TP=1/32GB/Qwen3.8
setup, and a clean boot wouldn't come close to justifying a swap away from
the tuned vLLM stack (hierarchical KV offload, 85% prefix-cache hit rate,
246,944 ctx, working tool parser) — none of which minisglang-rdna4 has been
shown to replicate.

**Gate:** none required, but sequence last — it's the least likely to pay
off and costs a full downtime window per attempt, same idle-window scarcity
problem as before.

## Step 6 — Official SGLang gfx1201 image: watch only

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
  lower expected value than Steps 1-4 above; tracked there, not duplicated
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
