# vLLM qwen38-27b-vllm optimization log — 2026-08-18 to 2026-08-20

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
   unattractive before the decode number settled it. Not worth revisiting
   until vllm#45916 lands. Do not reopen this as an untested option.
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

## Methodology lessons (apply to any future tuning pass on this workload)

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
rejected, on measurement — decode 31.50 → ~1.89 tok/s with no prefill gain.
Full write-up in `aiter-unified-attn-kv-connector-plan-2026-08-31.md`.

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
