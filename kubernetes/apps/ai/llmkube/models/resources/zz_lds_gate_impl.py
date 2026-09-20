# Raises the W4A16 skinny-GEMM LDS gate to match the C++ kernel.
#
# rdna_hybrid_w4a16.py dispatches:
#     if M <= MAX_SKINNY_BATCH_SIZE and K * M <= LDS_CAPACITY_ELEMENTS:
#         ops.wvSplitK_int4_g(...)   # fast HIP skinny GEMM
#     else:
#         triton_w4a16_skinny_fmt_gemm(...)   # ~2x slower
#
# LDS_CAPACITY_ELEMENTS is 32768 (64 KiB / 2). But csrc/rocm/skinny_gemms_int4.cu
# checks `K_in * N_in <= max_lds_len * 1.2` (= 39321) and selects a "medium"
# kernel variant above the plain LDS size, keeping the activation prefix in LDS
# and streaming the remainder from global.
#
# So the Python gate is stricter than the kernel. down_proj (K=17408) at M=2 is
# K*M = 34816: inside the C++ medium window, outside the Python gate. Verified
# in-container on gfx1201: the HIP kernel at M=2 returns correct results
# (relative error 0.0014 vs an fp32 reference, BETTER than Triton's 0.0093) and
# is ~1.8x faster. M=3 (52224) is refused by the kernel itself with a clean
# RuntimeError, so an over-relaxed gate fails loudly rather than silently.
#
# Equivalent to upstream vllm PR #52619 (one-line, unreviewed), which measured
# 1.66x kernel / +63% e2e decode on gfx1151.
import importlib.abc
import sys

TARGET = "vllm.model_executor.kernels.linear.mixed_precision.rdna_hybrid_w4a16"
NEW_LIMIT = int(32768 * 1.2)  # 39321, matches max_lds_len * 1.2 in the C++


class _PatchLoader(importlib.abc.Loader):
    def __init__(self, loader):
        self._loader = loader

    def create_module(self, spec):
        return self._loader.create_module(spec)

    def exec_module(self, module):
        self._loader.exec_module(module)
        old = getattr(module, "LDS_CAPACITY_ELEMENTS", None)
        module.LDS_CAPACITY_ELEMENTS = NEW_LIMIT
        print(
            "[lds-gate-patch] LDS_CAPACITY_ELEMENTS %s -> %s" % (old, NEW_LIMIT),
            file=sys.stderr, flush=True,
        )
        _install_small_m_triton_config(module)


# Second patch, same module: the Triton tile config for the gfx12x M <= 32
# branch. Even at 39321 the gate only covers down_proj (K=17408) up to M=2;
# at M=3-5, where production runs (3.8 concurrent on average), it takes the
# Triton path, and so does every layer at M=6-32 (chunked-prefill tails).
#
# Shipped: BLOCK 16x16x128, 4 warps, default stages. Swept in-pod 2026-09-13
# under CUDA-graph replay across all six Qwen3.8 shapes at M=3..32
# (docs/llm-hosting/bench/downfix/triton_m32.out): 2 warps + 1 stage wins
# 14/30 cells and regresses none, 1.16-1.31x on down_proj, 1.26-1.30x on
# gate_up, >= 1.04x everywhere. Split-K over the HIP skinny kernel was also
# measured and rejected: 1.2-1.3x at M=3-5 but 0.6-0.75x at M=1-2 and
# 0.5-0.7x on prefill tails, because the weight must be stored in K-chunks.
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

    def gemm(a, b_q, scales, group_size, zp_bias=8, zp=None):
        M, K = a.shape
        if M > 32:
            return orig(a, b_q, scales, group_size, zp_bias, zp)
        N = b_q.shape[0]
        c = torch.empty((M, N), dtype=a.dtype, device=a.device)
        grid = (triton.cdiv(M, 16), triton.cdiv(N, 16))
        kernel[grid](
            # the zp pointer is unused when HAS_ZP is False; any tensor will do
            a, b_q, scales, zp if zp is not None else scales, c,
            M, N, K, K // 8, K // group_size,
            group_size=group_size, ZP_BIAS=zp_bias, HAS_ZP=zp is not None,
            BLOCK_M=16, BLOCK_N=16, BLOCK_K=min(128, group_size), num_warps=2, num_stages=1,
        )
        return c

    hy.triton_w4a16_skinny_fmt_gemm = gemm
    print("[lds-gate-patch] Triton M<=32 tile 16x16x128, 2 warps, 1 stage", file=sys.stderr, flush=True)


class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name != TARGET:
            return None
        for finder in sys.meta_path:
            if finder is self:
                continue
            spec = finder.find_spec(name, path, target)
            if spec and spec.loader:
                spec.loader = _PatchLoader(spec.loader)
                return spec
        return None


sys.meta_path.insert(0, _Finder())
print("[lds-gate-patch] import hook installed", file=sys.stderr, flush=True)
