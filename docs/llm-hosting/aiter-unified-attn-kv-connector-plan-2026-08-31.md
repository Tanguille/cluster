# Plan: unblock ROCM_AITER_UNIFIED_ATTN under the KV offload connector

**Status:** not started
**Branch:** `test/aiter-unified-attn-kvconn`
**Worktree:** `.claude/worktrees/aiter-unified-attn-kvconn`
**Target:** `qwen38-27b-vllm` InferenceService, namespace `ai`, control-1 (R9700, gfx1201)
**Shape:** runtime-only live PR test. No cluster-repo commit ships the patch.

## Hypothesis

`ROCM_AITER_UNIFIED_ATTN` is rejected on this deployment for a property it does
not have. Unblocking it is a one-line override.

Evidence, all read out of the running pod at vLLM `0.28.1rc1.dev130+g44fe2a392`:

1. The gate is `backend.py:323` — `use_kv_connector and not cls.supports_kv_connector()`.
   Base default is `True`.
2. `rocm_attn.py:210` overrides it to `False`, reason given in-comment: *"ROCM_ATTN
   uses (2, num_blocks, ...) KV cache layout which is incompatible with KV
   connectors that require blocks-first layout."*
3. `RocmAiterUnifiedAttentionBackend` subclasses `RocmAttentionBackend` and never
   overrides the method — confirmed, `'supports_kv_connector' in A.__dict__` is
   `False`.
4. The subclass explicitly departs from the parent's layout. It overrides
   `customize_spec` (docstring: *"Keep K and V packed in the content dim, unlike
   the native HIP kernels the base class targets"*) and `supported_kv_cache_layouts`
   → `(LBHNC, LHBNC)`.
5. `OffloadingConnector.get_required_kvcache_layout()` returns `"LBHNC"` —
   which the subclass supports.

So the parent's stated reason does not hold for the child, and the layout the
connector demands is one the child advertises. No upstream issue or PR covers
this; #43615 enabled the backend on gfx12 on 2026-08-03 and the connector layout
work landed separately in 0.28.0, so plausibly nobody has run both on RDNA4.

**Prize:** #43615's own numbers, measured on an R9700, are prefill +56.5% at 512
tokens, +72.4% on 1K→2K, 47-58% mixed. Our workload is ~50K-token prefills.
Unlike DFlash2 this costs no VRAM.

**What is NOT established:** that the kernel is *correct* through the offload
path. Matching layout declarations is necessary, not sufficient. The subclass
keeps K and V "as transposed views rather than copies"; the connector copies
blocks out to the CPU and fs tiers. If those views violate an assumption in the
copy path, the failure mode is **silently wrong KV**, not a crash. That is the
whole reason this plan gates on correctness before it looks at a single
throughput number.

## Risk verdict

**RISKY — proceed only in an agreed window, with the correctness gate first.**

| factor | assessment |
|---|---|
| one-way door? | No. No CRD, schema, or on-disk format change. Patch is a mounted file. |
| blast radius | `qwen38-27b-vllm` only. It is the `qwen-3.8` / `qwen-3.8-fast` backend, so Hermes, karakeep, and agent-pr-review all stall while it is down. |
| worst case | Silent KV corruption → wrong answers with no error. Gate exists for this. |
| second worst | Two restarts. Per prior incidents a restart costs *hours* of degraded admission, not the ~4 min boot: it wipes the tmpfs CPU tier and collapses the GPU prefix cache, pushing requests onto `load_kv_async` under reservation pressure. |
| reversibility | High. Revert = drop two `extraVolume*` entries and reconcile. |

Two environmental constraints, both already known:

- **No side-by-side A/B.** control-1 advertises 4 `squat.ai/dri` slots and all 4
  are held (this pod, jellyfin ×3, fileflows, drm-exporter). Arms run sequentially.
- **Renovate auto-bumps this image digest** (5 bumps in the last two weeks). A
  digest bump mid-test invalidates the arm. Step 1 pins it; Step 7 unpins.

## The patch

Do **not** rebuild the ROCm image (tens of GB) and do **not** overlay the whole
399-line source file — a file copy silently couples the test to one vLLM build,
and Renovate bumps this image often. Use an import hook, which is
version-independent and ~25 lines.

Two files in a ConfigMap, mounted by `subPath` into site-packages. A `.pth` line
beginning with `import` is executed at interpreter startup; the module it names
installs a meta-path finder that patches the class *after* normal import.

`zz_aiter_kvconn.pth`:

```
import zz_aiter_kvconn_impl
```

`zz_aiter_kvconn_impl.py`:

```python
# TEST ONLY -- see docs/llm-hosting/aiter-unified-attn-kv-connector-plan-2026-08-31.md
# RocmAiterUnifiedAttentionBackend inherits supports_kv_connector()->False from
# RocmAttentionBackend, whose stated reason (the parent's (2, num_blocks, ...)
# layout) does not apply: the subclass overrides customize_spec and advertises
# LBHNC, which is exactly what OffloadingConnector requires.
import importlib.abc
import sys

TARGET = "vllm.v1.attention.backends.rocm_aiter_unified_attn"


class _PatchLoader(importlib.abc.Loader):
    def __init__(self, spec):
        self._spec = spec

    def create_module(self, spec):
        return self._spec.loader.create_module(spec)

    def exec_module(self, module):
        self._spec.loader.exec_module(module)
        module.RocmAiterUnifiedAttentionBackend.supports_kv_connector = classmethod(
            lambda cls: True
        )
        print("[aiter-kvconn-patch] supports_kv_connector -> True", file=sys.stderr, flush=True)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name != TARGET:
            return None
        for finder in sys.meta_path:
            if finder is self:
                continue
            spec = finder.find_spec(name, path, target)
            if spec and spec.loader:
                spec.loader = _PatchLoader(spec)
                return spec
        return None


sys.meta_path.insert(0, _Finder())
```

Mounted via the CR's `extraVolumes` / `extraVolumeMounts` (both confirmed present
on the InferenceService CRD) at
`/usr/local/lib/python3.12/dist-packages/zz_aiter_kvconn{.pth,_impl.py}`.

