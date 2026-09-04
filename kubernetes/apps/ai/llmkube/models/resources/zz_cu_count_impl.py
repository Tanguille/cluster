# Passes the real CU count to the W4A16 skinny GEMM, and ONLY to it.
#
# HIP reports WGPs, not CUs, when the device is in WGP mode, so
# num_compute_units() returns 32 on this 64-CU gfx1201 part. wvSplitK_int4_g
# uses that number to size its K-split, so it splits half as many ways as the
# hardware allows. Measured: ~21% on down_proj at M=1, ~2% at M=2.
#
# Deliberately NOT a global override of num_compute_units. Other live callers
# on this model -- flash_linear_attention/ops/layernorm_guard.py (the GDN linear
# attention path), v1/worker/gpu_model_runner.py, v1/sample/ops/topk_topp_triton.py
# -- also read it, and none of them were measured with 64. A global patch would
# silently change kernels nobody benchmarked. This wraps only
# RDNAHybridW4A16LinearKernel.apply_weights, so the override is live for exactly
# the call that was measured and is restored immediately afterwards.
#
# The wrapper delegates to the original method rather than copying its body, so
# an upstream change to that method is picked up rather than silently shadowed.
#
# Thread-safety: vLLM V1 executes the model on a single thread per engine core,
# so the swap cannot interleave with another forward. If vLLM ever moves to
# multi-threaded execution within a rank, this becomes racy and must be replaced
# by passing cu_count explicitly.
#
# If upstream renames the module or class, the hook never fires and the engine
# runs unpatched at 32. Confirm from the startup log, do not assume.
import importlib.abc
import sys

TARGET = "vllm.model_executor.kernels.linear.mixed_precision.rdna_hybrid_w4a16"
REAL_CU_COUNT = 64


class _PatchLoader(importlib.abc.Loader):
    # Hold the ORIGINAL loader, not the spec: find_spec() overwrites spec.loader
    # with this wrapper, so going back through the spec recurses into itself.
    def __init__(self, loader):
        self._loader = loader

    def create_module(self, spec):
        return self._loader.create_module(spec)

    def exec_module(self, module):
        self._loader.exec_module(module)
        kernel_cls = getattr(module, "RDNAHybridW4A16LinearKernel", None)
        if kernel_cls is None or not hasattr(kernel_cls, "apply_weights"):
            print("[cu-count-patch] RDNAHybridW4A16LinearKernel.apply_weights missing, "
                  "leaving cu_count at the platform value",
                  file=sys.stderr, flush=True)
            return

        original = kernel_cls.apply_weights

        def apply_weights(self, *args, **kwargs):
            import vllm.utils.platform_utils as platform_utils

            real = platform_utils.num_compute_units
            platform_utils.num_compute_units = lambda *a, **k: REAL_CU_COUNT
            try:
                return original(self, *args, **kwargs)
            finally:
                platform_utils.num_compute_units = real

        kernel_cls.apply_weights = apply_weights
        print(f"[cu-count-patch] W4A16 cu_count 32 -> {REAL_CU_COUNT}",
              file=sys.stderr, flush=True)


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
print("[cu-count-patch] import hook installed", file=sys.stderr, flush=True)
