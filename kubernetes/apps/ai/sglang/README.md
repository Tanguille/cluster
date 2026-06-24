# sglang — Qwen3.6-27B on RDNA4

Replaces the `vllm` app as the primary inference backend for `qwen-3.6`. Serves the
dense **Qwen3.6-27B-AWQ** on the single AMD R9700 (gfx1201/RDNA4, 32 GB) via the
**mattbucci SGLang RDNA4 fork** (v0.5.13.post1 + 46 patches, torch 2.12+rocm7.2,
Triton 3.6), TP=1.

## Validated performance (single card, tools on, thinking on)

| concurrency | aggregate decode | prefill (PP) | notes |
|---|---|---|---|
| 1 | 16 tok/s | 528–888 tok/s | single-stream |
| 8 | 35 tok/s | | |
| 16 | 67 tok/s | | |
| 24–32 | **~99 tok/s** | | plateau |

Clears the ≥10 single-stream and ≥60 aggregate targets with the preferred dense
model (its prefill is ~5× the MoE 35B-A3B's). Rejected after testing: MoE 35B-A3B
(weaker PP, not needed), MTP/EAGLE3/DFlash/ngram (net-negative on the DeltaNet
hybrid, or hard-reject grammar/tool-calling), fp4 KV (hard-blocked on non-CUDA),
Hadamard-KV (unwired + decode-negative online on gfx1201).

## ⚠️ Runtime-from-PVC (reproducibility debt)

The container image (`rocm/dev-ubuntu-24.04:7.2.4-complete`) is **only the ROCm
runtime base**. The actual engine runs from the prebuilt conda env on the `sglang`
PVC at `/cache/sglang`:

- `/cache/sglang/conda` — env `sglang-triton36-v0513` (torch 2.12+rocm7.2, SGLang
  fork, the 3 native gfx1201 HIP kernels: sgl_kernel, awq_gemv, wvSplitK INT4 MoE)
- `/cache/sglang/repo` — fork source incl. `scripts/launch.sh`, `common.sh`, the
  `qwen3.6_devrole_chat_template.jinja`
- `/cache/sglang/sglang-src` — patched, editable SGLang source
- `/cache/sglang/triton` — persisted Triton JIT cache
- `/cache/hf` — Qwen3.6-27B-AWQ snapshot (`mattbucci/Qwen3.6-27B-AWQ`)

This means **the serving behavior is not fully reproducible from git** — it depends
on the PVC built out-of-band (`scripts/setup.sh` from the fork, run in the ROCm base).
The PVC also carries these runtime fixes applied during bring-up:

1. `jit_kernel/kvcache.py` `can_use_store_cache()` → `return False` — the fp8 KV
   `store_cache` JIT kernel hits `hipErrorIllegalState` on TP=1; falls back to the
   naive torch KV store.
2. `scripts/common.sh` `PYTORCH_HIP_ALLOC_CONF` → env-overridable empty (torch-2.12
   faults with `expandable_segments` on RDNA4).
3. `srt/server_args.py` extra_buffer device gate → add `is_hip()` (enables prefix
   caching on ROCm; **not used in this no-cache config** but kept for the cache mode).
4. `qwen3.6_devrole_chat_template.jinja` — [QwenLM/Qwen3.6#131](https://github.com/QwenLM/Qwen3.6/issues/131)
   fix (guard historical `<think>` on `reasoning_content` so empty blocks don't drift
   the prefix). Needed for correct `preserve_thinking`.
5. `pip uninstall kernels` — transformers 5.6's hub-kernels integration builds a
   `LayerRepository` without a revision and crashes `import sglang`; SGLang uses its own
   compiled kernels, so dropping the `kernels` pkg is safe.

**Follow-up (blocked):** the clean fix is to bake the env into a versioned OCI image
(`docker/sglang-rdna4/` already builds `ghcr.io/tanguille/sglang-rdna4`) and switch the
HelmRelease to it. But the cluster currently **cannot pull never-cached images** — Spegel's
upstream fall-through is broken, so any image not already on a node fails (even `alpine`).
Until that's fixed, runtime-from-PVC on the *cached* ROCm base is the only viable path.

## Caching vs batch (ROCm tradeoff)

On the DeltaNet hybrid, prefix cache and high batch are mutually exclusive on RDNA4:
this config runs **no-cache** (`--disable-radix-cache`, overlap scheduler ON) for the
~99 tok/s batch. The patched `extra_buffer` mode gives prefix cache (5–6× TTFT,
validated correct) but caps batch ~24 — switch to it only for single-user agentic use.

## Thinking / preserve_thinking

Thinking is on (`--reasoning-parser qwen3`). To preserve prior-turn reasoning across
a multi-turn agent loop (and avoid the empty-arg tool-call loop,
[earendil-works/pi#3325](https://github.com/earendil-works/pi/issues/3325)), clients
pass per-request `chat_template_kwargs: {"enable_thinking": true, "preserve_thinking": true}`.
The served template (patch 4 above) renders preserved `<think>` blocks correctly.