## Steps

### Step 1 — Freeze and baseline (no mutation yet)

1. Create the worktree and branch; copy `.mcp.json .env CLAUDE.local.md .vscode/ .claude/` into it.
2. Pin the image digest against Renovate for the window (close/hold any open bump PR).
3. Record the baseline, gated on an **idle engine** (0 running / 0 waiting) and an
   **idle GPU** (Jellyfin not transcoding — check VCN utilization, not just pod state):
   - `docs/llm-hosting/bench/concsweep.py 8000 qwen-3.8` → decode, report tok/s at conc 1 and 6
   - `docs/llm-hosting/bench/ttftsweep.py 8000 qwen-3.8 50000` → prefill, production shape
   - Report **both PP and TG**. Never one alone.
   - Offload tier state: `vllm:kv_offload_tiering_chunk_hits_total` /
     `vllm:kv_offload_tiering_chunk_queries_total`, and
     `vllm:external_prefix_cache_hits_total` / `..._queries_total`.
     Judge by **combined** hit rate, `gpu + (1-gpu)*ext` — the tiers are
     anticorrelated by construction and either alone is meaningless.
   - `kubectl get inferenceservice qwen38-27b-vllm -n ai -o yaml` to a scratchpad file.
   - Cluster inventory for the Step 7 diff:
     `kubectl get crd,validatingwebhookconfigurations,mutatingwebhookconfigurations -o name | sort`
     and the current list of suspended Flux objects.
