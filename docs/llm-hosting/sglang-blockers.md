# SGLang Blockers for gfx1201 (RDNA4, RX 9700 / Qwen3.6-27B)

Tracking what needs to land upstream before SGLang can replace vLLM in production without depending on the mattbucci RDNA4 fork.

**Current approach:** `mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference` fork, **v0.5.15** (torch 2.11+rocm7.2, Triton 3.6), baked into the `ghcr.io/tanguille/sglang-rdna4` image by `.github/workflows/build-sglang-rdna4.yaml`. Build/pin/rollback mechanics are in `docker/sglang-rdna4/README.md`. The retired PVC-rebuild recipe is gone with the `sglang` app directory; recover it from git history if ever needed.

---

## Blocker 1 — Spec-decode net-negative on dense DeltaNet (verify wall, no depth gate)

**Impact:** Critical. MTP×4 gives ~2× C=1 throughput in vLLM (19.5 vs ~7 tok/s without). Without it, SGLang C=1 performance degrades significantly.

*(The Symptoms/Root-cause below are pre-fork-patch v0.5.12-era findings — superseded by the
2026-07-07 re-check further down, which distinguishes the working EAGLE3 path from the still-broken
Spec-V2 path.)*

**Symptoms:**
- `SGLang Spec-V2 asserts on ROCm` at runtime
- `DFlash OOMs the DeltaNet draft path even at 16K context` on the fork

**Root cause:** The ROCm speculative decoding path in SGLang v0.5.12 has two separate failures: the Spec-V2 scheduler crashes on ROCm (assert in non-CUDA code path), and the DraftFlash attention kernel for the DeltaNet MTP draft model OOMs at 16K on a single 32 GB card (the fork was tuned for dual-card TP=2).

**Upstream references:**
- SGLang GitHub: search `speculative decoding ROCm assert` — no tracking issue confirmed; may be implicitly covered by general ROCm CI gap
- The fork excludes patch `050…CANDIDATE` which was the experimental MTP fix — it was too unstable to include

**Status:** Blocked — a workload-math wall, not a ROCm crash. See the retest triggers below.

