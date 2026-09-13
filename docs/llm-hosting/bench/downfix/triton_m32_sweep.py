#!/usr/bin/env python3
"""Triton W4A16 tile config for the gfx12x M<=32 branch, all Qwen3.8 decode shapes, graph replay.

The shipped config is (BLOCK_M 16, BLOCK_N 16, BLOCK_K 128, 4 warps, default stages). Any
replacement must not regress the other shapes that hit the same branch at M=6..32.
"""
import statistics, sys

import torch
from vllm.model_executor.kernels.linear.mixed_precision import rdna_hybrid_w4a16 as hy
from vllm.triton_utils import triton

G = 128
dev = torch.device("cuda")
SHAPES = [(5120, 17408, "down"), (34816, 5120, "gate_up"), (16384, 5120, "in_proj"),
          (14336, 5120, "qkv"), (5120, 6144, "out_proj")]
MS = [3, 4, 5, 8, 16, 32]
CFGS = [(16, 128, 4, None), (16, 128, 2, 2), (16, 64, 2, 2), (16, 128, 2, 1), (16, 128, 1, 3),
        (16, 64, 2, 3), (16, 128, 1, 2), (32, 128, 2, 2), (32, 64, 4, 2)]  # (BN, BK, warps, stages)


def timeit(fn, iters=40, warm=5):
    for _ in range(warm):
        fn()
    torch.cuda.synchronize()
    g = torch.cuda.CUDAGraph()
    with torch.cuda.graph(g):
        fn()
    torch.cuda.synchronize()
    ts = []
    for _ in range(iters):
        s = torch.cuda.Event(enable_timing=True); e = torch.cuda.Event(enable_timing=True)
        s.record(); g.replay(); e.record(); e.synchronize()
        ts.append(s.elapsed_time(e) * 1e3)
    return min(ts)


def launch(x, w_q, w_s, w_zp, N, K, BN, BK, warps, stages):
    M = x.shape[0]
    c = torch.empty((M, N), dtype=x.dtype, device=dev)
    grid = (triton.cdiv(M, 16), triton.cdiv(N, BN))
    kw = {} if stages is None else {"num_stages": stages}
    hy._triton_w4a16_skinny_fmt_kernel[grid](
        x, w_q.view(torch.int32), w_s, w_zp, c, M, N, K, K // 8, K // G,
        group_size=G, ZP_BIAS=8, HAS_ZP=True, BLOCK_M=16, BLOCK_N=BN, BLOCK_K=BK,
        num_warps=warps, **kw)
    return c


def main():
    g = torch.Generator(device=dev).manual_seed(0)
    print("shape     M | " + " | ".join("%dx%dw%ds%s" % c for c in CFGS))
    wins = {c: 0 for c in CFGS}
    for N, K, label in SHAPES:
        w_q = torch.randint(-128, 127, (N, K // 2), dtype=torch.int8, device=dev, generator=g)
        w_s = (torch.rand((N, K // G), device=dev, generator=g) * 0.01).to(torch.bfloat16)
        w_zp = torch.randint(0, 16, (N, K // G), device=dev, generator=g).to(torch.bfloat16)
        bytes_w = w_q.numel() + w_s.numel() * 2 + w_zp.numel() * 2
        for M in MS:
            x = torch.randn((M, K), dtype=torch.bfloat16, device=dev, generator=g)
            # same tile shape and reduction order for every candidate, so outputs must be bit-identical
            ref = launch(x, w_q, w_s, w_zp, N, K, *CFGS[0])
            for c in CFGS[1:]:
                assert torch.equal(launch(x, w_q, w_s, w_zp, N, K, *c), ref), (label, M, c)
            t = {}
            for _ in range(3):  # interleave
                for c in CFGS:
                    v = timeit(lambda: launch(x, w_q, w_s, w_zp, N, K, *c))
                    t[c] = min(t.get(c, 1e9), v)
            base = t[CFGS[0]]
            best = min(t, key=t.get); wins[best] += 1
            print(f"{label:9s} {M:2d} | " + " | ".join(f"{t[c]:7.1f} {base / t[c]:4.2f}x" for c in CFGS)
                  + f"  [{bytes_w / base / 1e3:.0f} GB/s shipped]", flush=True)
    print("# wins:", {("%dx%dw%ds%s" % c): n for c, n in wins.items() if n})


if __name__ == "__main__":
    main()