4. **Correctness noise floor.** This is the step people skip and it invalidates
   everything downstream. Different attention kernels are not bit-identical, so
   an exact-match gate against the patched arm would fail for benign reasons.
   Establish the baseline's *self*-consistency first:
   - 20 fixed production-shaped prompts (~50K tokens), greedy: `temperature 0`,
     `top_p 1`, fixed seed, `max_tokens 256`.
   - Run the set twice against the **unpatched** engine, back to back, second run
     warm so it loads from the offload tier rather than recomputing.
   - Confirm `kv_offload_tiering_chunk_hits_total` actually increments between
     runs. If it does not, the test is not exercising the path under suspicion
     and the whole exercise is void — fix the prompt size before continuing.
   - Record baseline↔baseline agreement. That number is the gate threshold.

### Step 2 — Write the revert runbook, then mutate

Write it **before** the first mutating command, to
`~/.local/state/live-pr-test/ai-qwen38-27b-vllm.md` (not the session scratchpad,
which dies with the session). Pin it to the cluster so anyone finding the object
suspended finds the trail:

```
kubectl annotate inferenceservice qwen38-27b-vllm -n ai \
  live-pr-test/runbook=~/.local/state/live-pr-test/ai-qwen38-27b-vllm.md
```

Runbook contents:

```
# revert
flux resume kustomization llmkube-models -n flux-system
flux reconcile kustomization llmkube-models -n flux-system --with-source
# kustomize-controller SSA force-applies, so field drift reverts without --force
# (unlike helm-controller -- do not copy the HelmRelease recipe here)
kubectl delete configmap aiter-kvconn-patch -n ai
kubectl annotate inferenceservice qwen38-27b-vllm -n ai live-pr-test/runbook-
# ORIGINAL image (verify before patching, paste actual value here):
#   vllm/vllm-openai-rocm:nightly@sha256:d53c0dd4a4639e9e8eb10a3128821083acfbc1c083125f92300366bde3153335
# Fields ADDED by this test, each needs explicit removal:
#   spec.extraVolumes[name=aiter-kvconn-patch]
#   spec.extraVolumeMounts[2 entries, subPath zz_aiter_kvconn.pth / _impl.py]
```

Then: suspend the owning Kustomization (read the
`kustomize.toolkit.fluxcd.io/name`/`namespace` labels off the CR — do not guess),
create the ConfigMap, and patch the **CR**, never the child Deployment: the
llmkube operator reverts a Deployment edit within seconds, and suspending the
operator's own HelmRelease does not stop it.

### Step 3 — Verify the patch actually took

Three checks, all required. Any one failing means abort and revert, not "probably fine":

1. `[aiter-kvconn-patch] supports_kv_connector -> True` in the pod's stderr.
2. The selector log now reads `Using ROCM_AITER_UNIFIED_ATTN backend`, and the
   old `rocm.py:703 Found incompatible backend(s) [ROCM_AITER_UNIFIED_ATTN, ...]`
   line is **absent**.
3. The engine reached `Starting vLLM server` and the offload connector
   initialised without error — a connector that silently failed to attach would
   make the throughput numbers meaningless *and* look like a win.

### Step 4 — Correctness gate (blocking)

Re-run Step 1.4's exact prompt set and comparison against the patched engine.

- Agreement must be at or above the baseline↔baseline floor from Step 1.4.
- Confirm `kv_offload_tiering_chunk_hits_total` increments — the patched arm must
  be exercising the offload path, not bypassing it.
- Spot-read several full outputs by hand. Degenerate repetition, truncation, or
  drifting-then-recovering text are the signatures of subtly corrupt KV and can
  hide inside an aggregate agreement score.

**If this gate fails, revert immediately and stop.** Do not proceed to
benchmarks to "see if it was at least faster" — a fast wrong answer is the
outcome this whole plan exists to avoid.

### Step 5 — Benchmarks

Only after Step 4 passes. Same harnesses, same idle gating, same prompt shapes
as Step 1.3. Report PP and TG both, scoped explicitly to this exact config —
no generalization beyond it.

