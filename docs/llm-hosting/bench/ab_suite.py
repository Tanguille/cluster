#!/usr/bin/env python3
"""Stack A/B micro-suite (same vLLM, different ROCm/torch): bf16 GEMM TFLOP/s, HBM copy GB/s, W4A16 decode GEMMs.

usage: python3 ab_suite.py [--iters 100]      one JSON line per measurement, min over interleaved blocks
Pair it with gputelem.py (sclk, power, band) and bench_uq_prefill.py for the attention kernel.
"""
import argparse
import json
import statistics

import torch

dev = torch.device("cuda")
GROUP = 128


def time_us(fn, iters, warm=20):
    for _ in range(warm):
        fn()
    torch.cuda.synchronize()
    ts = []
    for _ in range(iters):
        s, e = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        s.record()
        fn()
        e.record()
        e.synchronize()
        ts.append(s.elapsed_time(e) * 1e3)
    return min(ts), statistics.median(ts)


def emit(**kw):
    print(json.dumps(kw), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=100)
    a = ap.parse_args()
    emit(kind="env", torch=torch.__version__, hip=torch.version.hip, dev=torch.cuda.get_device_name(0))

    for n in (4096, 8192):
        x = torch.randn(n, n, device=dev, dtype=torch.bfloat16)
        lo, med = time_us(lambda: x @ x, max(20, a.iters // 4))
        emit(kind="gemm_bf16", n=n, min_us=round(lo, 1), tflops=round(2 * n**3 / lo / 1e6, 1), med_us=round(med, 1))

    big = torch.empty(2**29, dtype=torch.uint8, device=dev)  # 512 MiB, well past the 64 MB Infinity Cache
    dst = torch.empty_like(big)
    lo, med = time_us(lambda: dst.copy_(big), a.iters)
    emit(kind="copy", mib=512, min_us=round(lo, 1), gbps=round(2 * big.numel() / lo / 1e3, 1), med_us=round(med, 1))
    del big, dst

    from vllm.model_executor.kernels.linear.mixed_precision import rdna_hybrid_w4a16 as hy
    from vllm.utils.platform_utils import num_compute_units
    cu = num_compute_units()
    g = torch.Generator(device=dev).manual_seed(1)
    for n, k, label in ((16384, 5120, "gdn in_proj"), (34816, 5120, "mlp gate_up"), (5120, 17408, "mlp down")):
        w_q = torch.randint(-128, 127, (n, k // 2), dtype=torch.int8, device=dev, generator=g)
        w_s = (torch.rand((n, k // GROUP), device=dev, generator=g) * 0.01).to(torch.bfloat16)
        w_zp = torch.randint(-(2**31), 2**31 - 1, (n // 8, k // GROUP), dtype=torch.int32, device=dev, generator=g)
        nbytes = w_q.numel() + w_s.numel() * 2 + w_zp.numel() * 4
        for m in (1, 4, 8):
            x = torch.randn(m, k, device=dev, dtype=torch.bfloat16, generator=g)
            lo, med = time_us(lambda: hy._rdna_hybrid_w4a16_apply_impl(x, w_q, w_s, w_zp, None, cu, GROUP), a.iters)
            emit(kind="w4a16", label=label, n=n, k=k, m=m, min_us=round(lo, 1), gbps=round(nbytes / lo / 1e3, 1), med_us=round(med, 1))


if __name__ == "__main__":
    main()
