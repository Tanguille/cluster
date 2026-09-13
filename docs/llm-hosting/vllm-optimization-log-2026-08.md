# vLLM qwen38-27b-vllm optimization log — 2026-08-18 onward

Live-patch tuning history for the `qwen38-27b-vllm` InferenceService (R9700,
gfx1201). **The current production config and its per-line rationale live as
inline comments in `kubernetes/apps/ai/llmkube/models/qwen38-27b-vllm.yaml` —
read that first.** This log is kept for two things the manifest comments
don't carry: the negative results (so they aren't retried), and the
methodology lessons below (so future tuning passes don't repeat the same
measurement mistakes).

Superseded/later work not in this log: DFlash2 spec-decode was attempted and
reverted 2026-08-22 (PR #4651/#4652, VRAM instability under load, root cause
not isolated) — see the PR history, not this file.

**DFlash2 was then disqualified outright on VRAM, 2026-08-23. Do not revisit.**
A sizing boot on `0539b7e1` without `--kv-cache-memory` (the flag pins the pool
and makes the profiler's ceiling report meaningless) had vLLM budget essentially
the whole 32 GiB card: 22.52 GiB weights + non-torch, 2.80 peak activation,
0.41 CUDAGraph, leaving **5.92 GiB max KV** against the 9 GiB production pool.
Subtract the mandatory 2 GiB Jellyfin reserve and DFlash2 leaves ~3.9 GiB — a
56% pool cut, `maxModelLen` near 107K, **below Hermes' measured 112K peak**.
The 4.31 GiB delta over the 18.21 GiB target checkpoint is ~3.58 GiB of *static
draft weights*; draft `kv_cache_dtype` and draft `max_model_len` only touch draft
KV, so there is no knob. Quantizing the draft is not a route either: DFlash builds
its fused KV buffers from the draft's raw `.weight` tensor
(`qwen3_dflash.py:490`), which a compressed-tensors layer does not have, so **no
quantized DFlash draft loads at all** — `AttributeError: 'QKVParallelLinear'
object has no attribute 'weight'`, on both images tested. Reopen only if upstream
fixes the quantized-draft path *and* the W4A16 draft's 2.39 GiB saving covers a
3.1 GiB shortfall, which on its own it does not.

**minisglang-rdna4: deferred 2026-08-23 on judgement, not failure.** Never
booted. Even a clean boot would not displace this stack — hierarchical KV
offload, 93.3% token-level hit rate, 246,944 ctx, a working tool parser, none of
which minisglang has been shown to replicate — and the maintainer's validation
(TP=2, 16 GB cards, a different checkpoint) does not transfer to TP=1 on 32 GB.
The one reusable piece: weights were pre-staged onto a CephFS RWX volume so a
retest costs a short GPU window instead of a window plus a 23 GB download.

## State as of 2026-08-20 (verify against the manifest before trusting)

| setting | value |
|---|---|
| quant | `philbert440/Qwen3.8-27B-W4A16-AWQ`, g128 |
| image | vLLM nightly (ROCm) |
| maxNumBatchedTokens | 4096 |
| maxModelLen | 246,944 (pool 288,493 tok from the ceiling-sweep measurement, ~1.17x — the separate 287,159-tok figure below is from the kv-cache-memory-raise measurement, a different snapshot; the two are close but not the same run) |
| `--kv-cache-memory` | 9 GiB |
| `cpu_bytes_to_use` (CPU offload tier) | 22 GiB — stability-tested over a 9h restart-free window only; the manifest's own bar is a 24h restart-free soak beating the 0.5333 pre-change external-cache-hit baseline, which has NOT run yet. Manifest still says "Benefit NOT yet validated" — don't read the 9h result as validation |
| fs secondary offload tier | present — **load-bearing for decode speed, not just its hit rate** |
| spec-decode | off |
| cpu / memory | 2 / 36Gi |
| litellm `maxInputTokens` (qwen-3.8 entries) | 238,752 (= maxModelLen − maxOutputTokens) |
| litellm `maxOutputTokens` | 8192 |
| Hermes `context_length` | 238,752 |
| Hermes `max_concurrent_sessions` | 5 |
| Hermes `compression.threshold` | 0.5 |

## Open questions — never validated, don't mistake for settled

1. **philbert vs cyankiwi output quality — never compared.** The quant swap
   (g32→g128) was decided on `group_size`/ignore-list reasoning and a 1.5 GB
   VRAM saving, not a quality A/B. Largest open risk in the whole config.
   Cheapest close: a fixed real-prompt set (tool-calling, long-context recall,
   thinking traces), scored by hand or a judge model.
2. **`--kv-cache-memory 9 GiB` was never A/B'd** — chosen, not measured.
   `maxModelLen` is *derived* from the resulting pool via the 1.17x ceiling
   (see finding 5 below), so any resweep must re-derive maxModelLen per pool
   size — comparing ceilings across different pools is meaningless.
3. ~~**AITER unified attention vs the KV offload connector.**~~ **CLOSED
   2026-08-31 by live measurement.** The exclusion is an upstream bug and is
   removable (mechanism in "Compressed findings" below), but lifting it costs
   decode 31.50 → ~1.89 tok/s on gfx1201 with no prefill gain at our 50K shape.
   The connector it would have cost carries 72.1% of fallthrough and holds
   combined hit rate at 91.2% vs 18.6% GPU-only, so the trade was already
   unattractive before the decode number settled it. **Superseded 2026-09-01:**
   that 1.89 tok/s was vllm#53821 (AITER graph-replay metadata corruption),
   absent from the build under test and fixed upstream the same day. Retested on
   dev199 with the fix: decode reaches parity (conc-16 77.27 vs 77.63) and
   engine prefill is a heat (6388.5 vs 6390). Still not adopted, but because
   parity does not pay for carrying an out-of-tree patch, not because it is
   harmful. Note vllm#45916 is **not** the unblocker either: it patches
   `chunked_prefill_paged_decode.py`, imported by `rocm_attn.py` only, a backend
   genuinely incompatible with the connector. **Two things the retest did not
   measure**, so do not read parity as comprehensive: short-context prefill, which
   is the regime #43615's +56.5% at 512 tokens and +72.4% at 1K→2K were actually
   claimed for (judged unlikely to move a 42:1 prompt:completion workload — a
   judgement, not a measurement), and per-arm power draw, with the GPU sitting
   power-capped at 248W of 250W throughout both arms. A sweep at 512 / 2K / 8K /
   50K with `rocm-smi` sampled per arm is what would settle the published claim
   here; another decode sweep would not.
4b. **TurboQuant 4-bit KV: tested 2026-09-01, works on gfx1201, rejected.**
   552,612-token pool vs 288,508 (+92%), concurrency 1.17x -> 2.24x, prefill
   parity (6378.7 vs ~6390) -- but decode -27% at conc-16 and -54% single-stream,
   and it is mutually exclusive with the KV offload connector (layout LBNHC vs
   required LBHNC). Rejected because Hermes caps `max_concurrent_sessions: 5`,
   so the extra concurrency is unusable while the decode cost is paid in full.
   Revisit if that cap rises or context must grow past 246,944. Booting it needs
   three out-of-tree patches, each only visible after clearing the previous one:
   the `supports_kv_connector` override from finding 3; an import hook rebinding
   `fa_utils.flash_attn_varlen_func` to `aiter.ops.triton.attention.mha`, because
   `fa_utils` picks CK for anything that is not gfx1250 and CK's `mha_varlen_fwd`
   segfaults at head_dim 256 on gfx1201; and mounting AITER's **gfx1151**
   `MHA-DEFAULT.json` under the gfx1201 name, since AITER ships tuning configs for
   gfx1151/1250/942/950 only. Same RDNA family and 32-wide wavefronts, so only tile
   tuning is borrowed — which means **the decode numbers above are on an untuned
   config** and some of that loss is likely tiles, not architecture.
   **Output quality at 4-bit was never measured**: the throughput result
   disqualified the change before the quality gate ran. Published figures suggest
   ~0.8 points vs fp8 on long-context mrcr, and AMD found Qwen3.5's hybrid
   attention tolerant, but that is nobody's measurement of this deployment. Every
   published AMD TurboQuant result is MI355X (CDNA); gfx1201 is untested upstream.
4. **Inherited, never re-challenged:** `kvCacheDtype: fp8_e4m3` (never
   compared to fp16 KV — quality cost on this hybrid GDN model unmeasured);
   `gpuMemoryUtilization: 0.875` (inert now that `--kv-cache-memory` bypasses
   it, kept only as the fallback if that flag is ever dropped); `memory: 36Gi`
   (sized against a stale estimate, not reverified since the tier changes);
   `cpu: "2"` (raising to 4 measured no effect; lowering never tried).
5. **The 249,477-token ceiling reading is unexplained instability** (25.12
   and 1.95 tok/s on two runs — not merely slow, unstable), one step above the
   246,944 that shipped. If unexplained decode variance ever reappears at
   246,944, drop to 241,878 (measured clean) before investigating anything
   else.
6. **The whole config is shape-specific** to ~50K prompts / ~5 concurrent
   sessions / one GPU shared with Jellyfin. Nothing enforces the coupling —
   a shift in prompt size or concurrency invalidates `max_concurrent_sessions`
   (pool ÷ prompt size), `maxModelLen` (1.17x pool ratio), litellm
   `maxInputTokens`, and Hermes `context_length` together. Re-derive all four
   on any change to `--kv-cache-memory`, `maxModelLen`, or an image bump.

## Prefix caching is already at its ceiling — measured 2026-08-23

`vllm:prompt_tokens_by_source_total` exports the cache-outcome breakdown
server-side, so no `--enable-prompt-tokens-details` restart is needed for the
token-level answer. Over 24h, `service="qwen38-27b-vllm"`:

| source | prompt tokens (24h) | share |
|---|---|---|
| `external_kv_transfer` | 34,669,600 | 51.7% |
| `local_cache_hit` | 27,822,400 | 41.5% |
| `local_compute` | **4,503,759** | **6.7%** |

Only 6.7% of prompt tokens are recomputed. Cross-checks: `prompt_tokens_cached_total
/ prompt_tokens_total` = 93.3%, block-level combined = 92.1% (GPU 40.7%, external
86.7%). The "85%" in older docs was stale and understated this.

**Consequence: Hermes-side prefix stabilization is dropped.** A perfect fix is
bounded by that 6.7%, and 93.3% is itself evidence the prefix is already stable.
Revisit only if `local_compute` climbs past ~15%, which would mean something
upstream started perturbing the prefix.

## Methodology lessons (apply to any future tuning pass on this workload)

- **Check what a pinned build does *not* contain before hypothesising.** The
  2026-08-31 AITER test produced 1.89 tok/s and three theories, none of them the
  cause: the build was cut 9.5 hours before vllm#53821 merged, and the number was
  that bug. Diffing the build's own commit against upstream's merge log is cheaper
  than any hypothesis it would have replaced.
- **Suspending a child Kustomization does not hold.** `llmkube-models` is itself
  defined in git and reconciled by the parent `flux-system` Kustomization, which
  resets `spec.suspend` to the git value — three CR patches were silently reverted
  mid-test before this was found. Durable live-patching needs *both* levels
  suspended, and `flux-system` suspended means nothing in the cluster reconciles.
  Prefer a git commit over live patching for anything lasting more than minutes.

- **Gate every benchmark on an idle engine** (0 running / 0 waiting).
  Production traffic silently contaminates results — one run read a 35s
  median TTFT purely because 14 production requests landed mid-benchmark.
- **Never compare cache-cold to cache-warm.** The one big wrong conclusion in
  this log — "`maxNumBatchedTokens` 4096→8192 cuts TTFT 90%" — went through
  three rounds of re-measurement (retracted, confirmed-as-cache-state, then
  proven a regression when both arms were forced cache-cold) before landing
  on the true, much smaller, opposite-direction effect. A single-chunk prompt
  row in every sweep is a free noise-floor control (~5% here) that would have
  caught it immediately.
- **Judge KV/offload tiers by combined hit rate, not any tier alone.**
  `combined = gpu + (1-gpu) * ext` — the GPU and external tiers are
  anticorrelated by construction (external is only consulted on a GPU miss),
  so reading either one in isolation is meaningless and was nearly used to
  justify deleting a load-bearing tier.
- **Verify a gauge's semantics before trusting it.** Both
  `vllm:kv_cache_usage_perc` and `kv_offload_cpu_cache_usage_perc` turned out
  to measure *active/pinned* blocks, not cached content — a tier full of
  useful cached-but-idle blocks reads near 0%. Two separate wrong conclusions
  in this log came from trusting an unverified gauge.
- **A restart costs hours, not the ~4 min boot** — mechanism and root cause
  in "Compressed findings" below. Don't restart under load; batch config
  changes and prefer the admin API where possible.
- **Re-derive coupled invariants together, every time.** `max_concurrent_sessions
  <= KV pool / typical prompt tokens`, litellm `maxInputTokens + maxOutputTokens
  == maxModelLen`, and Hermes `context_length` all move together with
  `--kv-cache-memory`/`maxModelLen`/an image bump. Three separate incidents in
  one night came from exactly one of these being left stale.

## Compressed findings, in order

**vllm#50696 was a silent-correctness bug live in this deployment until
PR #4808 rolled out.** On models that zero freshly allocated KV blocks — any model with
mamba layers, and Qwen3.5 is a GDN hybrid — a CPU→GPU load in the offloading
connector could be wiped by a pending zeroing, and the request then attended over
zeros for its entire cache-hit prefix. No crash, no error, just degraded output.
The fix is `stream.wait_stream(current_platform.current_stream())` at
`kv_offload/cpu/gpu_worker.py:643`. Verify presence in the image rather than
inferring it from a build date. This is the concrete reason image bumps on this
deployment get read for correctness fixes, not just features.

**An identical prompt resend IS a real prefix-cache hit on this hybrid model,
at 832-token granularity.** Worth stating because it was disputed in review, on
the theory that a GDN hybrid can only reuse SSM state at request boundaries and
so would report zero cached tokens. Measured against the live engine, one prompt
sent twice with `max_tokens: 1`, reading `vllm:prefix_cache_hits_total` around
each send:

| send | prompt tokens | queries | hits |
|---|---|---|---|
| 1st (cold) | 4775 | +4775 | **+0** |
| 2nd (identical) | 4775 | +4775 | **+4160** |

4160 = 5 x the 832-token `block_size`, i.e. the hit covers every whole block and
rounds down — `mamba_cache_mode=align` aligns the mamba group to the same 832,
so alignment costs the remainder, not the hit. Consequence: `bench/cachedecode.py`'s
warm arm is genuinely warm, and its result (**no cached-prefix decode penalty,
1.05x over n=2**) stands as measured-but-inconclusive on sample size, not as an
invalid test design. Note `prompt_tokens_details` is absent from responses here
(`--enable-prompt-tokens-details` is not set), so `cached_tokens` cannot be read
per-request — use the metric delta above instead.

**Grammar-constrained tool calling costs nothing on decode.** Same 48K context,
conc 1, only variable a 2-tool schema: 22.84 vs 22.89 tok/s = 1.00x. The known
~100x MTP regression is a verify-step × grammar-mask interaction, not a grammar
cost on ordinary decode — do not conflate them.

**ROCM_AITER_UNIFIED_ATTN is refused whenever a KV connector is set.** The
gate is `backend.py:323` — `use_kv_connector and not cls.supports_kv_connector()`.
`RocmAiterUnifiedAttentionBackend` inherits `supports_kv_connector() -> False`
from `RocmAttentionBackend` and never overrides it.

NOT `forward_includes_kv_cache_update`, as an earlier revision of this file
claimed: `TRITON_ATTN` declares that `False` too and is the backend actually
selected here, so it cannot be the discriminator.

The inherited `False` is an upstream bug. The parent justifies it by its own
`(2, num_blocks, ...)` layout, which the subclass does not use — it overrides
`customize_spec` and advertises `LBHNC`, and
`OffloadingConnector.get_required_kvcache_layout()` returns exactly `LBHNC`.
Confirmed live 2026-08-31 by injecting the override: the backend selected and
ran with the connector attached, tiers created, KV pool unchanged. It was still
rejected, on measurement — decode 31.50 → ~1.89 tok/s with no prefill gain, a
figure later traced to vllm#53821 and retracted (see open question 3).

The mechanism is worth keeping even though the change was not adopted: the
override was injected without rebuilding the image or overlaying the source file,
via a `.pth` line naming a module that installs a meta-path finder and patches the
class after normal import. ~25 lines, version-independent, mounted by `subPath`
into site-packages through the CR's `extraVolumes`/`extraVolumeMounts`. A file
copy would have coupled the test to one vLLM build, which Renovate bumps every few
days. That is the pattern to reuse for any future one-symbol upstream test here.

What AITER *does* still contribute here: `AITER_LINEAR`, `AITER_TRITON_GEMM`,
`AITER_MHA`. **Not** `AITER_RMSNORM` — #43615 defaults
`VLLM_ROCM_USE_AITER_RMSNORM` to `False` on gfx12 because the kernel has known
issues there, and the running engine confirms it (`rms_norm=['native']`).

**MTP is blocked**, both by an open upstream issue (vllm-project/vllm#49002,
spec-decode + structured-output/tool-call → second-scale decode stalls) and
by measurement: a live attempt to enable it cost a ~6 min outage (the KV cut
made to fit the draft model's VRAM undershot the draft's own KV requirement),
and it independently caused a ~2x single-stream decode loss when finally
isolated (context 262144 case, decode 15.75-17.09 vs 29.66-31.99 at 221,612).
Combined with a separate rig's measurement that 3.8's MTP acceptance decays
fast past K=2, not worth Jellyfin's VRAM headroom. Revisit only at reduced
context, if ever.

**Decode is kernel-bound**, confirmed via GPU at 100% util / 2.4 GHz / 276W
while delivering only ~15 tok/s single-stream (pre-tuning baseline); the
skinny-GEMM Triton kernel still JIT-compiles during inference.

**Newest nightly image bumps carry nothing model-relevant** unless the diff
explicitly touches the dense (non-MoE) ROCm/int4 path — checked twice, both
times the only relevant-looking commit turned out to be MoE-specific.
Re-verify the compare (`git log <old>...<new>`) on every Renovate digest bump
rather than trusting the vLLM version string, which reads *older* than
current stable by design (`setuptools-scm` derives from the nearest reachable
release tag — `behind_by` in a tag compare is normal release-branch backport
noise, not a sign of being behind).

**DFlash1 (not v2) is what vLLM's registry actually supports** as of this
window — the DFlash2 checkpoint declares fields (`conv_group_size`,
`selector_rank`, etc.) that the then-current v1-only implementation had zero
references to. (Superseded 2026-08-21 upstream — see the DFlash2 PR/revert
history for the current state; this entry is kept only as a "don't trust an
unverified compatibility claim" methodology note.)

**Philbert vs cyankiwi quant: same recipe family, g128 wins on a kernel-tiling
artifact, not a better checkpoint.** Both are `compressed-tensors`
pack-quantized 4-bit AWQ, MSE observer, ignore lists identical on 311/313
entries, both keep the vision tower / GatedDeltaNet projections / lm_head /
MTP head in BF16. The Triton skinny-GEMM path clamps `BLOCK_K = min(BLOCK_K,
group_size)`, so philbert's g128 measures +52% decode over cyankiwi's g32
purely from that clamp — group 128 is objectively the coarser quantization of
the two. Philbert additionally documents thinking-mode calibration (avoids
the llm-compressor #2680/#2681 `<think>`-block corruption) and MTP validated
at 92.5% draft acceptance (K=2) on the quantizer's own hardware. Chosen on
this reasoning; **never validated for output quality on this workload** (open
question 1 above).

**Prefill scales roughly n^1.5-n^1.65 with prompt length** (fitted from the
mnbt-4096 sweep: cost rises ×1.38 then ×1.56 per doubling). Consequence:
halving prompt length cuts TTFT ~2.6x, not 2x — upstream context discipline
(Hermes compression) has more leverage than any engine-side knob.
`maxNumBatchedTokens` 4096 is the knee (confirmed by direct 2048/4096/8192/16384
comparison, cache-cold, single-chunk-controlled) — it wins on both TTFT
(9-17% faster than the previously-deployed 8192, at 10-40K prompt sizes) and
decode (+94% single-stream, no measured tradeoff). Do not raise it.

**Context ceiling bisected 221,612 → 246,944** (PR #4559): the regression at
higher context tracks the **pool/ceiling ratio**, not absolute token count —
it sits just below ~1.17x pool/cap. Only concurrency-1 decode cliffs; conc-16
holds flat across the whole range, so the regression is invisible to any
concurrent benchmark. Mechanism still unexplained. 249,477 (one step above
the shipped value) reads unstable rather than merely slower (open question 5).

**Preemption thrash is a concurrency-vs-pool arithmetic problem, not bad
luck.** 5.8 concurrent full-size (~50K token) sessions is the pool's actual
ceiling at 288,493 tokens; Hermes was allowing 8. Capping
`max_concurrent_sessions` to 5 stopped preemptions dead (5→1 over the next
6.5h) and flattened decode from an erratic 0-27 tok/s to a steady ~27.5.
Queueing at the source is strictly cheaper than admitting a request and then
discarding its prefill under preemption.

**CPU offload tier 16→22 GiB: stability-tested (not yet validated) over a 9h
restart-free window** (external tier fallthrough rose from a 62.6% pre-change
baseline to 72.1%). The manifest's own validation bar is stricter — a 24h
restart-free soak beating a 0.5333 pre-change `external_prefix_cache_hits/
queries` baseline, or rollback to 16Gi — and hasn't run that long yet; the
manifest itself still reads "Benefit NOT yet validated." Treat this 9h result
as a positive early signal, not a settled result.
The tier's occupancy gauge reads near-zero even while serving 88%+ of
fallthrough — it's a fast staging layer over the much larger fs (Ceph) tier,
not a bulk store, so low occupancy is by design, not evidence to reclaim the
RAM. `active_promotion_jobs` similarly reads 0 even while hits climb — not a
liveness signal.

**Hermes compression threshold 0.8→0.5, verdict keep 0.5, don't go lower.**
Seven days of litellm SpendLogs showed the old 184,448-token trigger was
*never once* reached across 3,329 requests — dead config. 0.5 (trigger
115,280) caps the 68 most expensive requests (~3% of n^1.5-weighted prefill
work) at effectively no cost. Structural blocker: `MINIMUM_CONTEXT_LENGTH =
64,000` floors the trigger, so compression can never reach the 25K-64K band
that alone holds ~52% of real prefill work — compression is architecturally
incapable of being the primary lever here at any threshold. 78.7% of prefill
work sits in the ordinary 25K-100K band (real agent turns, not runaway
sessions); the lever that matters is what enters *every* turn, not tail
compression.

**A restart's real cost is admission stalling for hours, not the ~4 min
boot.** Root cause (`v1/core/sched/scheduler.py:1026-1044`): with
`scheduler_reserve_full_isl` on by default, admission requires
`free >= full_ISL_of_new_request + blocks_reserved_by_inflight_async_loads`.
A restart collapses the GPU prefix cache (seen dropping 76-97% → 5-9%), which
routes nearly every request onto the `load_kv_async` path, maxing out
reservation pressure and stopping the admission loop entirely (`allocate_slots`
returning `None` hits a hard `break`, so prompt throughput reads exactly 0.0
rather than degrading gracefully). Recovery is load-dependent: if the current
working set exceeds the pool, the GPU cache thrashes instead of rewarming.

**Three silent coupling incidents in one night**, all now cross-referenced
in the "open questions"/state table above: a `Model.spec.files` mismatch after
a quant swap (fixed, PR #4561); the litellm `maxInputTokens`/`maxModelLen`
invariant left stale; and the preemption thrash from Hermes
`max_concurrent_sessions` outgrowing the pool. None of these three are
enforced by any validator — re-derive all of them by hand on any relevant
config change.

**litellm `model_group_alias` is a trap — do not use it to shim a retired
model name.** With `applyMode: api` + `store_model_in_db: true`, an alias
entry surfaces in `/model/info` carrying the *target's* `model_info.id`; the
operator reads that as unmanaged drift and issues `DELETE` by id, which
deletes the real model, whose subsequent 400-forever error storm OOMKilled the
operator and took the validating webhook down with it (blocking all CR writes
cluster-wide). Recovery: drop the alias, restart litellm, wait for
`Reconciled`. The only operator-safe shim for a retired model name is a real
`LiteLLMModel` CR per legacy name. Adjacent: compare `/model/info` per-pod,
never through the Service — two replicas can desync under `applyMode: api`
and the Service load-balancer hides it.

**`max_parallel_requests` at the litellm layer is the wrong layer for
KV-pressure control** — it's a request-count semaphore that can't see KV
occupancy, strictly less informed than vLLM's own token-aware admission
control, and was proposed/applied/reverted the same evening (PR #4578, closed
unmerged) once `num_preemptions_total` showed it was guarding a problem that
wasn't occurring. What actually fixed the preemption thrash was Hermes
`max_concurrent_sessions`, a token-aware cap at the request source.

**Tool calling is not a decode bottleneck** — xgrammar's constrained-decoding
kernel costs within 8% (no tools vs tools-auto vs tools-required, under live
load), nothing resembling the spec-decode/grammar wedge seen elsewhere.

**`maxOutputTokens` stays at 8192** — only 0.7% of a 7-day, 2,680-request
sample generated over 5,000 tokens; raising it would trade context nobody
uses for headroom under 1% of traffic ever touches.