Watch VRAM: AITER unified attention may size workspaces differently. Free VRAM
must stay above the 2 GiB Jellyfin transcode reserve. `DgpuVramLow` firing is an
abort condition, exactly as it was for DFlash2 on 2026-08-22.

### Step 6 — Revert and prove it

Run the runbook. Then prove clean, do not assume:

- CR matches its manifest in git (no `extraVolumes`/`extraVolumeMounts` residue).
- Selector log is back to `Overriding with TRITON_ATTN`.
- ConfigMap deleted; runbook annotation removed.
- Step 1.3 inventory diff: CRDs and webhooks identical, and no Flux object left
  suspended that was not suspended before —
  `kubectl get kustomizations,helmreleases -A -o json | jq -r '.items[] | select(.spec.suspend==true) | .metadata.name'`
  (this cluster once carried an unnoticed suspended ks for weeks).
- Unpin Renovate.
- Give the offload tiers time to rewarm before declaring throughput normal.

### Step 7 — Report and, if it worked, upstream it

If Steps 4 and 5 both pass, this is a one-line upstream fix worth sending:
`supports_kv_connector() -> True` on `RocmAiterUnifiedAttentionBackend`, with the
layout argument and the measured gfx1201 numbers as justification. Nothing in the
cluster repo changes either way — if we want the win permanently we take it from
a released vLLM carrying the fix, not from a mounted patch.

Regardless of outcome, correct the two stale lines in
`vllm-optimization-log-2026-08.md`: open question #3 attributes this exclusion to
`forward_includes_kv_cache_update = False`, which is wrong (TRITON_ATTN declares
that too and *was* selected — the real mechanism is `supports_kv_connector()`),
and the claim that AITER RMSNorm stays on by default is stale for gfx12, where
#43615 defaults `VLLM_ROCM_USE_AITER_RMSNORM` to False.

## Abort conditions

Any one of these ends the test and triggers Step 6 immediately:

- Correctness gate (Step 4) below the noise floor.
- `DgpuVramLow` fires, or free VRAM drops under the 2 GiB transcode reserve.
- The engine does not reach serving within its normal load window.
- Offload chunk hits stay flat, meaning the path under test is not exercised.
- The image digest changes underneath the test.

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of the plan have been consolidated into existing documentation, the plan file can be removed. If there is no relevant existing documentation, the plan should be reworked into a reference document.

**Important**: Every prompt should verify the branch and worktree before doing any work.

---

# RESULT — executed 2026-08-31, REVERTED

> **The 2026-08-31 performance conclusion below is SUPERSEDED.** Its 1.89 tok/s
> decode figure was an artefact of vllm#53821, an AITER graph-replay bug present
> in the build under test and fixed upstream the same day. See
> "RETEST 2026-09-01" at the end. The section is kept because its *method* and
> its retracted hypotheses remain instructive — the numbers are not.

**Status: hypothesis CONFIRMED. Change rejected — originally on a regression
that turned out to be a bug, and on retest for delivering parity.**

## What the patch proved

The exclusion is real and removable. With `supports_kv_connector() -> True`
force-injected onto `RocmAiterUnifiedAttentionBackend` via the import hook, the
engine selected:

```
rocm.py:703 Found incompatible backend(s) [TURBOQUANT] with AttentionType.DECODER.
Overriding with ROCM_AITER_UNIFIED_ATTN out of potential backends:
  ['ROCM_AITER_UNIFIED_ATTN', 'TRITON_ATTN']
```

AITER unified attention ran **with the OffloadingConnector active**, which the
unpatched engine refuses outright. Supporting evidence that nothing else broke:

- `Created TieringOffloadingManager with primary tier (lru, 216 blocks) and 1 secondary tier(s)`
- KV pool identical to baseline: **288,508 tokens, 1.17x** (baseline 288,493)
- VRAM 31.10 / 34.21 GB, free 3.11 GB — above the 2 GiB Jellyfin reserve
- Offload tier lookups healthy: sync delay ~3.4e-5 s over 152 chunk queries
- No tracebacks, no connector errors

