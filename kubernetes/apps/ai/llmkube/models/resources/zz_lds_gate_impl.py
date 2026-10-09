# Import-hook patches for gfx1201, one section per target module (PATCHES).
#
# Removed with the 81198e97 nightly: the W4A16 LDS gate raise (upstream since vllm#52619,
# MEDIUM_SKINNY_LIMIT_ELEMENTS) and the aiter attn_3d num_stages 1 patch (fp8 only; UltraQuant
# replaces that backend). An fp8 rollback wants the latter back from git history.
import importlib.abc
import os
import sys
import types


class _PatchLoader(importlib.abc.Loader):
    def __init__(self, loader, patch):
        self._loader = loader
        self._patch = patch

    def create_module(self, spec):
        return self._loader.create_module(spec)

    def exec_module(self, module):
        self._loader.exec_module(module)
        self._patch(module)


# The Triton tile config for the gfx12x M <= 32 branch. Upstream's 39321-element
# medium gate only covers down_proj (K=17408) up to M=2; at M=3-5, where
# production runs (3.8 concurrent on average), it takes the Triton path, and so
# does every layer at M=6-32 (chunked-prefill tails).
#
# Upstream tile: BLOCK 16x16x128, 4 warps, default stages. Here: 2 warps + 1
# stage, swept in-pod under CUDA-graph replay across all six Qwen3.8 shapes at
# M=3..32 (docs/llm-hosting/bench/downfix/triton_m32.out): wins 14/30 cells and
# regresses none, 1.16-1.31x on down_proj, 1.26-1.30x on gate_up, >= 1.04x
# everywhere. Split-K over the HIP skinny kernel was also measured and rejected:
# 1.2-1.3x at M=3-5 but 0.6-0.75x at M=1-2 and 0.5-0.7x on prefill tails,
# because the weight must be stored in K-chunks.
#
# _rdna_hybrid_w4a16_apply_impl resolves triton_w4a16_skinny_fmt_gemm by
# module global at call time, so replacing it here reaches the registered
# custom op without re-registering it.
def _install_small_m_triton_config(hy):
    import torch
    from vllm.triton_utils import triton

    if not hy._on_gfx12x():
        return
    orig = hy.triton_w4a16_skinny_fmt_gemm
    kernel = hy._triton_w4a16_skinny_fmt_kernel
    # Newer vllm (#56301) passes the row strides between num_groups and group_size.
    has_strides = "stride_bn" in kernel.arg_names

    def gemm(a, b_q, scales, group_size, zp_bias=8, zp=None):
        M, K = a.shape
        if M > 32:
            return orig(a, b_q, scales, group_size, zp_bias, zp)
        N = b_q.shape[0]
        c = torch.empty((M, N), dtype=a.dtype, device=a.device)
        grid = (triton.cdiv(M, 16), triton.cdiv(N, 16))
        strides = (b_q.stride(0), a.stride(0)) if has_strides else ()
        kernel[grid](
            # the zp pointer is unused when HAS_ZP is False; any tensor will do
            a, b_q, scales, zp if zp is not None else scales, c,
            M, N, K, K // 8, K // group_size, *strides,
            group_size=group_size, ZP_BIAS=zp_bias, HAS_ZP=zp is not None,
            BLOCK_M=16, BLOCK_N=16, BLOCK_K=min(128, group_size), num_warps=2, num_stages=1,
        )
        return c

    hy.triton_w4a16_skinny_fmt_gemm = gemm
    print("[lds-gate-patch] Triton M<=32 tile 16x16x128, 2 warps, 1 stage", file=sys.stderr, flush=True)


