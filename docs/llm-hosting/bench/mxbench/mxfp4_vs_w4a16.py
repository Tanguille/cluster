#!/usr/bin/env python3
"""Same-process decode GEMM microbench: radiance MXFP4 W4A8 vs upstream W4A16 hybrid.

Both kernels run in ONE HIP process, interleaved, so they share the gfx1201 band draw and
whatever else the GPU is doing. Reports effective weight GB/s per (N, K, M): bytes the kernel
must read for the weight (packed 4-bit + scales [+ zero points]) over the median kernel time.
The MXFP4 side includes the per-token fp8 activation quant, because production pays it too.

Run inside the vLLM pod:  python3 mxfp4_vs_w4a16.py [--iters 200]
Needs radiance_mxfp4_fp8.so (built from radiance_mxfp4_fp8.hip) on sys.path.
"""
import argparse, os, statistics, sys, time

os.environ.setdefault("RADIANCE_MXFP4_DECODE_MAX_M", "64")
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import radiance_mxfp4_fp8 as rad  # noqa: E402
from vllm import _custom_ops as ops  # noqa: E402
from vllm.model_executor.kernels.linear.mixed_precision import rdna_hybrid_w4a16 as hy  # noqa: E402
from vllm.utils.platform_utils import num_compute_units  # noqa: E402

# Decode-step linears of Qwen3.8-27B as vLLM fuses them (N, K, label).
SHAPES = [
    (16384, 5120, "gdn in_proj_qkvz"),
    (5120, 6144, "gdn/attn out_proj"),
    (34816, 5120, "mlp gate_up"),
    # gate_up split in two: radiance's decode kernel caps N at DEC_MAX_N=32768, so the fused
    # 34816 falls to its prefill-tiled kernel; this is what two launches would cost
    (17408, 5120, "mlp gate (half)"),
    (5120, 17408, "mlp down"),
    (14336, 5120, "attn qkv"),
]
MS = [1, 2, 3, 4, 5]
GROUP = 128
dev = torch.device("cuda")


def permute_w(packed, N, K):
    # radiance_mxfp4.permute_w verbatim: fragment order for RADIANCE_MXFP4_WPERM=1
    nt, ks = N // 16, K // 16
    return packed.view(nt, 16, ks, 2, 4).permute(0, 2, 3, 1, 4).contiguous().view(N, K // 2)


def timeit(fn, iters, warm=20):
    for _ in range(warm):
        fn()
    torch.cuda.synchronize()
    ts = []
    for _ in range(iters):
        s = torch.cuda.Event(enable_timing=True); e = torch.cuda.Event(enable_timing=True)
        s.record(); fn(); e.record(); e.synchronize()
        ts.append(s.elapsed_time(e) * 1e3)  # us
    return statistics.median(ts), min(ts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=200)
    ap.add_argument("--wperm", type=int, default=int(os.environ.get("RADIANCE_MXFP4_WPERM", "0")))
    a = ap.parse_args()
    assert a.iters >= 4, "--iters is split over 4 interleaved blocks"
    os.environ["RADIANCE_MXFP4_WPERM"] = str(a.wperm)  # read once by the .so at first launch

    cu = num_compute_units()
    # radiance decode split-K scratch, sized as radiance_mxfp4.py sizes it
    scratch = torch.empty(4 * 64 * 32768, dtype=torch.float32, device=dev)
    cnt = torch.zeros(32768 // 128 + 8, dtype=torch.int32, device=dev)
    rad.set_decode_scratch(scratch.data_ptr(), scratch.numel() * 4, cnt.data_ptr())
    stream = torch.cuda.current_stream().cuda_stream

    print(f"# gfx CUs={cu} torch={torch.__version__} wperm={a.wperm} iters={a.iters} "
          f"LDS_CAP={hy.LDS_CAPACITY_ELEMENTS} MAX_SKINNY={hy.MAX_SKINNY_BATCH_SIZE}")
    print(f"{'shape':22s} {'N':>6s} {'K':>6s} {'M':>2s} | {'w4a16 us':>9s} {'GB/s':>6s} path     | "
          f"{'mxfp4 us':>9s} {'GB/s':>6s} | ratio")
    for N, K, label in SHAPES:
        g = torch.Generator(device=dev).manual_seed(0)
        # W4A16 (compressed-tensors, group 128, asymmetric): packed int4 [N,K/2] as int8,
        # scales and zero points [N, K/128] bf16. Random bits are fine: bandwidth, not values.
        w_q = torch.randint(-128, 127, (N, K // 2), dtype=torch.int8, device=dev, generator=g)
        w_s = (torch.rand((N, K // GROUP), device=dev, generator=g) * 0.01).to(torch.bfloat16)
        w_zp = torch.randint(0, 16, (N, K // GROUP), device=dev, generator=g).to(torch.bfloat16)
        bytes_w4a16 = w_q.numel() + w_s.numel() * 2 + w_zp.numel() * 2
        # MXFP4: packed e2m1 [N,K/2] uint8, e8m0 scales [K/32, N] uint8 (transposed layout),
        # per-row reference exponent for the folded path.
        w_mx = torch.randint(0, 256, (N, K // 2), dtype=torch.uint8, device=dev, generator=g)
        if a.wperm:
            w_mx = permute_w(w_mx, N, K)
        ws = torch.randint(120, 130, (K // 32, N), dtype=torch.uint8, device=dev, generator=g)
        wref = ws.max(dim=0).values.contiguous()
        bytes_mx = w_mx.numel() + ws.numel() + wref.numel()
        for M in MS:
            x = torch.randn((M, K), dtype=torch.bfloat16, device=dev, generator=g)
            out_mx = torch.empty((M, N), dtype=torch.bfloat16, device=dev)
            skinny = M <= hy.MAX_SKINNY_BATCH_SIZE and K * M <= hy.LDS_CAPACITY_ELEMENTS

            def f_w4a16():
                return hy._rdna_hybrid_w4a16_apply_impl(x, w_q, w_s, w_zp, None, cu, GROUP)

            def f_mx():
                xq, xs = ops.scaled_fp8_quant(x, scale=None, use_per_token_if_dynamic=True)
                xs = xs.view(-1).float().contiguous()
                rad.launch(xq.data_ptr(), w_mx.data_ptr(), ws.data_ptr(), wref.data_ptr(),
                           xs.data_ptr(), out_mx.data_ptr(), M, N, K, stream)
                return out_mx

            # interleave A/B so a mid-run band flip or a prod burst hits both alike
            ta, tb = [], []
            for _ in range(4):
                ta.append(timeit(f_w4a16, a.iters // 4))
                tb.append(timeit(f_mx, a.iters // 4))
            # min over the interleaved blocks: the least-disturbed sample of each side, so prod
            # noise mostly cancels; the median is kept for reference
            ua = min(t[1] for t in ta); ub = min(t[1] for t in tb)
            ma = statistics.median(t[0] for t in ta); mb = statistics.median(t[0] for t in tb)
            ga = bytes_w4a16 / ua / 1e3; gb = bytes_mx / ub / 1e3
            print(f"{label:22s} {N:6d} {K:6d} {M:2d} | {ua:9.1f} {ga:6.0f} {'skinny' if skinny else 'triton':8s} | "
                  f"{ub:9.1f} {gb:6.0f} | {ua / ub:5.2f}x  (median {ma:7.1f} {mb:7.1f} {ma / mb:4.2f}x)", flush=True)


if __name__ == "__main__":
    main()