So the inherited `False` is a genuine upstream bug. It is just not a *useful* one
to fix on this hardware.

## Why it was rejected

| metric | baseline TRITON_ATTN | patched AITER_UNIFIED |
|---|---|---|
| prefill, 50K tokens | ~6390 tok/s | ~6392 tok/s |
| decode, conc 1 | **31.50 tok/s** | **~1.89 tok/s** |
| cudagraph capture | PIECEWISE | PIECEWISE |

Two sequential 50K correctness prompts took 74.9 s and 75.5 s. The near-identical
times rule out JIT warmup as the cause (two Triton kernels did JIT-compile mid-run,
`kernel_unified_attention_2d` and `_triton_w4a16_skinny_fmt_kernel`, but the second
request paid the same cost as the first). Decomposing 75.5 s: prefill 50K at
~6390 tok/s = 7.8 s, leaving ~67.7 s for 128 decode tokens = **1.89 tok/s**, a
~16.6x regression against the 31.50 tok/s baseline measured the same night.

**No prefill gain whatsoever.** #43615's R9700 numbers (+56.5% at 512 tokens,
+72.4% on 1K→2K) did not reproduce at our 50K production shape. Those were
measured on short contexts and, per its own text, validated on
Qwen3-30B-A3B-FP8 — not a GDN hybrid at `head_dim=256` behind a KV connector.

**Two intermediate hypotheses were wrong. Both are recorded because the
evidence that killed them is cheap to re-check and expensive to rediscover.**

1. *Lost FULL cudagraph capture.* Wrong. The reverted TRITON baseline captures
   `PIECEWISE` only as well, so graph mode is identical on both arms.
2. *#45916 would fix it.* Wrong, and worse: #45916 patches
   `vllm/v1/attention/ops/chunked_prefill_paged_decode.py`, which is imported by
   **`rocm_attn.py` only**. ROCM_ATTN is the backend whose
   `supports_kv_connector() -> False` is *legitimate* — its
   `(2, num_blocks, ...)` packing really is connector-incompatible, which is why
   the parent comment exists. So #45916 optimises a decode path this deployment
   cannot reach while the offload connector is in use.

3. *The cause is not identifiable.* Also wrong, and this is the one that
   mattered. The cause was **vllm#53821** — the generic ROCm metadata builder
   zeroes `common_attn_metadata.query_start_loc` during graph capture, and AITER
   unified attention consumes that tensor during replay, so sharing the generic
   capture path corrupts its query boundaries. It merged 2026-08-31T13:53Z,
   about 9.5 hours after the build under test (`44fe2a392`, 04:20Z) was cut.
   The test measured a known bug, not the backend.

   The lesson generalises past this PR: three hypotheses were offered here before
   anyone checked the build's own commit against upstream's merge log. Checking
   what a pinned build does *not* yet contain is cheaper than any of them.

## Upstream value

The one-line fix is still correct and worth reporting: the parent's stated reason
(its `(2, num_blocks, ...)` layout) does not apply to a subclass that overrides
`customize_spec` and advertises `LBHNC`, which is what
`OffloadingConnector.get_required_kvcache_layout()` requires. But the honest
report must carry the gfx1201 measurement too: unblocking it here is a large
decode regression, so it should not be enabled by default on this arch until
AITER unified attention's own decode path is competitive on gfx12.

**Retried on 2026-09-01 once #53821 shipped — see the retest section below.**
The decode regression does not reproduce; the backend reaches parity and is
still not worth adopting, for a different and better reason.

## Revert proof

- `spec.extraVolumeMounts` back to 3 entries (`/cache`, `/kvoffload`, `/dev/shm`)
- configmap `aiter-kvconn-patch` deleted; `live-pr-test/runbook` annotation removed
- engine log: `Overriding with TRITON_ATTN`, zero `aiter-kvconn-patch` mentions
- pod back on the pre-patch replicaset hash `6b7ddcf577`
- `kustomizations,helmreleases` with `suspend=true`: none
- Flux resumed and reconciled to `refs/heads/main@sha1:21c2856`

