# TEST ONLY -- raises the W4A16 skinny-GEMM LDS gate to match the C++ kernel.
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
