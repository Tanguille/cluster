#!/usr/bin/env python3
"""W4A16 Triton tile sweep at prefill size on the five Qwen3.8 shapes, graph replay. usage: triton_prefill_sweep.py [M=2048]

Baseline = upstream's gfx12x pick for M > 512 (tuned on Llama-3.1-8B shapes). Ceiling = bf16 torch.mm (hipBLASLt)
of the same shape. Candidates must match the baseline output to 2e-2 relative (reduction order differs with BLOCK_K).
"""
import itertools
import sys

import torch
from vllm.model_executor.kernels.linear.mixed_precision import rdna_hybrid_w4a16 as hy
from vllm.triton_utils import triton

G = 128
dev = torch.device("cuda")
SHAPES = [(5120, 17408, "down"), (34816, 5120, "gate_up"), (16384, 5120, "in_proj"),
          (14336, 5120, "qkv"), (5120, 6144, "out_proj")]
M = int(sys.argv[1]) if len(sys.argv) > 1 else 2048


def upstream_cfg(N, K):
    if K >= 2 * N:
        return (128, 64, 64, 8, None)
    if N >= 4 * K:
        return (256, 64, 64, 8, None)
    return (128, 128, 32, 8, None)


CFGS = [c for c in itertools.product((64, 128, 256), (64, 128, 256), (64, 128), (4, 8), (None, 1))
        if not (c[0] == 256 and c[1] == 256)]


def timeit(fn, iters=15, warm=3):
    for _ in range(warm):
        fn()
    torch.cuda.synchronize()
    g = torch.cuda.CUDAGraph()
    with torch.cuda.graph(g):
        fn()
    torch.cuda.synchronize()
    ts = []
    for _ in range(iters):
        s, e = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        s.record(); g.replay(); e.record(); e.synchronize()
        ts.append(s.elapsed_time(e))
    return min(ts)  # ms


def launch(x, w_q, w_s, w_zp, N, K, BM, BN, BK, warps, stages):
    c = torch.empty((M, N), dtype=x.dtype, device=dev)
    grid = (triton.cdiv(M, BM), triton.cdiv(N, BN))
    kw = {} if stages is None else {"num_stages": stages}
    strides = (w_q.stride(0), x.stride(0)) if "stride_bn" in hy._triton_w4a16_skinny_fmt_kernel.arg_names else ()
    hy._triton_w4a16_skinny_fmt_kernel[grid](
        x, w_q, w_s, w_zp, c, M, N, K, K // 8, K // G, *strides,
        group_size=G, ZP_BIAS=8, HAS_ZP=True, BLOCK_M=BM, BLOCK_N=BN, BLOCK_K=min(BK, G),
        num_warps=warps, **kw)
    return c


def main():
    g = torch.Generator(device=dev).manual_seed(0)
    best_by_shape = {}
    for N, K, label in SHAPES:
        w_q = torch.randint(-2**31, 2**31 - 1, (N, K // 8), dtype=torch.int32, device=dev, generator=g)
        w_s = (torch.rand((N, K // G), device=dev, generator=g) * 0.01).to(torch.bfloat16)
        # AWQ is asymmetric: packed int32 zero-points [N//8, K//G], row n at bits 4*(n%8) of word n//8
        w_zp = torch.randint(-2**31, 2**31 - 1, (N // 8, K // G), dtype=torch.int32, device=dev, generator=g)
        x = torch.randn((M, K), dtype=torch.bfloat16, device=dev, generator=g)
        flops = 2 * M * N * K
        base_cfg = upstream_cfg(N, K)
        ref = launch(x, w_q, w_s, w_zp, N, K, *base_cfg)
        base = timeit(lambda: launch(x, w_q, w_s, w_zp, N, K, *base_cfg))
        wb = torch.randn((N, K), dtype=torch.bfloat16, device=dev, generator=g)
        ceil = timeit(lambda: torch.mm(x, wb.t()))
        del wb
        res = []
        for c in CFGS:
            try:
                out = launch(x, w_q, w_s, w_zp, N, K, *c)
                err = ((out.float() - ref.float()).abs().max() / ref.float().abs().max()).item()
                if err > 2e-2:
                    res.append((float("inf"), c, f"err {err:.3g}"))
                    continue
                res.append((timeit(lambda: launch(x, w_q, w_s, w_zp, N, K, *c)), c, ""))
            except Exception as e:  # noqa: BLE001  (out of resources: LDS/registers)
                res.append((float("inf"), c, type(e).__name__))
        res.sort(key=lambda r: r[0])
        best_t, best_c, _ = res[0]
        best_by_shape[label] = (best_c, base / best_t)
        print(f"== {label} N={N} K={K} M={M}: upstream {base_cfg} {base:.2f} ms ({flops / base / 1e9:.0f} TFLOP/s), "
              f"bf16 hipBLASLt {ceil:.2f} ms ({flops / ceil / 1e9:.0f} TFLOP/s)", flush=True)
        for t, c, note in res[:6]:
            print(f"   {c}  {t:.2f} ms  {flops / t / 1e9:.0f} TFLOP/s  {base / t:.2f}x {note}", flush=True)
        # the same config across shapes matters for a simple hook: report the upstream rank too
    print("BEST", best_by_shape, flush=True)


if __name__ == "__main__":
    main()