## Cost

Two engine restarts. The offload tiers were already cold from the earlier PVC
deletion, so admission stays degraded for a while yet — do not read throughput
as steady-state until they rewarm.


---

# RETEST 2026-09-01 — regression was a bug; verdict now PARITY

Prompted by reviewing upstream commits our pin did not yet carry. Ran on
`vllm/vllm-openai-rocm:nightly@sha256:f0bdaf5...` = `0.28.1rc1.dev199+g7c5dc571c`
(bumped in #4808), which is `ahead` of both fixes below. Presence verified in the
image, not inferred from the build date:

- **vllm#53821** — `class RocmAiterUnifiedAttentionMetadataBuilder` at
  `rocm_aiter_unified_attn.py:35` with its own `get_builder_cls()`.
- **vllm#50696** — `stream.wait_stream(current_platform.current_stream())` at
  `kv_offload/cpu/gpu_worker.py:643`.

## Decode: the regression does not reproduce

Protocol identical on both arms — one warmup run discarded, then two measured.

| conc | TRITON_ATTN (dev199) | AITER_UNIFIED (dev199) | 2026-08-31 AITER (dev130, buggy) |
|---|---|---|---|
| 1 | 31.49, 31.68 | 16.75, 30.21 | ~1.89 |
| 8 | 53.90, 64.97 | 63.45, 61.78 | — |
| 16 | **78.22, 77.03** | **77.21, 77.32** | — |

conc 16 is the low-variance pair on both arms (spread <= 1.2) and is a dead heat:
77.63 vs 77.27. Engine-reported prefill throughput likewise: 6390 vs 6388.5.
Decode at conc 1 recovered ~16x, matching the magnitude of the original collapse.

## Verdict: parity, so still not adopted — but for a sound reason now

Adopting AITER means permanently carrying an out-of-tree source patch (the
`supports_kv_connector` override, which upstream has not taken). Parity does not
pay for that. The change in reasoning matters: 2026-08-31 rejected it as harmful,
which was false; 2026-09-01 rejects it as unnecessary, which the data supports.

## Explicitly NOT measured — do not read this as comprehensive

- **Short-context prefill.** #43615's claims are +56.5% at 512 tokens and +72.4%
  on 1K->2K. This retest covered 50K prefill and short-prompt *decode*. The
  regime the claim was actually made for is untested here. Judged unlikely to
  move production (42:1 prompt:completion at 47-56K tokens) — a judgement about
  workload mix, not a measurement.
- **Power draw per arm.** The GPU sat power-capped at 248W of 250W throughout
  both arms; no per-arm efficiency data was captured. Equal throughput at lower
  draw would be a real win and is unmeasured.

A future retest should sweep prefill at 512 / 2K / 8K / 50K on both arms and
sample `rocm-smi` power per arm. That, not another decode sweep, is what would
settle whether the published claim holds here.

## Measurement caveat carried forward

`bench/pp1.py` reports PP derived from TTFT and read 948.9 / 709.5 tok/s while
the engine simultaneously reported 6388.5. On this deployment TTFT is dominated
by offload-lookup and admission time (~57-80s of a ~90s TTFT), not by the
attention kernel. **Do not quote TTFT-derived PP as a kernel measurement** — use
the engine's own `Avg prompt throughput`.

## Unrelated but load-bearing finding from the same bump

**vllm#50696** fixed a silent-correctness bug affecting this exact deployment:
on models that zero freshly allocated KV blocks (any model with mamba layers —
Qwen3.5 is a GDN hybrid) a CPU->GPU load in the offloading connector could be
wiped by a pending zeroing, and the request would attend over zeros for its whole
cache-hit prefix. No crash, no error, just degraded output. It was live in
production until the #4808 rollout.
