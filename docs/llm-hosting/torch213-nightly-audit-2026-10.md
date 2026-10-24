# torch 2.13 nightly audit, SSM state dtype and compile-cache growth, 2026-10-20/21

qwen38-27b-vllm on control-1 (R9700, gfx1201). Nightly `fdd4e10` (vllm
`0.32.1rc1.dev117+gdf3bd094a`, torch `2.13.0+rocm10.0.0`, Triton `3.8.0+git669b31ac`, AITER
0.1.25), merged in #5954.

## Torch 2.12 to 2.13: nothing to flip, no gain

The bump is vllm#62276 (CI-driven: `torch._inductor.config.batch_invariant` needs 2.13). Of the
119 vLLM commits between `49d8c1a` and `fdd4e10`, none touch `ultraquant_attn`,
`rdna_hybrid_w4a16`, the FLA/GDN code, `platforms/rocm.py` or the compilation config
(`gh api repos/vllm-project/vllm/commits?path=...`). The torch notes that matter on RDNA are
Inductor-only (`num_warps` on wave32, #181112; ROCm combo-kernel options, #180277).

`longctx.py 18000 qwen-3.8 4000 3`, same pod type, three reps:

| nightly | tok/s | ITL ms | TTFT s |
| --- | ---: | ---: | ---: |
| `3ccadf5` | 34.3 | 29.15 | n/a |
| `49d8c1a` | 33.80-33.95 | 29.5 | n/a |
| `fdd4e10` | 33.43-33.52 | 29.83-29.92 | 3.45-3.50 |

The 1-2.5% gap is inside the fast/slow band noise ([band doc](gfx1201-fast-slow-band-2026-09-12.md)).

Live-pod gates on `fdd4e10` (no clone): vision 2/2, tools 12/12 single and 12/12 x5, GSM8K
147/150 (gate 143), NLL 2.08676 (-0.00024 vs e885).

## Patches

- Upstream gfx12 W4A16 tiles are unchanged, so the Triton tile hook stays.
- Upstream now shares only the UltraQuant dequant `k_buf`/`v_buf` through the workspace manager;
  the per-layer K/V concat buffer remains, so the shared-holder patch stays.
- The custom fallback geometry was unused on the production path (`UQ_FAST=1`, head 256).
  It was removed on 2026-10-24; `UQ_FAST=0` now uses upstream defaults.

## Warnings that are not bugs

- "JIT during inference" for `attn_fwd`, `_ultraquant_full_dequant_kv`, the skinny W4A16 kernel,
  `uq_prefill_fast_kernel`, `reduce_segments` and `fused_sigmoid_gating_delta_rule_update_kernel`:
  the monitor hooks Triton post-compile, which also fires on disk-cache loads. No `/cache/triton`
  entry was newer than 2026-10-15, so nothing compiled.
- The generation-config override is deliberate: the model's `generation_config.json` is the
  thinking-mode set and the LiteLLM `qwen-3.8-fast` alias overrides it with the non-thinking set.

## SSM state dtype: bf16 stays

The model config says `mamba_ssm_dtype: float32`; we run bf16 for both mamba caches. Measured on an
unrouted clone (same ISVC, `--mamba-ssm-cache-dtype float32`, scratch emptyDir for the offload
tier) with production paused:

| | bf16 (live) | fp32 clone |
| --- | ---: | ---: |
| NLL mean, 20 texts (`qualitygate.py nll --compare e885`) | 2.08676 | 2.08640 |
| decode tok/s (`longctx.py 18000 qwen-3.8 4000 3`) | 33.43-33.52 | 33.23 median |
| KV pool tokens (boot log `GPU KV cache size`) | 469,489 | 451,780 (-17,709, -3.8%) |

No quality or speed gain, 3.8% less KV: keep bf16. Needles passed at 17.5K (3/3) and 52.5K (2/3
depths); the clone was then evicted for filling its 20Gi scratch volume, so the 175K needle and the
offload-reload check did not run. Control-1 sits near the 160Gi kv-offload guard floor, so do not
retry with a bigger volume.

## Compile cache growth

`/cache/vllm/torch_compile_cache/torch_aot_compile` gets one entry per build and was never pruned:
189 entries, 17.5 GB (`du -sh`), 185 older than two days. Pruned live to the 4 newest (145 MB, `/cache`
free 161G to 171G). The `qwen38-27b-vllm-compile-cache-prune` CronJob now bounds it.

## Nightly ad061ff compatibility, 2026-10-24

[PR #6123](https://github.com/Tanguille/cluster/pull/6123) updates `d92e7eb` to `ad061ff`:
the image metadata identifies vLLM `83c078d95` → `c50aca144` (74 commits). The torch,
Triton, AITER and ROCm base layers are identical between these two images.

Upstream `96bbaa300f` adds the required `PACKED_DEQUANT` argument to the W4A16 Triton
kernel. The hook and both `bench/downfix/triton_*_sweep.py` launchers pass `False` to
preserve the measured gfx1201 path. Named strides replace per-GEMM dictionary building;
tile geometry is unchanged. Roll back the image and these calls together.
The UltraQuant backend, Triton launcher, reduction kernel, cache store and format
sources are byte-identical; the shared-buffer and fast-kernel patches stay.

Validation: hook routing, padded strides, zero-points and UQ fallback checks pass.
All three W4A16 callers failed signature binding against image `ad061ff` before the
fix and passed afterward. `validate-pr.sh` passed Flate, shellcheck and 24 guardrail tests.

Removed the `_patch_uq_ops` proxy and `UQ_STOCK/SPLITS/TILE/WARPS/STAGES` knobs. The last
documented tuned-fallback GPU use was the October 7 benchmark; all 94 retained hook
initializations from October 10–24 reported fast kernels enabled. These logs do not
count individual fallback calls. `UQ_FAST=0` still works, using upstream defaults;
the old benchmark results do not describe its performance after this removal.

GPU execution remains untested. Upstream `d561af5dfc` changes hybrid prefix-cache
partial-tail reuse, which this service exercises. Before deployment, run
`bench/qualitygate.py` and `bench/prodshape.py` on an unrouted clone with production
paused, recording NLL/agreement, prefix-cache reuse and boot-log KV capacity against
[the baseline](ttft-breakdown-2026-10.md). No cluster resources were changed for this audit.
