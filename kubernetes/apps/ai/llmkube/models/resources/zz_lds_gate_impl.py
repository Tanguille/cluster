# Import-hook patches for gfx1201, one per target module (PATCHES).
import contextlib
import glob
import importlib.abc
import os
import sys
import types

# An unclean exit leaves the CPU KV-offload region in the pod's /dev/shm, so every restart fails for space
# (247 restarts on 10-30). PID 1 is the API server, which runs before EngineCore creates this run's region.
if os.getpid() == 1:
    for _region in glob.glob("/dev/shm/vllm_offload_*.mmap"):
        with contextlib.suppress(OSError):
            os.unlink(_region)


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
# - M>=768 (prefill chunks): 256x128x64, 8 warps, 1 stage, 1.02-1.25x on all 5 shapes at M=768-2048; it loses
#   up to 0.80x at 520-640 (docs/llm-hosting/bench/downfix/triton_prefill.out), so 33-767 stays upstream.
# Upstream's gemm resolves the kernel global at call time. The proxy rewrites only the tile and launch kwargs of
# kernel[grid](a, b_q, scales, zp, c, M, N, ...) and forwards the rest, so a new kernel parameter cannot break it
# (a re-launched copy of the signature crash-looped prod on 10-14). If c is not (M, N) the layout moved: upstream tiles.
def _install_triton_tiles(hy):
    from vllm.triton_utils import triton

    if not hy._on_gfx12x():
        return
    kernel = hy._triton_w4a16_skinny_fmt_kernel

    class _Tiles:
        def __getitem__(self, grid):
            def launch(*args, **kw):
                c, M, N, g = args[4], args[5], args[6], kw.get("group_size")
                if 32 < M < 768 or g is None or tuple(c.shape) != (M, N):
                    return kernel[grid](*args, **kw)
                bm, bn, bk, warps = (16, 16, 128, 2) if M <= 32 else (256, 128, 64, 8)
                # Keep the scalar dequant path these gfx12 tiles were measured with.
                kw.update(PACKED_DEQUANT=False, BLOCK_M=bm, BLOCK_N=bn, BLOCK_K=min(bk, g), num_warps=warps, num_stages=1)
                return kernel[(triton.cdiv(M, bm), triton.cdiv(N, bn))](*args, **kw)

            return launch

    hy._triton_w4a16_skinny_fmt_kernel = _Tiles()
    print("[lds-gate-patch] Triton tiles M<=32 16x16x128 w2, M>=768 256x128x64 w8, 1 stage", file=sys.stderr, flush=True)


# UltraQuant keeps a max_model_len bf16 K/V concat buffer per layer (16 x 966 MiB), which OOMs the card;
# upstream shares only the dequant target, not this one. Layers run one at a time and rewrite [:seq_len]
# first, so one shared holder serves all. Required, so a moved symbol fails the boot; the fast kernels
# below are optional (SKIPPED).
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
    if UQ_FAST:
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
    # flash-attn's Triton AMD kernel JIT-compiles per max_seqlen_q/k (ttft-breakdown-2026-10.md). Varlen reads real
    # lengths from cu_seqlens and every call is causal with k >= q, so bucketing q to 256 and pinning k changes no result.
    fa = getattr(cls, "_flash_attn_varlen", None)
    if fa is None:
        print("[lds-gate-patch] SKIPPED flash-attn length buckets: no _flash_attn_varlen", file=sys.stderr, flush=True)
    else:
        def flash_attn_varlen(self, q, k, v, cu_seqlens_q, cu_seqlens_k, max_seqlen_q, max_seqlen_k):
            return fa(self, q, k, v, cu_seqlens_q, cu_seqlens_k, -(-max_seqlen_q // 256) * 256, self._max_model_len)

        cls._flash_attn_varlen = flash_attn_varlen
    # Decode: UQ_FAST=1 runs the RDNA4 kernel in uq_decode_fast.py; otherwise (and for sinks, windows or other
    # head sizes) upstream's launcher with its default geometry.
    if hasattr(module, "ultraquant_unified_attention"):
        launcher = module.ultraquant_unified_attention
        if UQ_FAST:
            from uq_decode_fast import uq_decode_fast as fast

        def decode(query, kv_cache, block_table, seq_lens, query_start_loc, scale, PiT=None, output=None, **kw):
            if fast and kw.get("max_query_len") == 1 and _fast_ok(kw.get("sinks"), kw.get("sliding_window"), query.shape[-1]):
                return fast(query, kv_cache, block_table, seq_lens, query_start_loc, scale,
                            PiT=PiT, output=output, max_seq_len=kw.get("max_seq_len"))
            return launcher(query, kv_cache, block_table, seq_lens, query_start_loc, scale, PiT=PiT, output=output, **kw)

        module.ultraquant_unified_attention = decode
    else:
        print("[lds-gate-patch] SKIPPED uq decode: no ultraquant_unified_attention", file=sys.stderr, flush=True)
    # fast= is the requested mode; prefill=/decode= show which fast kernels actually installed.
    print("[lds-gate-patch] UltraQuant shared continuation buffer, fast=%s (prefill=%s, decode=%s)"
          % (UQ_FAST, prefill is not None, fast is not None), file=sys.stderr, flush=True)


# UQ_FAST=0 keeps upstream kernels as the rollback path.
UQ_FAST = os.environ.get("UQ_FAST") == "1"


def _fast_ok(sinks, sliding_window, head_size):
    # uq_decode_fast.py covers D = 256 without sinks or a sliding window; the rest stays upstream.
    return sinks is None and not sliding_window and head_size == 256


PATCHES = {
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
