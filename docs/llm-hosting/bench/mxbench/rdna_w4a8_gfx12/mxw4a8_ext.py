"""Build and load the gfx12 port of vllm#60413's MXFP4 W4A8 GEMV as torch.ops.mxw4a8.gemv.

  python3 mxw4a8_ext.py            # build (once) and smoke-test the op

The fake impl is registered here so torch.compile(fullgraph=True) can trace the op, as the PR
does for _rocm_C::mxfp4_w4a8_gemv in vllm/_custom_ops.py.
"""
import os

import torch

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD_DIR = os.environ.get("MXW4A8_BUILD_DIR", "/work/mx/build")
_loaded = False


def load_op(verbose: bool = False) -> None:
    global _loaded
    if _loaded:
        return
    # one arch only: the image presets PYTORCH_ROCM_ARCH to a list of nine targets (187 s build)
    os.environ["PYTORCH_ROCM_ARCH"] = os.environ.get("MXW4A8_ARCH", "gfx1201")
    os.makedirs(BUILD_DIR, exist_ok=True)
    from torch.utils.cpp_extension import load

    load(
        name="mxw4a8_ext",
        sources=[os.path.join(HERE, "mxfp4_w4a8_gemv.cu"), os.path.join(HERE, "mxw4a8_bindings.cpp")],
        extra_include_paths=[HERE],
        extra_cflags=["-O3"],
        extra_cuda_cflags=["-O3"],
        build_directory=BUILD_DIR,
        is_python_module=False,
        verbose=verbose,
    )

    @torch.library.register_fake("mxw4a8::gemv")
    def _gemv_fake(a, b_q, b_scale):
        return torch.empty((a.size(0), b_q.size(0)), dtype=a.dtype, device=a.device)

    _loaded = True


def gemv(a: torch.Tensor, b_q: torch.Tensor, b_scale: torch.Tensor) -> torch.Tensor:
    """a [M,K] fp16/bf16 (1<=M<=8), b_q [N,K/2] uint8 E2M1, b_scale [N,K/32] uint8 E8M0 -> [M,N]."""
    return torch.ops.mxw4a8.gemv(a, b_q, b_scale)


if __name__ == "__main__":
    load_op(verbose=True)
    a = torch.randn(2, 256, device="cuda", dtype=torch.bfloat16)
    wq = torch.randint(0, 256, (64, 128), dtype=torch.uint8, device="cuda")
    ws = torch.randint(122, 131, (64, 8), dtype=torch.uint8, device="cuda")
    out = gemv(a, wq, ws)
    torch.cuda.synchronize()
    print("op ok", tuple(out.shape), out.dtype, bool(torch.isfinite(out).all()))
