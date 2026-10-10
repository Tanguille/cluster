#!/usr/bin/env python3
"""W4A16 Triton tile sweep at prefill size on the five Qwen3.8 shapes, graph replay. usage: triton_prefill_sweep.py [M ...]

Baseline = upstream's own gemm at every M (tuned on Llama-3.1-8B shapes). Ceiling = bf16 torch.mm (hipBLASLt) of the
same shape. Candidates must match the baseline element-wise (rtol 2e-2, atol 2e-2 x max|ref|; reduction order differs
with BLOCK_K) and be finite. One 3-replay screen first: a candidate over 1.5x the baseline is not timed further.
"""
import itertools
import sys
from functools import partial

import torch

# The import hook (zz_lds_gate_impl) swaps the gemm below for its own tiles; drop its finder so vllm loads untouched.
sys.meta_path[:] = [f for f in sys.meta_path if type(f).__module__ != "zz_lds_gate_impl"]
from vllm.model_executor.kernels.linear.mixed_precision import rdna_hybrid_w4a16 as hy
from vllm.triton_utils import triton

from triton_m32_sweep import G, SHAPES, dev, timeit

MS = [int(a) for a in sys.argv[1:sys.argv.index("--port") if "--port" in sys.argv else None]] or [2048]  # runner appends --port N
KERNEL = hy._triton_w4a16_skinny_fmt_kernel
HAS_STRIDES = "stride_bn" in KERNEL.arg_names  # vllm#56301 passes the row strides
CFGS = [c for c in itertools.product((16, 32, 64, 128, 256), (16, 32, 64, 128), (64, 128), (1, 2, 4, 8), (None, 1))
        if 64 <= c[0] * c[1] // c[3] <= 4096]  # (BM, BN, BK, warps, stages); accumulator elements per warp in [64, 4096]


def ms(fn, **kw):
    return timeit(fn, **kw) / 1e3  # timeit returns microseconds


def launch(x, w_q, w_s, w_zp, N, K, BM, BN, BK, warps, stages):
    M = x.shape[0]
    c = torch.empty((M, N), dtype=x.dtype, device=dev)
    kw = {} if stages is None else {"num_stages": stages}
    strides = (w_q.stride(0), x.stride(0)) if HAS_STRIDES else ()
    KERNEL[(triton.cdiv(M, BM), triton.cdiv(N, BN))](
        x, w_q, w_s, w_zp, c, M, N, K, K // 8, K // G, *strides,
        group_size=G, ZP_BIAS=8, HAS_ZP=True, BLOCK_M=BM, BLOCK_N=BN, BLOCK_K=min(BK, G),
        num_warps=warps, **kw)
    return c


def sweep(M, g):
    for N, K, label in SHAPES:
        w_q = torch.randint(-2**31, 2**31 - 1, (N, K // 8), dtype=torch.int32, device=dev, generator=g)
        w_s = (torch.rand((N, K // G), device=dev, generator=g) * 0.01).to(torch.bfloat16)
        # AWQ is asymmetric: packed int32 zero-points [N//8, K//G], row n at bits 4*(n%8) of word n//8
        w_zp = torch.randint(-2**31, 2**31 - 1, (N // 8, K // G), dtype=torch.int32, device=dev, generator=g)
        x = torch.randn((M, K), dtype=torch.bfloat16, device=dev, generator=g)
        flops = 2 * M * N * K
        upstream = partial(hy.triton_w4a16_skinny_fmt_gemm, x, w_q, w_s, G, 8, w_zp)
        ref = upstream().float()
        atol = 2e-2 * ref.abs().max().item()
        base = ms(upstream)
        wb = torch.randn((N, K), dtype=torch.bfloat16, device=dev, generator=g)
        ceil = ms(lambda: torch.mm(x, wb.t()))
        del wb
        res = []
        for c in CFGS:
            run = partial(launch, x, w_q, w_s, w_zp, N, K, *c)
            try:
                out = run().float()  # also compiles, so the screen below times the kernel only
                if not (torch.isfinite(out).all() and torch.allclose(out, ref, rtol=2e-2, atol=atol)):
                    res.append((float("inf"), c, "mismatch"))
                    continue
                if (t := ms(run, iters=3, warm=1)) > 1.5 * base:
                    res.append((t, c, "screened"))
                    continue
                res.append((ms(run), c, ""))
            except Exception as e:  # noqa: BLE001  (out of resources: LDS/registers)
                res.append((float("inf"), c, type(e).__name__))
        res.sort(key=lambda r: r[0])
        print(f"== {label} N={N} K={K} M={M}: upstream {base:.2f} ms ({flops / base / 1e9:.0f} TFLOP/s), "
              f"bf16 hipBLASLt {ceil:.2f} ms ({flops / ceil / 1e9:.0f} TFLOP/s)", flush=True)
        for t, c, note in res[:6]:
            print(f"   {c}  {t:.2f} ms  {flops / t / 1e9:.0f} TFLOP/s  {base / t:.2f}x {note}", flush=True)
        hook = next((r for r in res if r[1] == (256, 128, 64, 8, 1)), None)
        if hook:
            print(f"   hook tile (256, 128, 64, 8, 1)  {hook[0]:.2f} ms  {base / hook[0]:.2f}x {hook[2]}", flush=True)


if __name__ == "__main__":
    gen = torch.Generator(device=dev).manual_seed(0)
    for m in MS:
        sweep(m, gen)
