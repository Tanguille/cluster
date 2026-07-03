# SGLang Blockers for gfx1201 (RDNA4, RX 9700 / Qwen3.6-27B)

Tracking what needs to land upstream before SGLang can replace vLLM in production without depending on the mattbucci RDNA4 fork.

**Current approach:** `mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference` fork, **v0.5.13.post1 + 46 patches** (fork HEAD `1034fea`, 2026-06-24), built in place on the `sglang` PVC at `/cache/sglang` (the image is only the ROCm 7.2.4 runtime base). Rebuild recipe — including the RDNA4/TP=1 fixes the fork's `setup.sh` omits — is `kubernetes/apps/ai/sglang/app/scripts/sglang-env-rebuild.sh`.
See `docs/sglang-qwen3.6-rocm-plan.md` for the full build plan and decision record.

---

## Blocker 1 — MTP / Speculative Decoding crashes on ROCm

**Impact:** Critical. MTP×4 gives ~2× C=1 throughput in vLLM (19.5 vs ~7 tok/s without). Without it, SGLang C=1 performance degrades significantly.

**Symptoms:**
- `SGLang Spec-V2 asserts on ROCm` at runtime
- `DFlash OOMs the DeltaNet draft path even at 16K context` on the fork

**Root cause:** The ROCm speculative decoding path in SGLang v0.5.12 has two separate failures: the Spec-V2 scheduler crashes on ROCm (assert in non-CUDA code path), and the DraftFlash attention kernel for the DeltaNet MTP draft model OOMs at 16K on a single 32 GB card (the fork was tuned for dual-card TP=2).

**Upstream references:**
- SGLang GitHub: search `speculative decoding ROCm assert` — no tracking issue confirmed; may be implicitly covered by general ROCm CI gap
- The fork excludes patch `050…CANDIDATE` which was the experimental MTP fix — it was too unstable to include

**Status:** Blocked / no upstream fix. Quarterly revisit.

**When to retest:** When SGLang ROCm CI shows speculative decoding passing, or when a `spec-v2-rocm` tag appears in the sgl-project/sglang releases.

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
`setup.sh` rebuild without this crashes on the first request — captured in `kubernetes/apps/ai/sglang/app/scripts/sglang-env-rebuild.sh`.

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

**When to revisit:** (1) When #24121 closes — v0.5.13 HiCache could offload mamba/KV state to host RAM
and ease the cache/batch/context tensions together (enabling bigger batch *and* full context). (2) If a
future fork rebase relaxes the `no_buffer`→overlap-off constraint, re-test overlap+cache. (3) Re-test
`extra_buffer` only if a faster RDNA4 GDN decode kernel lands (today it's the scheduler, not the
kernel, that makes overlap a no-op).

---

## Blocker 7 — decode-topk-pages CANDIDATE chain (067+068+069) has drifted off our pinned `FORK_REF`

**Impact:** Low/deferred — this is a not-yet-shipped optional decode-speed lever, not a regression. Blocks testing patch 069 (Quest-style top-K KV page selection, claimed up to 1.77x long-context decode speedup) on our DeltaNet-hybrid Qwen3.6-27B.

**Symptoms:** `patch -p1 --dry-run` of `patches/067-force-decode-window.patch.CANDIDATE` fails on `server_args.py` even when applied first, against a genuinely pristine tree (i.e. not a chain-ordering artifact — 068 and 069 also fail, compounding the same root cause).

**Root cause:** 067's hunk expects `triton_attention_split_tile_size: Optional[int] = None` to be immediately followed by `num_continuous_decode_steps: int = 1` in the `ServerArgs` dataclass, so it can insert `force_decode_window` between them. Our pinned tree (`FORK_REF=60ffa9501c2c6`) already has four newer fields inserted at that exact location (`prefill_only_disable_kv_cache`, `disable_radix_cache`, `disable_chunked_prefix_cache`, `disable_overlap_schedule`) from later upstream/fork changes the `.CANDIDATE` series was never rebased against. This is real semantic drift, not a cosmetic line-number offset — `patch`'s fuzz matching (already at fuzz=2) can't resolve it.

**Investigated:** 2026-07-01, caught at the dry-run phase with zero production impact — full detail in [[project_sglang_decode_topk_patch069]] and `docs/llm-hosting/decode-topk-pages-test-plan.md` (architectural compatibility with the DeltaNet hybrid was confirmed separately; the patches are sound, just stale against our tree).

**Status:** NO-GO per the test plan's gate — no hand-rebasing the patches (upstream hasn't validated a hand-patched variant). `.CANDIDATE` files and `FORK_REF` left untouched. Retest when upstream rebases the `.CANDIDATE` series past our pin, or incidentally on our next `FORK_REF` bump.

---

## Summary table

| # | Blocker | Severity | Workaround? | When to recheck |
|---|---------|----------|-------------|-----------------|
| 1 | MTP / Spec-V2 ROCm crash | **Critical** | No | Quarterly / SGLang ROCm CI |
| 2 | DeltaNet in_proj_ba weight loader | High | Yes (BF16 ignore list) | When qwen_gdn_weight_loader fixed upstream |
| 3 | gptq_marlin_repack missing on ROCm | Medium | Yes (Triton AWQ) | N/A (accept Triton path) |
| 4 | gfx1201 missing from sgl_kernel / AITER | Medium | Yes (mattbucci patches) | Discussion #12600 closed |
| 5 | No official gfx1201 Docker image | Operational | Yes (custom Dockerfile) | RDNA4 in official ROCm matrix |
| 6 | Prefix cache on DeltaNet hybrid (ROCm) | **Resolved** (v0.5.13) | MambaRadixCache (cache-on, batch 16/~63) + TP=1 store_cache patch | HiCache #24121 (cache+batch+full-context together) |
| 7 | decode-topk-pages CANDIDATE chain (067+068+069) drifted off FORK_REF | Low (optional, deferred) | No — patch needs upstream rebase | Fork rebases the CANDIDATE series, or our FORK_REF bump happens to realign |

**Bottom line:** The mattbucci fork resolves blockers 2–6 today (v0.5.13.post1). Blocker 6 (prefix cache) is fixed by native MambaRadixCache — cache is ON in prod, which fixes the long-context agent, at a batch cost (~63 @conc16 vs ~99 no-cache) that's the right tradeoff for the agentic workload. Blocker 1 (MTP) remains the single-stream gap vs vLLM but is moot here (spec is net-negative on dense RDNA4). Once #24121 (HiCache-for-hybrid) closes, retest to get cache + higher batch + full context together.
