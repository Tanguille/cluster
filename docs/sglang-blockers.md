# SGLang Blockers for gfx1201 (RDNA4, RX 9700 / Qwen3.6-27B)

Tracking what needs to land upstream before SGLang can replace vLLM in production without depending on the mattbucci RDNA4 fork.

**Current approach:** `mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference` fork (v0.5.12 + 37 patches).
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

## Summary table

| # | Blocker | Severity | Workaround? | When to recheck |
|---|---------|----------|-------------|-----------------|
| 1 | MTP / Spec-V2 ROCm crash | **Critical** | No | Quarterly / SGLang ROCm CI |
| 2 | DeltaNet in_proj_ba weight loader | High | Yes (BF16 ignore list) | When qwen_gdn_weight_loader fixed upstream |
| 3 | gptq_marlin_repack missing on ROCm | Medium | Yes (Triton AWQ) | N/A (accept Triton path) |
| 4 | gfx1201 missing from sgl_kernel / AITER | Medium | Yes (mattbucci patches) | Discussion #12600 closed |
| 5 | No official gfx1201 Docker image | Operational | Yes (custom Dockerfile) | RDNA4 in official ROCm matrix |

**Bottom line:** The mattbucci fork resolves blockers 2–5 today. Blocker 1 (MTP) is the only hard performance gap vs vLLM. Once SGLang ROCm CI shows speculative decoding passing, retest with a single-card configuration on the fork.