# UltraQuant (vllm#57057) caches a max_model_len-sized bf16 K and V buffer on every
# attention layer (16 x 966 MiB = 15 GiB here), grown on the first continuation chunk
# over 128 tokens, and OOMs the 32 GB card. Layers run one at a time on one stream and
# rewrite [:seq_len] before reading it, so one shared holder serves them all. Without it
# the first long continuation OOMs mid-traffic, so a moved symbol fails the boot instead.
# The fast kernels below stay optional (SKIPPED).
def _patch_uq(module):
    try:
        cls = module.UltraQuantAttentionImpl
        orig = cls._ultraquant_continuation_prefill
    except AttributeError as e:
        raise RuntimeError("[lds-gate-patch] UltraQuant shared continuation buffer cannot install: %r" % e) from e
    shared = types.SimpleNamespace()
    # Upstream runs chunks up to _CONTINUATION_DECODE_THRESHOLD tokens as one synthetic decode
    # per token, re-reading the whole prefix per token. uq_prefill_fast reads it once per 2
    # tokens: 6.8 vs 15.9 ms per layer for 128 tokens on a 48K prefix. Above the threshold
    # dequant + flash-attn stays faster (14.9 vs 24.1 ms at 512 tokens), so it keeps them.
    prefill, small, fast = None, 0, None
    if UQ_CFG["fast"]:
        if hasattr(module, "_CONTINUATION_DECODE_THRESHOLD"):
            from uq_decode_fast import uq_prefill_fast as prefill

            # Zero routes every continuation chunk through the override below.
            # ponytail: ineligible small chunks (sinks, sliding window) now take dequant + flash-attn,
            # which ignores the window; Qwen3.8 has neither. Rebuild the synthetic decode if one does.
            small, module._CONTINUATION_DECODE_THRESHOLD = module._CONTINUATION_DECODE_THRESHOLD, 0
        else:
            print("[lds-gate-patch] SKIPPED uq fast continuation: no _CONTINUATION_DECODE_THRESHOLD",
                  file=sys.stderr, flush=True)

    def continuation(self, *, layer, **kw):
        q = kw["query"]
        if prefill and q.shape[0] <= small and _fast_ok(self.sinks, self.sliding_window, q.shape[-1]):
            return prefill(q, kw["kv_cache"], kw["block_table"], kw["cached_len"], self.scale, PiT=kw["PiT"])
        return orig(self, layer=shared, **kw)

    cls._ultraquant_continuation_prefill = continuation
    # Decode. UQ_FAST=1: the RDNA4 kernel in uq_decode_fast.py (mounted next to this file),
    # bit-trick FP4 -> fp16 and fp16 WMMA instead of the generic dot_scaled decomposition.
    # Otherwise, and for sinks, sliding window or other head sizes, the upstream launcher with
    # this card's geometry. Upstream launches the 3D split-KV kernel with 16 splits, 16-token
    # tiles, 2 warps, 3 stages: 64 programs x 2 waves = one wave per SIMD on 64 CUs, no
    # latency hiding, 47 GB/s at 64K.
    if hasattr(module, "ultraquant_unified_attention"):
        launcher = module.ultraquant_unified_attention
        if UQ_CFG["fast"]:
            from uq_decode_fast import uq_decode_fast as fast

        def decode(query, kv_cache, block_table, seq_lens, query_start_loc, scale, PiT=None, output=None, **kw):
            if kw.get("max_query_len") == 1:
                if fast and _fast_ok(kw.get("sinks"), kw.get("sliding_window"), query.shape[-1]):
                    return fast(query, kv_cache, block_table, seq_lens, query_start_loc, scale,
                                PiT=PiT, output=output, max_seq_len=kw.get("max_seq_len"))
                if not UQ_CFG["stock"]:
                    kw["tile_size"], kw["num_kv_splits"] = UQ_CFG["tile"], UQ_CFG["splits"]
            return launcher(query, kv_cache, block_table, seq_lens, query_start_loc, scale, PiT=PiT, output=output, **kw)

        module.ultraquant_unified_attention = decode
    else:
        print("[lds-gate-patch] SKIPPED uq decode: no ultraquant_unified_attention", file=sys.stderr, flush=True)
    # fast= is the requested mode; prefill=/decode= show which fast kernels actually installed.
    print("[lds-gate-patch] UltraQuant shared continuation buffer + decode geometry, fast=%s (prefill=%s, decode=%s)"
          % (UQ_CFG["fast"], prefill is not None, fast is not None), file=sys.stderr, flush=True)


# UQ_* env vars are the sweep and rollback knobs; UQ_STOCK=1 leaves upstream geometry alone.
UQ_CFG = {
    "stock": os.environ.get("UQ_STOCK") == "1",
    # Swept in a pod at 64K (3D kernel, 16 layer-calls): stock 50.1 ms/token, this 13.5 ms.
    "splits": int(os.environ.get("UQ_SPLITS", "32")),
    "tile": int(os.environ.get("UQ_TILE", "32")),
    "warps": int(os.environ.get("UQ_WARPS", "8")),
    "stages": int(os.environ.get("UQ_STAGES", "1")),
    # UQ_FAST=1 routes pure decode and continuation chunks up to 128 tokens to uq_decode_fast.py,
    # at that file's default geometry.
    "fast": os.environ.get("UQ_FAST") == "1",
}


def _fast_ok(sinks, sliding_window, head_size):
    # uq_decode_fast.py covers D = 256 without sinks or a sliding window; the rest stays upstream.
    return sinks is None and not sliding_window and head_size == 256


def _patch_uq_ops(module):
    try:
        kernel = module.kernel_ultraquant_unified_attention_3d
    except AttributeError as e:
        print("[lds-gate-patch] SKIPPED ultraquant ops: %r" % e, file=sys.stderr, flush=True)
        return
    if UQ_CFG["stock"]:
        return

    class _Proxy:
        def __getitem__(self, grid):
            launch = kernel[grid]

            def run(*a, **kw):
                kw["num_warps"], kw["num_stages"] = UQ_CFG["warps"], UQ_CFG["stages"]
                return launch(*a, **kw)

            return run

        def __getattr__(self, name):
            return getattr(kernel, name)

    module.kernel_ultraquant_unified_attention_3d = _Proxy()


PATCHES = {
    "vllm.v1.attention.ops.ultraquant.triton_unified_attention": _patch_uq_ops,
    "vllm.model_executor.kernels.linear.mixed_precision.rdna_hybrid_w4a16": _install_small_m_triton_config,
    "vllm.v1.attention.backends.ultraquant_attn": _patch_uq,
}


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        patch = PATCHES.get(name)
        if patch is None:
            return None
        for finder in sys.meta_path:
            if finder is self:
                continue
            spec = finder.find_spec(name, path, target)
            if spec and spec.loader:
                spec.loader = _PatchLoader(spec.loader, patch)
                return spec
        return None


sys.meta_path.insert(0, _Finder())
print("[lds-gate-patch] import hook installed", file=sys.stderr, flush=True)
