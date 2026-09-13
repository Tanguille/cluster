"""Load the candidate zz_lds_gate_impl.py ahead of the mounted one, check numerics and timing."""
import sys, statistics
import importlib.util
sys.meta_path[:] = [f for f in sys.meta_path if type(f).__name__ != "_Finder"]  # drop the mounted hook
spec = importlib.util.spec_from_file_location("zz_candidate", "/tmp/downfix/hook/zz_lds_gate_impl.py")
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)  # finder installed before vllm import
import torch
from vllm.model_executor.kernels.linear.mixed_precision import rdna_hybrid_w4a16 as hy
from vllm.utils.platform_utils import num_compute_units

cu = num_compute_units(); dev = torch.device("cuda"); G = 128
g = torch.Generator(device=dev).manual_seed(0)
orig = next(c.cell_contents for c in hy.triton_w4a16_skinny_fmt_gemm.__closure__ if callable(c.cell_contents) and not hasattr(c.cell_contents, "__path__"))
for N, K in [(5120, 17408), (34816, 5120), (5120, 6144)]:
    w_q = torch.randint(-128, 127, (N, K // 2), dtype=torch.int8, device=dev, generator=g)
    w_s = (torch.rand((N, K // G), device=dev, generator=g) * 0.01).to(torch.bfloat16)
    w_zp = torch.randint(0, 16, (N, K // G), device=dev, generator=g).to(torch.bfloat16)
    for M in (3, 4, 5, 32, 33, 4096):
        x = torch.randn((M, K), dtype=torch.bfloat16, device=dev, generator=g)
        a = torch.ops.vllm.rdna_hybrid_w4a16_apply(x, w_q, w_s, w_zp, None, cu, G)
        b = orig(x, w_q.view(torch.int32), w_s, G, zp=w_zp)
        print(f"N={N} K={K} M={M:4d} max|patched-orig|={(a.float() - b.float()).abs().max().item():.3e} "
              f"same={torch.equal(a, b)}")
