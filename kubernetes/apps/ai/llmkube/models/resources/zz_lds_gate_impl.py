# Import-hook patches for gfx1201, one per target module (PATCHES).
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


# gfx12 W4A16 Triton tiles for Qwen3.8 on the 64-CU R9700 (upstream's were tuned on Llama-3.1-8B shapes):
# - M<=32 (down_proj from M=3, every layer at M=6-32): 16x16x128, 2 warps, 1 stage, 1.04-1.31x on all 30 cells
#   (docs/llm-hosting/bench/downfix/triton_m32.out).
# - M>512 (prefill chunks): 256x128x64, 8 warps, 1 stage, 1.10-1.25x on all 5 shapes at M=2048
#   (docs/llm-hosting/bench/downfix/triton_prefill.out). 33-512 stays upstream.
# The custom op resolves this module global at call time, so replacing it needs no re-registration.
def _install_triton_tiles(hy):
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
        if 32 < M <= 512:
            return orig(a, b_q, scales, group_size, zp_bias, zp)
        bm, bn, bk, warps = (16, 16, 128, 2) if M <= 32 else (256, 128, 64, 8)
        N = b_q.shape[0]
        c = torch.empty((M, N), dtype=a.dtype, device=a.device)
        grid = (triton.cdiv(M, bm), triton.cdiv(N, bn))
        strides = (b_q.stride(0), a.stride(0)) if has_strides else ()
        kernel[grid](
            # the zp pointer is unused when HAS_ZP is False; any tensor will do
            a, b_q, scales, zp if zp is not None else scales, c,
            M, N, K, K // 8, K // group_size, *strides,
            group_size=group_size, ZP_BIAS=zp_bias, HAS_ZP=zp is not None,
            BLOCK_M=bm, BLOCK_N=bn, BLOCK_K=min(bk, group_size), num_warps=warps, num_stages=1,
        )
        return c

    hy.triton_w4a16_skinny_fmt_gemm = gemm
    print("[lds-gate-patch] Triton tiles M<=32 16x16x128 w2, M>512 256x128x64 w8, 1 stage", file=sys.stderr, flush=True)


# UltraQuant keeps a max_model_len bf16 K/V buffer per layer (16 x 966 MiB), which OOMs the card. Layers run
# one at a time and rewrite [:seq_len] first, so one shared holder serves all. Required, so a moved symbol
# fails the boot; the fast kernels below are optional (SKIPPED).
def _patch_uq(module):
    try:
        cls = module.UltraQuantAttentionImpl
        orig = cls._ultraquant_continuation_prefill
    except AttributeError as e:
        raise RuntimeError("[lds-gate-patch] UltraQuant shared continuation buffer cannot install: %r" % e) from e
    shared = types.SimpleNamespace()
    # Chunks up to the threshold: uq_prefill_fast reads the prefix once per 2 tokens, not per token
    # (6.8 vs 15.9 ms per layer, 128 tokens at 48K); above it dequant + flash-attn stays faster.
    prefill, small, fast = None, 0, None
    if UQ_CFG["fast"]:
        if hasattr(module, "_CONTINUATION_DECODE_THRESHOLD"):
            from uq_decode_fast import uq_prefill_fast as prefill

            # 0 routes every chunk through continuation() below. ponytail: ineligible small chunks (sinks,
            # window) now take dequant + flash-attn, which ignores the window; Qwen3.8 has neither.
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
    # Decode: UQ_FAST=1 runs the RDNA4 kernel in uq_decode_fast.py; otherwise (and for sinks, windows or other
    # head sizes) upstream's launcher with this card's geometry.
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
    "vllm.model_executor.kernels.linear.mixed_precision.rdna_hybrid_w4a16": _install_triton_tiles,
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