**Spec-decode re-check (2026-07-07, 3-agent sweep: upstream ~545-commit delta, fork state, research
literature):** the framing above is now sharper — spec-decode *runs* on our RDNA4 via the fork's
patches (EAGLE3 measured on our exact dense 27B: **3.16× at ≤64K, 46.8 vs 14.8 tok/s**), so the
blocker is not ROCm alone. The real wall is workload math: DeltaNet's recurrent verify collapses to
**0.2 tok/s at 188K** (74× regression) and Hermes lives at 100-200K. Capturing the shallow win would
need depth-adaptive gating (speculate while shallow, off when deep), which **no engine implements** —
upstream `speculative_adaptive` adapts on batch size/accept-EMA only (PRs #21599/#24055/#23331) and
requires Spec-V2 (V1 removed in #25464, and Spec-V2 still asserts on ROCm — only CDNA gfx942/gfx950
got cookbook-verified, #29194/#29313). The closest-ever fix for the verify wall itself exists as
**PR #28695 (ReplaySSM ring spec-verify for GDN, RFC #28511)** — chunked/parallel GDN verify — but
it's unmerged, topk≤1-only, a batch≥64 bandwidth win (nothing at our compute-bound low batch), and
self-admittedly non-lossless past ~16K output tokens (repetition loops). Research has no GPU
answer for delta-rule GDN verify either (STree arXiv:2505.14969 is Mamba2-only; SpecMamba
arXiv:2509.19873 is FPGA/pure-Mamba). Spec-decode that actually works stays MoE-only
(no recurrent verify) → the dense→MoE swap remains the real TG lever.

**When to retest:** (1) sgl-project/sglang#28511 (ReplaySSM RFC) ships Part B merged AND lossless at
long output; (2) sgl-project/sglang#30263 (per-request spec opt-out) or any engine grows a
context-depth spec gate; (3) a DeltaNet-aware parallel-verify kernel appears from any source. These
replace the old "quarterly / ROCm CI" trigger — subscribe to #28511 and #30263.

---

## Blocker 2 — DeltaNet `in_proj_ba.weight` not recognized by weight loader

**Impact:** High for AWQ/quantized variants. Causes a crash or silent weight-drop when loading `cyankiwi/Qwen3.6-27B-AWQ-INT4` or any Qwen3-Next AWQ model.

**Symptom:** `parameter model.layers.N.linear_attn.in_proj_ba.weight not found in params_dict` — weight loader can't map the fused `in_proj_ba` / `in_proj_qkvz` names.

**Upstream references:**
- sgl-project/sglang #20973 — primary report ("can't load Qwen3.5-35B-A3B-NVFP4, in_proj_ba not found")
- sgl-project/sglang #20069 — Qwen3.5 bug tracker
- vllm-project/vllm #40252 — cross-engine confirmation

**Workaround (already applied in mattbucci fork):** Add `in_proj_ba` / `in_proj_qkvz` patterns to `quantization_config.ignore` to load those layers in BF16.

**Status:** Open in upstream SGLang; workaround available. Doesn't block the fork path.

**When to retest:** When a merged SGLang commit updates `qwen_gdn_linear_attn.py` or `qwen_next_weight_loader.py` to handle these weight names.

---

## Blocker 3 — `gptq_marlin_repack` kernel not compiled for ROCm

**Impact:** Medium — blocks Marlin-speed AWQ inference on AMD. Triton AWQ fallback (`--quantization awq`) works but is slower.

**Symptom:** `AttributeError: module 'sgl_kernel' has no attribute 'gptq_marlin_repack'` when SGLang selects the `awq_marlin` quantization backend on an AMD GPU.

**Root cause:** `gptq_marlin_repack` and `awq_marlin_repack` are CUDA-only kernels. The ROCm build of `sgl_kernel` does not compile them. These were migrated to JIT in v0.5.9 but remain CUDA-only.

**Upstream references:**
- sgl-project/sglang #12419 — "Unsupported Qwen3-next on ROCm" (Marlin + missing HIP kernels)
- sgl-project/sglang #17398 — comprehensive AMD ROCm support gaps
- AMD docs explicitly list `awq_marlin` and `gptq_marlin` as unsupported on AMD

**Workaround:** Use `--quantization awq` (Triton dequant path). The mattbucci fork uses `mattbucci/Qwen3.6-27B-AWQ` (calibrated for Triton AWQ on RDNA4), so Marlin is not needed.

**Status:** Won't-fix for the Marlin path on ROCm. No AMD port planned upstream.

**When to retest:** N/A — accept Triton AWQ as the permanent AMD path.

---

## Blocker 4 — gfx1201 not in sgl_kernel / AITER architecture table

**Impact:** Medium — without explicit gfx1201 support, sgl_kernel falls back to generic RDNA4 and FP8 ops silently use FP32 accumulation.

**Root cause:**
1. `sgl-kernel/setup_rocm.py` originally hard-exited for non-CDNA GPUs (pre-2026). Patch: add `gfx1201` to the whitelist.
2. AITER (`module_aiter_core.so`) does not include `gfx1201` in its arch table → FP8 WMMA silently falls back to FP32.

**Upstream references:**
- sgl-project/sglang Discussion #12600 — "gfx1201 llvm target support" (open, no ETA)
- sgl-project/sglang #27519 — RDNA3 gfx1101 whitelist (precedent for 1-line fix)
- ROCm/TransformerEngine #520 — gfx1201 missing from AITER FP8 WMMA arch table

**Workaround (in mattbucci fork):** Patches add `gfx1201` to `setup_rocm.py` whitelist and patch the AITER arch table entry. `SGLANG_USE_AITER=0` as fallback.

**Status:** Open / community-maintained only. The fork has the fix; upstream does not.

**When to retest:** When `sgl-project/sglang` Discussion #12600 is closed with a commit, or when `gfx1201` appears in the official SGLang Docker build targets.

---

## Blocker 5 — No official SGLang Docker image for gfx1201

**Impact:** Operational. Requires maintaining a custom Dockerfile and image (defined in `docker/sglang-rdna4/`).

**Upstream references:**
- sgl-project/sglang Discussion #12600 — same tracking thread as Blocker 4

**Status:** Community-only. The mattbucci fork is the only active maintained source; no AMD/SGLang team commitment to RDNA4 Docker images.

**When to retest:** If `sgl-project/sglang` starts publishing ROCm wheels for consumer RDNA GPUs, or if AMD adds gfx1201 to their official ROCm GPU support matrix for SGLang.

---

## Blocker 6 — Prefix cache vs batch on the DeltaNet hybrid (ROCm) — RESOLVED on v0.5.13 (now a tunable tradeoff)

**Status: resolved by the v0.5.13.post1 rebuild.** v0.5.13 ships native **MambaRadixCache**
(`hybrid_ssm=True`) — DeltaNet/SSM prefix caching on ROCm with **no** `extra_buffer` / FLA-NVIDIA
gate. The ~124k-token Hermes loop now reuses its cached prefix instead of re-prefilling every turn:
measured **7.6× TTFT** (cold ~16s → cache-hit ~2.1s; server log `#cached-token: 16384` of 18256),
which is what stops it tripping `agent.gateway_timeout` / litellm aborts. The earlier StreamingSession
mitigation is obsolete.

**The cache-vs-batch question is now a config choice, not a blocker:**
- **cache ON** (`no_buffer` + radix, **prod**): overlap scheduler off, but single-stream ~13.4 tok/s
  (decode-steps 16) and batch ~34 @conc8 / ~63 @conc16, max_running 16. Chosen — the long-context
  agent is the primary workload.
- **cache OFF** (`--disable-radix-cache`): overlap on, batch ~99 @conc32, but every agent turn
  re-prefills the full context (the original failure). A 97k cold prefill measured **303s** — the
  cost the cache eliminates.
- **`extra_buffer`** (overlap on *with* cache): boots on ROCm via a 1-line `is_hip()` patch to the
  `server_args.py` device assert, but gives **no** batch gain (~35 @conc8) and *worse* single-stream
  (~12) — RDNA4 dense-DeltaNet decode is compute-bound, so the overlap scheduler has nothing to hide
  and it halves max_running (→9). Ruled out; we stay on `no_buffer`.

**Required RDNA4/TP=1 patch (NOT in the fork — it targets a dual-card TP=2 box):** the JIT
`store_cache` kernel aborts at TP=1 (`kvcache.cuh:204: CUDA error: the operation cannot be performed
in the present state`). Force `can_use_store_cache()->False` (naive torch KV store). A stock
`setup.sh` rebuild without this crashes on the first request.

A **second RDNA4/TP=1 patch** lives in the same recipe: the sampler's cross-TP token-id all-reduce
(`_sync_token_ids_across_tp`) runs even at TP=1 — a no-op on a 1-rank group — for grammar/structured-output
(`json_schema`/tool-calling) requests. That first all-reduce lazily initialises NCCL mid-run (~256MB
calloc); hours in, once VRAM is committed, the calloc OOMs and crashes the engine (exit 0 via SIGQUIT),
which intermittently broke the `agent-pr-review` CI with HTTP 500s. Gated on `dist.get_world_size(group=self.tp_sync_group) > 1`
so NCCL never initialises at TP=1 (validated: 0 NCCL inits under real grammar traffic).

**VRAM ceiling (context vs batch, measured on the 32GB R9700):** weights (~16GB) + fp32 mamba state
(**6.89GB** @ 48 slots = max_running 16) + KV pool + prefill headroom must fit in 31.9GB. Keeping the
16 batch slots, the KV pool tops out at **126,854 tokens** @ `--mem-fraction 0.90` (Hermes's 124k just
fits, 2.88GB prefill headroom; a real 97k prefill validated — correct recall, no OOM). mem 0.95 (179k
pool) **OOMs** on a ~125k prefill (only 1.26GB activation headroom → CUDA OOM). Full native 262k for a
single session isn't reachable without dropping batch slots or bf16 mamba state — and bf16 is risky
(the model ships `mamba_ssm_dtype: float32` for long-context recurrent stability, and the bf16 path is
NVIDIA-SM100 / FlashInfer-only).

**Upstream references:**
- SGLang cookbook (Qwen3.6) — extra_buffer "Requires FLA kernel backend (NVIDIA GPUs only)":
  https://docs.sglang.io/cookbook/autoregressive/Qwen/Qwen3.6
- HiCache-for-hybrid crash on Qwen3.5/3.6 (Open): https://github.com/sgl-project/sglang/issues/24121
- Unified Hybrid Radix Cache Refactor roadmap (Open): https://github.com/sgl-project/sglang/issues/20415

**HiCache re-check (2026-07-07, node RAM 40→64 GB):** #24121 is still open but every repro in the
thread uses `--hicache-io-backend kernel`; a maintainer recommends `direct` as the workaround, and
v0.5.14's kernel path additionally ships a known cache-hit KL regression on hybrid mamba (#28434) that
upstream "fixed" by rolling their own hybrid CI back to `direct` (#28904) — so `direct` is the only
upstream-validated hybrid path on our tag. Two open hazards on that path: #29034 (`--hicache-size N`
double-allocates 2N GB on hybrids — size via `--hicache-ratio` instead; fix unreleased) and #30314
(scheduler stalls minutes on mamba host-pool exhaustion under large-context eviction — degrades to a
stall for us, liveness is off). **HiCache enabled in prod** (direct, ratio 1.5, ~15 GB host pools; see
the sglang helmrelease). Enabling it swaps MambaRadixCache for the unified host-offload tree.
Validated live 2026-07-07, end-to-end under a Hermes load burst: host pools allocated (10.41 GB KV +
5.73 GB mamba), prefix reuse confirmed on the unified tree (extension probe: 2432/2435 tokens cached;
real-traffic 65,536-token hit; 90% burst hit rate), decode wall and 16-slot running ceiling unchanged,
and the host tier exercised for real — host pool filled to 98% under eviction pressure and 76,956
tokens were served back from host via ~1-3 ms load-backs (`cached_tokens_total{cache_source="host"}`),
vs the 155-303 s re-prefills those hits used to cost. NOTE: identical-prompt resends show `#cached-token: 0` BY
DESIGN on hybrids (SSM states exist only at end-of-request node boundaries) — validate reuse with an
extension probe (`/v1/completions`, request B = A's prompt + A's output + more), not a resend. No
release newer than v0.5.14 exists; main is ~545 commits ahead with unreleased hicache/mamba fixes —
the pinned fork tree carries stock v0.5.14 hicache code (no fork patch touches it).

**PVC-backed file L3 restart-recovery experiment (2026-07-15, disabled):** a 61,600-token
population request took 113.3s. After a clean pod restart, the token-exact post-restart extension took
70.7s, but the file-backend metric recorded only 4,538 backed-up tokens, with zero storage
prefetches and zero `storage_HiCacheFile` hits. Aggregate completion throughput for C1/C4/C8 was
11.16/8.74/14.09 tok/s; the C8 sample overlapped real traffic. Direct I/O and ratio 1.5 L2 remain
validated and enabled; `write_through_selective` was replaced on 07-27, see the retest below. Do
not propose unverified cache tweaks.

**Correction (2026-07-27): "failed restart recovery" was the wrong conclusion.** Two defects sat
upstream of that measurement, and neither is a property of L3.

1. **The backend cannot initialise on this image.** HiCache JIT-compiles `hash_binding.cpp` via
   `torch.utils.cpp_extension.load`; it `#include`s `<openssl/sha.h>`, and the image shipped the
   OpenSSL runtime without the headers. The compile is lazy, so `--hicache-storage-backend file`
   passes startup and health checks, then raises `RuntimeError: Failed to load HiCache native hash
   extension` from `unified_radix_cache.insert` on the **first prefill**, killing the scheduler.
   Fixed by adding `libssl-dev` in `docker/sglang-rdna4/Dockerfile`; needs an image rebuild. The
   first attempt put it in the **builder** stage and changed nothing: the JIT runs at request time,
   so the headers are needed in the *runtime* stage. `g++` and `ninja` already survive into the slim
   base, so only the headers were missing. A build-time gate now compiles the extension so this
   fails the build instead of a live request.
2. **`write_through_selective` blocks first-pass content from ever reaching L2.**
   `hiradix_cache.py:203` sets `write_through_threshold = 1 if write_through else 2`, gated at
   `:928` on `node.hit_count`. A novel prompt sent once has `hit_count == 1`, so it is never
   promoted GPU→host, and L3 can only back up host-resident nodes. That is the likely source of the
   4,538-of-61,600 backup figure. Any L3 test must run under plain `write_through`.

The 07-15 run did not crash, so it cannot have been on an image with defect 1 — meaning it is not
comparable to current builds and should not be quoted as evidence either way.

**Retest (2026-07-27): L3 works.** Run on image `sha256:72d934d3` under plain `write_through`,
with `--hicache-storage-backend file` and `SGLANG_HICACHE_FILE_BACKEND_STORAGE_DIR`.

| stage | evidence |
|---|---|
| backend initialises | synthetic prefill served; pod ran 22 min under live traffic, 0 restarts |
| L3 writes | 268,176 files / 14 GB on disk, `backuped_tokens_total` 267,978 |
| L2 saturates | `hicache_host_used_tokens` 267,978 of 274,861 |
| survives restart | after a cold start, `prefetched_tokens_total{storage_backend="file"}` 63,205 |

The restart figures close exactly: 73,120 newly backed + 63,205 prefetched = 136,325
`hicache_host_used_tokens`, so every L2 token is accounted for as either a fresh write or a
read-back from disk. Populate (58,071 tok) PP 196.9 tok/s; extension probe (58,209 tok) PP
252.9 tok/s, TG 0.56 tok/s. Both ran with production traffic on the GPU, so both are floors,
not clean numbers.

Three operational notes.

**Sizing.** The evictor is unbounded by default and `openebs-hostpath` enforces no quota, so
`MAX_SIZE` (64Gi) and `MIN_FREE_SPACE` (100Gi) are the only things between L3 and control-1's
shared 500G root; the free-space floor is what keeps a full cache from pushing the node under
kubelet's ~50G nodefs eviction threshold. L3 lives on its own `qwen36-27b-hicache` PVC rather
than sharing the triton cache, so its footprint is visible where capacity is planned.

**The cache key does not cover the weights or the engine.** `get_hash_str` is
`sha256(token_ids, prior_hash, page_size)` (`mem_cache/utils.py:106`) and the on-disk name only
appends `config_suffix = f"_{model_name}"` plus TP/PP/CP ranks (`hicache_storage.py:336-346`).
Nothing in it encodes the weights revision, the KV dtype or the KV layout, while
`served-model-name` stays stable across Renovate bumps of both the AWQ revision and the sglang
image. Either bump would otherwise serve KV pages computed by the previous one, which is a
wrong-output risk rather than a cache miss. `STORAGE_DIR` therefore carries a revision suffix
(`/hicache/sglang-v0.5.15_awq-f541031d`) that **must be bumped by hand with either pin**. It sits
directly above them in the manifest to keep the three visible together; the durable fix is
upstream folding a model/engine fingerprint into `config_suffix`.

**Deletion needs approval.** Rollback does not remove the directory; it holds prompt-derived
data. The 07-27 test data still sits at the old `/cache/sglang/hicache` path, orphaned by the
move to a dedicated PVC.

**`hicache-write-policy write_back` trialled and reverted (2026-07-09):** kept alongside the L3 removal
above as a still-valid L1→L2 (GPU→host) optimization — synthetic testing showed 0 aborts and lower
write amplification vs the default `write_through`. Real production traffic exposed a blocking bug not
visible in synthetic testing: `UnifiedRadixCache.evict()` calls `writing_check(write_back=True)`, which
does a GPU-stream-synchronize wait for *every* pending write-back to drain before eviction can proceed.
`write_through` copies happen eagerly per-hit in small increments, so this path rarely stalls; `write_back`
defers all copies to eviction time, so under real (non-repeated-prefix) traffic the backlog builds up and
the wait blocks the scheduler loop itself — observed as concurrency serialized to 1 running request with
the queue climbing unbounded (Hermes' own 240s stream-stale client timeout then compounded it via
retry storms). Reverted to default `write_through`; validated stable for 8h+ post-revert (0 backlog, 0
serialization). Don't re-propose `write_back` without a fix to the synchronous drain in `evict()`.

**When to revisit:** (1) HiCache: on the next FORK_REF/tag bump, re-check #29034/#30314/#24121 — a
release with the unified-tree fixes could restore `--hicache-size` sizing and remove the stall risk.
(2) If a future fork rebase relaxes the `no_buffer`→overlap-off constraint, re-test overlap+cache.
(3) Re-test `extra_buffer` only if a faster RDNA4 GDN decode kernel lands (today it's the scheduler,
not the kernel, that makes overlap a no-op).

---

## Blocker 7 — decode-topk-pages CANDIDATE chain (067+068+069) has drifted off our pinned `FORK_REF`

**Impact:** Low/deferred — this is a not-yet-shipped optional decode-speed lever, not a regression. Blocks testing patch 069 (Quest-style top-K KV page selection, claimed up to 1.77x long-context decode speedup) on our DeltaNet-hybrid Qwen3.6-27B.

**Symptoms:** `patch -p1 --dry-run` of `patches/067-force-decode-window.patch.CANDIDATE` fails on `server_args.py` even when applied first, against a genuinely pristine tree (i.e. not a chain-ordering artifact — 068 and 069 also fail, compounding the same root cause).

**Root cause:** 067's hunk expects `triton_attention_split_tile_size: Optional[int] = None` to be immediately followed by `num_continuous_decode_steps: int = 1` in the `ServerArgs` dataclass, so it can insert `force_decode_window` between them. Our pinned tree (`FORK_REF=60ffa9501c2c6`) already has four newer fields inserted at that exact location (`prefill_only_disable_kv_cache`, `disable_radix_cache`, `disable_chunked_prefix_cache`, `disable_overlap_schedule`) from later upstream/fork changes the `.CANDIDATE` series was never rebased against. This is real semantic drift, not a cosmetic line-number offset — `patch`'s fuzz matching (already at fuzz=2) can't resolve it.

**Investigated:** 2026-07-01, caught at the dry-run phase with zero production impact — full detail in the blocker 7 row below (architectural compatibility with the DeltaNet hybrid was confirmed separately; the patches are sound, just stale against our tree).

**Status:** NO-GO per the test plan's gate — no hand-rebasing the patches (upstream hasn't validated a hand-patched variant). `.CANDIDATE` files and `FORK_REF` left untouched. Retest when upstream rebases the `.CANDIDATE` series past our pin, or incidentally on our next `FORK_REF` bump.

---

## Blocker 8 — No RDNA4 fused kernel for the newer FP4 formats (Quark 0.12: AMDFP4 / NVFP4 / SVDQuant)

**Impact:** None today — this is a "does re-quantizing unlock faster decode?" evaluation, and the answer is no. Documented so we stop re-asking on every Quark release.

**Context:** AMD Quark 0.12.0 (2026-07) adds AMDFP4 (E5M3 per-block scales), NVFP4, native MXFP4 inference support, and the SVDQuant algorithm (INT4/MXFP4/NVFP4 weights + low-rank outlier branch). Tempting as a path off the slow AWQ-int4 dense-decode wall.

**Root cause (why none of it helps this box):** Quark is a *quantization producer* — it emits checkpoints. Our dense-decode bottleneck is the RDNA4 **inference kernel** (Triton W4A16 + GatedDeltaNet), an SGLang/fork problem the checkpoint format can't touch. Re-quantizing the same weights to a different FP4 format doesn't change which kernel SGLang dispatches at decode. Producer with no consumer:
- **MXFP4 / NVFP4 / AMDFP4:** no fused decode kernel in the mattbucci fork for gfx1201 (consistent with the MXFP4/fp4 "ruled out for dense" finding in `engine-benchmarks-gfx1201.md`).
- **SVDQuant:** its runtime is **Nunchaku — CUDA W4A4 + low-rank, Nvidia-only**; no ROCm path, and SGLang has no consumer for the SVDQuant (INT4 + low-rank branch) format on any GPU. Public SVDQuant checkpoints are also almost all diffusion/image models (FLUX, Qwen-Image); no LLM checkpoint exists for Qwen3.6-27B (even Qwen3.5-27B is GPTQ-Int4/AWQ only).

**Investigated:** 2026-07-04, doc-only (no build). SVDQuant quality-vs-AWQ was the one non-throughput angle (better int4 accuracy at equal speed) but we've flagged no quality issue, so not worth the self-quantize + missing-kernel effort.

**Status:** NO-GO. AWQ-int4 stays the dense path. Same retest trigger as MXFP4 — a fork/upstream announcement of an RDNA4 **fused FP4 decode kernel** for gfx1201. *That* is the trigger, not a Quark release (Quark is necessary-but-not-sufficient: it produces the checkpoint the kernel would consume).

**References:**
- Quark 0.12 release notes: https://quark.docs.amd.com/latest/release_note.html
- Nunchaku (SVDQuant runtime, CUDA-only): https://github.com/nunchaku-ai/nunchaku

---

## Summary table

| # | Blocker | Severity | Workaround? | When to recheck |
|---|---------|----------|-------------|-----------------|
| 1 | Spec-decode net-negative on dense DeltaNet (verify wall + no depth gate) | **Critical** | No (runs, but net-negative at depth — see body) | #28511 merged+lossless, or #30263 / any depth gate |
| 2 | DeltaNet in_proj_ba weight loader | High | Yes (BF16 ignore list) | When qwen_gdn_weight_loader fixed upstream |
| 3 | gptq_marlin_repack missing on ROCm | Medium | Yes (Triton AWQ) | N/A (accept Triton path) |
| 4 | gfx1201 missing from sgl_kernel / AITER | Medium | Yes (mattbucci patches) | Discussion #12600 closed |
| 5 | No official gfx1201 Docker image | Operational | Yes (custom Dockerfile) | RDNA4 in official ROCm matrix |
| 6 | Prefix cache on DeltaNet hybrid (ROCm) | **Resolved** (v0.5.13) | Unified tree + HiCache host-offload since 2026-07-07 (was MambaRadixCache); TP=1 store_cache patch | #29034/#30314 fixes released (restores --hicache-size, removes stall risk) |
| 7 | decode-topk-pages CANDIDATE chain (067+068+069) drifted off FORK_REF | Low (optional, deferred) | No — patch needs upstream rebase | Fork rebases the CANDIDATE series, or our FORK_REF bump happens to realign |
| 8 | No RDNA4 fused kernel for newer FP4 formats (Quark 0.12 AMDFP4/NVFP4/SVDQuant) | None (eval, NO-GO) | N/A — format has no gfx1201 consumer | Fork/upstream ships a fused FP4 decode kernel for gfx1201 (not a Quark release) |

**Bottom line:** The mattbucci fork resolves blockers 2–6 today (v0.5.13.post1). Blocker 6 (prefix cache) is fixed by native MambaRadixCache — cache is ON in prod, which fixes the long-context agent, at a batch cost (~63 @conc16 vs ~99 no-cache) that's the right tradeoff for the agentic workload. Blocker 1 (spec-decode) has a real shallow-context win (3.16× ≤64K) but no depth gate exists anywhere and Hermes runs at 100-200K, so it stays off in prod. Since 2026-07-07 the prefix cache runs on the unified host-offload tree with HiCache (`direct` backend, ratio-sized, replacing MambaRadixCache — see the HiCache re-check above for the open upstream hazards it routes around and the live validation record).

---

## Known-benign log noise (not blockers — do not re-investigate)

Startup/runtime log lines that look like errors but aren't. Investigated 2026-07-04.

- **`Failed to get device capability: nvcc not found and PyTorch is not built with CUDA support...`** (×6 at startup) — not sglang, it's `flashinfer`'s `CompilationContext.__init__` probing `torch.cuda.get_device_capability()` to build `-gencode` flags for flashinfer's own **CUDA** JIT path. No ROCm branch exists, the exception is caught and logged, not propagated. This fork's real AMD backend/kernel selection goes through the separate ROCm-native path (`sglang/srt/platforms/rocm.py`), which is unaffected. Cosmetic; ignore.
- **`Tokenizer for mattbucci/Qwen3.6-27B-AWQ is still TokenizersBackend after retries with --trust-remote-code. Model-specific tokenizer attributes may be missing.`** (×3 at startup) — Qwen3.6's declared tokenizer class doesn't resolve even via the custom-code retry, so sglang falls back to the generic transformers-v5 `TokenizersBackend`. sglang has a matching handled path for this exact case: `_apply_post_load_fixes()` (`_fix_v5_add_bos_eos_token`, `_fix_special_tokens_pattern`, `_fix_added_tokens_encoding`) patches bos/eos + special-token gaps for tokenizers loaded this way — it's not silent breakage. Live tool-calling/chat-template traffic (Hermes, PR-review CI) has run on this fork without observed special-token corruption, so treat as verified-fine in practice.
- **`'--disable-cuda-graph' is deprecated...`** — was a real (if cosmetic) fix, not log noise: patched in the Dockerfile (patch 4) and `sglang-env-rebuild.sh` to emit `--cuda-graph-backend-{decode,prefill}=disabled` instead. Listed here only so the warning's absence post-rebuild isn't mistaken for a regression.
