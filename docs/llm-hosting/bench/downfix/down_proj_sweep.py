#!/usr/bin/env python3
"""down_proj (N=5120, K=17408, g128, asymmetric) at M=3..5: Triton tile sweep vs split-K skinny.

Both candidates and the shipped path run in ONE HIP process, interleaved, so they share the
gfx1201 band draw. Correctness: max abs err vs an fp32 dequant reference, next to Triton's.

Run inside the vLLM pod: python3 down_proj_sweep.py [--iters 80] [--phase sweep|final]
"""
import argparse, itertools, statistics, sys, time

import torch
import vllm._custom_ops as ops
from vllm.model_executor.kernels.linear.mixed_precision import rdna_hybrid_w4a16 as hy
from vllm.triton_utils import triton
from vllm.utils.platform_utils import num_compute_units

N, K, G = 5120, 17408, 128
dev = torch.device("cuda")
cu = num_compute_units()


def timeit(fn, iters, warm=10):
    for _ in range(warm):
        fn()
    torch.cuda.synchronize()
    ts = []
    for _ in range(iters):
        s = torch.cuda.Event(enable_timing=True); e = torch.cuda.Event(enable_timing=True)
        s.record(); fn(); e.record(); e.synchronize()
        ts.append(s.elapsed_time(e) * 1e3)
    return min(ts), statistics.median(ts)


def make_weights(seed=0):
    g = torch.Generator(device=dev).manual_seed(seed)
    w_q = torch.randint(-128, 127, (N, K // 2), dtype=torch.int8, device=dev, generator=g)
    w_s = (torch.rand((N, K // G), device=dev, generator=g) * 0.01).to(torch.bfloat16)
    w_zp = torch.randint(0, 16, (N, K // G), device=dev, generator=g).to(torch.bfloat16)
    return w_q, w_s, w_zp


def dequant_ref(w_q, w_s, w_zp):
    # ExLlama shuffle: nibble j of each int32 sits at shift (j//2)*4 + (j%2)*16
    p = w_q.view(torch.int32)  # [N, K/8]
    shifts = torch.tensor([(j // 2) * 4 + (j % 2) * 16 for j in range(8)], device=dev)
    nib = ((p[:, :, None] >> shifts) & 0xF).reshape(N, K).float()
    return (nib - w_zp.float().repeat_interleave(G, 1)) * w_s.float().repeat_interleave(G, 1)


def split_weights(w_q, w_s, w_zp, S):
    # contiguous K-chunks; K/S must stay a multiple of 8 (int32 packing) and of G
    kc = K // S
    assert kc % G == 0
    return [(w_q[:, i * kc // 2:(i + 1) * kc // 2].contiguous(),
             w_s[:, i * kc // G:(i + 1) * kc // G].contiguous(),
             w_zp[:, i * kc // G:(i + 1) * kc // G].contiguous()) for i in range(S)]


def splitk_skinny(x, parts, S):
    kc = K // S
    out = None
    for i, (wq, ws, wzp) in enumerate(parts):
        o = ops.wvSplitK_int4_g(wq, x[:, i * kc:(i + 1) * kc].contiguous(), ws, cu, G, wzp, None)
        out = o if out is None else out.add_(o)
    return out


def triton_cfg(x, w_q, w_s, w_zp, BM, BN, BK, warps, stages):
    M = x.shape[0]
    c = torch.empty((M, N), dtype=x.dtype, device=dev)
    grid = (triton.cdiv(M, BM), triton.cdiv(N, BN))
    hy._triton_w4a16_skinny_fmt_kernel[grid](
        x, w_q.view(torch.int32), w_s, w_zp, c, M, N, K, K // 8, K // G,
        group_size=G, ZP_BIAS=8, HAS_ZP=True, BLOCK_M=BM, BLOCK_N=BN, BLOCK_K=BK,
        num_warps=warps, num_stages=stages)
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=80)
    ap.add_argument("--phase", default="sweep")
    a = ap.parse_args()
    w_q, w_s, w_zp = make_weights()
    ref_w = dequant_ref(w_q, w_s, w_zp)
    parts = {S: split_weights(w_q, w_s, w_zp, S) for S in (2, 4)}
    bytes_w = w_q.numel() + w_s.numel() * 2 + w_zp.numel() * 2
    print(f"# CUs={cu} LDS_CAP={hy.LDS_CAPACITY_ELEMENTS} torch={torch.__version__} triton={triton.__version__}")

    def report(name, M, fn, ref):
        try:
            out = fn()
        except Exception as e:  # kernel refuses shape
            print(f"{name:34s} M={M} REFUSED {str(e)[:60]}"); return None
        err = (out.float() - ref).abs().max().item()
        mn, md = timeit(fn, a.iters)
        print(f"{name:34s} M={M} {mn:8.1f} us (med {md:8.1f}) {bytes_w / mn / 1e3:6.0f} GB/s  maxerr {err:.3e}", flush=True)
        return mn

    if a.phase == "sweep":
        Ms = [4]
        cfgs = [(16, BN, BK, w, st) for BN, BK, w, st in itertools.product((16, 32, 64, 128), (64, 128), (1, 2, 4, 8), (1, 2, 3))]
    else:
        Ms = [3, 4, 5]
        cfgs = [tuple(int(v) for v in c.split("x")) for c in sys.argv[sys.argv.index("--cfgs") + 1:]] if "--cfgs" in sys.argv else []
    for M in Ms:
        x = torch.randn((M, K), dtype=torch.bfloat16, device=dev)
        ref = x.float() @ ref_w.t()
        report("shipped (triton 16x16x128 w4)", M, lambda: hy._rdna_hybrid_w4a16_apply_impl(x, w_q, w_s, w_zp, None, cu, G), ref)
        for S in (2, 4):
            report(f"splitK skinny S={S}", M, lambda S=S: splitk_skinny(x, parts[S], S), ref)
        res = []
        for cfg in cfgs:
            t = report("triton %dx%dx%d w%d s%d" % cfg, M, lambda cfg=cfg: triton_cfg(x, w_q, w_s, w_zp, *cfg), ref)
            if t is not None:
                res.append((t, cfg))
        if res:
            print("# best triton at M=%d:" % M, sorted(res)[:5])


if __name__ == "__main__":
    main()
