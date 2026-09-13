#!/usr/bin/env python3
"""down_proj (N=5120, K=17408, g128, asym): cost of storing the weight as S contiguous K-chunks.

Decode M=1..5: shipped dispatch vs split-K skinny for S=2,3,4 (S=3 splits by group: 46/45/45).
Prefill M=512, 4096: shipped Triton on full K vs S Triton launches on the chunks + adds, since
chunked storage means the prefill path must also run per chunk (no duplicate weight).
Same process, interleaved; min and median of the interleaved blocks.
"""
import argparse, statistics

import torch
import vllm._custom_ops as ops
from vllm.model_executor.kernels.linear.mixed_precision import rdna_hybrid_w4a16 as hy
from vllm.utils.platform_utils import num_compute_units

N, K, G = 5120, 17408, 128
dev = torch.device("cuda")
cu = num_compute_units()
NG = K // G


def timeit(fn, iters, warm=10):
    # Replay under a CUDA graph, as prod decode does: a nice-19 process next to the serving
    # engine gets CPU-starved, and eager launch gaps punish multi-launch candidates unfairly.
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
    return min(ts), statistics.median(ts)


def chunk_bounds(S):
    # group-aligned, largest chunk first; K/S need not be a multiple of G
    per = -(-NG // S)
    b, out = 0, []
    while b < NG:
        e = min(b + per, NG); out.append((b * G, e * G)); b = e
    return out


def split_weights(w_q, w_s, w_zp, S):
    return [(w_q[:, a // 2:b // 2].contiguous(), w_s[:, a // G:b // G].contiguous(),
             w_zp[:, a // G:b // G].contiguous(), a, b) for a, b in chunk_bounds(S)]


def splitk_skinny(x, parts):
    out = None
    for wq, ws, wzp, a, b in parts:
        o = ops.wvSplitK_int4_g(wq, x[:, a:b].contiguous(), ws, cu, G, wzp, None)
        out = o if out is None else out.add_(o)
    return out


def splitk_triton(x, parts):
    out = None
    for wq, ws, wzp, a, b in parts:
        o = hy.triton_w4a16_skinny_fmt_gemm(x[:, a:b].contiguous(), wq.view(torch.int32), ws, G, zp=wzp)
        out = o if out is None else out.add_(o)
    return out


def dequant_ref(w_q, w_s, w_zp):
    p = w_q.view(torch.int32)
    shifts = torch.tensor([(j // 2) * 4 + (j % 2) * 16 for j in range(8)], device=dev)
    nib = ((p[:, :, None] >> shifts) & 0xF).reshape(N, K).float()
    return (nib - w_zp.float().repeat_interleave(G, 1)) * w_s.float().repeat_interleave(G, 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=100)
    a = ap.parse_args()
    g = torch.Generator(device=dev).manual_seed(0)
    w_q = torch.randint(-128, 127, (N, K // 2), dtype=torch.int8, device=dev, generator=g)
    w_s = (torch.rand((N, NG), device=dev, generator=g) * 0.01).to(torch.bfloat16)
    w_zp = torch.randint(0, 16, (N, NG), device=dev, generator=g).to(torch.bfloat16)
    ref_w = dequant_ref(w_q, w_s, w_zp)
    parts = {S: split_weights(w_q, w_s, w_zp, S) for S in (2, 3, 4)}
    bytes_w = w_q.numel() + w_s.numel() * 2 + w_zp.numel() * 2
    print(f"# CUs={cu} LDS_CAP={hy.LDS_CAPACITY_ELEMENTS} chunks S=3 {chunk_bounds(3)}")

    def run(M, cands, iters):
        x = torch.randn((M, K), dtype=torch.bfloat16, device=dev)
        ref = x.float() @ ref_w.t()
        res = {}
        for name, mk in cands:
            fn = mk(x)
            try:
                out = fn()
            except Exception as e:
                print(f"M={M:4d} {name:26s} REFUSED {str(e)[:70]}"); continue
            res[name] = ((out.float() - ref).abs().max().item(), [])
        for _ in range(4):  # interleave
            for name, mk in cands:
                if name in res:
                    res[name][1].append(timeit(mk(x), iters // 4))
        base = None
        for name, (err, ts) in res.items():
            mn = min(t[0] for t in ts); md = statistics.median(t[1] for t in ts)
            base = base or mn
            print(f"M={M:4d} {name:26s} {mn:8.1f} us (med {md:8.1f}) {bytes_w / mn / 1e3:6.0f} GB/s "
                  f"{base / mn:5.2f}x  maxerr {err:.3e}", flush=True)

    dec = [("shipped dispatch", lambda x: lambda: hy._rdna_hybrid_w4a16_apply_impl(x, w_q, w_s, w_zp, None, cu, G))]
    dec += [(f"splitK skinny S={S}", lambda x, S=S: lambda: splitk_skinny(x, parts[S])) for S in (2, 3, 4)]
    for M in (1, 2, 3, 4, 5):
        run(M, dec, a.iters)
    pre = [("shipped triton full K", lambda x: lambda: hy.triton_w4a16_skinny_fmt_gemm(x, w_q.view(torch.int32), w_s, G, zp=w_zp))]
    pre += [(f"triton chunked S={S}", lambda x, S=S: lambda: splitk_triton(x, parts[S])) for S in (2, 3)]
    for M in (64, 512, 4096):
        run(M, pre, max(20, a.iters // 5))


if __name__ == "__main__":
    main()
