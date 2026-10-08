#!/usr/bin/env python3
"""Decode GEMM bench: gfx12 port of vllm#60413 (MXFP4 W4A8) vs the PATCHED production W4A16 path.

* W4A16 side: vllm's _rdna_hybrid_w4a16_apply_impl with the lds-gate-patch hook mounted (asserted
  below: LDS_CAPACITY_ELEMENTS 39321 and the small-M Triton tile), real compressed-tensors
  asymmetric g128 layout (int8 [N,K/2], bf16 scales [N,K/128], int32 packed zero points).
* MXFP4 side: torch.ops.vllm.rdna_mxfp4_w4a8_apply (the kernel class's op), so activation int8
  quantization is inside the timed region. M > 8 times the same op's serving path (whole-weight
  dequant + one bf16 GEMM, gate_up alone materializes 356 MB of bf16 weights). A (shape, M) whose
  weights + activations + dequant temps + graph-pool outputs do not fit the remaining tensor cap is
  SKIPPED and listed, never replaced by a chunked variant: chunking changes the latency and peak memory.
* Timing: CUDA-graph replay (as production decode does), each graph cycles through c distinct
  weight copies so the weight bytes touched per replay exceed the 64 MB Infinity Cache (1.4x for gate_up,
  1.7x-2.2x for the rest, see FOOTPRINT); time per call = replay time / calls. Minimum of --iters (40) replays per pass; two
  passes with the A/B order reversed; the reported value is the minimum over both passes.
  The two kernels' weights are never resident together (budget), so they are not interleaved
  call by call; the reversed pass is what cancels slow drift of the shared GPU.
* lm_head proxy: torch.matmul on a bf16 row slice (1/16 of the 248320-row head; 1/4 does not fit
  the budget), scaled x16.
  python3 bench_vs_w4a16.py [--iters 40] [--out /work/mx/bench_result.json]
"""
import argparse
import json
import os
import statistics

os.environ["VLLM_ROCM_MXFP4_W4A8"] = "1"
import torch

import vramguard

vramguard.init()

import mxw4a8_ext  # noqa: E402

mxw4a8_ext.load_op()
import rdna_w4a8_gfx12 as kmod  # noqa: E402
from vllm.model_executor.kernels.linear.mixed_precision import rdna_hybrid_w4a16 as hy  # noqa: E402
from vllm.utils.platform_utils import num_compute_units  # noqa: E402

# Decode-step linears of Qwen3.8-27B as vLLM fuses them, with how many times each runs per step:
# 64 layers = 16 full-attention + 48 GDN. in_proj_ba (N=96) is launch-bound and left out.
SHAPES = [  # label, N, K, count per step
    ("qkv", 14336, 5120, 16),
    ("out_proj", 5120, 6144, 64),  # 16 attn o_proj + 48 GDN out_proj, same shape
    ("gdn in_proj", 16384, 5120, 48),
    ("gate_up", 34816, 5120, 64),
    ("down", 5120, 17408, 64),
]
MS = [1, 2, 3, 4, 5, 6, 8, 9, 64, 4096]  # M=6 is the first W4A16 Triton-route M (MAX_SKINNY_BATCH_SIZE=5)
LM_MS = [1, 2, 4, 8]
LM_ROWS_FULL, LM_SLICE = 248320, 16
G = 128
FOOTPRINT = 130e6  # target bytes of distinct weights per replay (~1.9x the 64 MB Infinity Cache); gate_up gets one copy (1.4x)
dev = torch.device("cuda")
cu = num_compute_units()


def read(path):
    try:
        return open(path).read().strip().replace("\n", " | ")
    except OSError:
        return "n/a"


def gpu_state():
    d = os.path.dirname(next(iter(__import__("glob").glob("/sys/class/drm/card*/device/mem_info_vram_total"))))
    return f"busy {read(d + '/gpu_busy_percent')}% sclk[{read(d + '/pp_dpm_sclk')}]"


def bytes_w4a16(N, K):
    return N * K // 2 + N * (K // G) * 2 + (N // 8) * (K // G) * 4


def bytes_mx(N, K):
    return N * K // 2 + N * (K // 32)


def mk_w4a16(N, K, seed):
    g = torch.Generator(device=dev).manual_seed(seed)
    w_q = torch.randint(-128, 127, (N, K // 2), dtype=torch.int8, device=dev, generator=g)  # any bits are a valid packing
    w_s = (torch.rand((N, K // G), device=dev, generator=g) * 0.01).to(torch.bfloat16)
    w_zp = torch.randint(-(2**31), 2**31 - 1, (N // 8, K // G), dtype=torch.int32, device=dev, generator=g)
    return w_q, w_s, w_zp


def mk_mx(N, K, seed):
    g = torch.Generator(device=dev).manual_seed(seed)
    w = torch.randint(0, 256, (N, K // 2), dtype=torch.uint8, device=dev, generator=g)
    s = torch.randint(120, 130, (N, K // 32), dtype=torch.uint8, device=dev, generator=g)
    return w, s


def mx_apply(x, w, s):
    return torch.ops.vllm.rdna_mxfp4_w4a8_apply(x, w, s, None, True)  # the serving op at every M


def fits(side, N, K, M, n_calls):
    """Estimate of the extra tensor bytes one (side, N, K, M) timing needs vs what the cap has left."""
    need = M * K * 2 + n_calls * M * N * 2  # x + the outputs each captured call keeps in the graph pool
    if side == "mx" and M > kmod.MAX_W4A8_BATCH_SIZE:
        need += 2 * N * K * 2 + M * K * 2  # bf16 dequantized weight + one temp, and the QDQ'd activation
    torch.cuda.empty_cache()  # reserved then tracks live tensors, not allocator leftovers of the previous M
    free = vramguard.tensor_cap_bytes() - torch.cuda.memory_reserved()
    return need <= free, need, free


def time_graph(calls, iters):
    """Min over `iters` replays of a graph holding all `calls`; returns us per call."""
    for c in calls * 2:
        c()
    torch.cuda.synchronize()
    graph = torch.cuda.CUDAGraph()
    with torch.cuda.graph(graph):
        for c in calls:
            c()
    torch.cuda.synchronize()
    for _ in range(5):
        graph.replay()
    ts = []
    for _ in range(iters):
        s, e = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        s.record()
        graph.replay()
        e.record()
        e.synchronize()
        ts.append(s.elapsed_time(e) * 1e3 / len(calls))
    del graph
    return min(ts), statistics.median(ts)


def calls_for(fn_i, copies, min_calls=8):  # large M is compute-bound: one replay per copy, outputs would not fit otherwise
    reps = max(1, -(-min_calls // copies))
    return [(lambda i=i: fn_i(i)) for i in range(copies)] * reps


def verify_w4a16_format():
    """Small shape: the random-bits layout is a valid asymmetric g128 weight for both paths."""
    N, K = 256, 1024
    w_q, w_s, w_zp = mk_w4a16(N, K, 7)
    p = w_q.view(torch.int32)
    shifts = torch.tensor([(j // 2) * 4 + (j % 2) * 16 for j in range(8)], device=dev)
    nib = ((p[:, :, None] >> shifts) & 0xF).reshape(N, K).float()
    zp = ((w_zp[:, None, :] >> (4 * torch.arange(8, device=dev))[None, :, None]) & 0xF).reshape(N, K // G).float()
    ref_w = (nib - zp.repeat_interleave(G, 1)) * w_s.float().repeat_interleave(G, 1)
    for M in (1, 3, 64):
        x = torch.randn((M, K), dtype=torch.bfloat16, device=dev)
        out = hy._rdna_hybrid_w4a16_apply_impl(x, w_q, w_s, w_zp, None, cu, G).float()
        ref = x.float() @ ref_w.T
        rel = ((out - ref).norm() / ref.norm()).item()
        print(f"  W4A16 layout check M={M}: rel-L2 vs dequant ref {rel:.3e}", flush=True)
        assert rel < 2e-2, "W4A16 weight format is wrong; bench would time the wrong path"


def w4a16_path(M, K):
    return "skinny" if M <= hy.MAX_SKINNY_BATCH_SIZE and K * M <= hy.LDS_CAPACITY_ELEMENTS else "triton"


def run_side(side, N, K, iters, res, pass_no, skipped):
    nb = bytes_w4a16(N, K) if side == "w4a16" else bytes_mx(N, K)
    copies = max(1, round(FOOTPRINT / nb))
    if side == "w4a16":
        ws = [mk_w4a16(N, K, 100 + i) for i in range(copies)]
    else:
        ws = [mk_mx(N, K, 100 + i) for i in range(copies)]
    for M in MS:
        n_calls = len(calls_for(lambda i: None, copies, 8 if M <= 8 else 1))
        ok, need, free = fits(side, N, K, M, n_calls)
        if not ok:
            skipped.append((side, N, K, M))
            print(f"    pass{pass_no} {side:6s} N={N:5d} K={K:5d} M={M:4d} SKIPPED for the VRAM cap: needs ~{need / 2**20:.0f} MiB, "
                  f"{max(free, 0) / 2**20:.0f} MiB left", flush=True)
            continue
        x = torch.randn((M, K), dtype=torch.bfloat16, device=dev)
        if side == "w4a16":
            def fn(i, x=x):
                return hy._rdna_hybrid_w4a16_apply_impl(x, ws[i][0], ws[i][1], ws[i][2], None, cu, G)
        else:
            def fn(i, x=x):
                return mx_apply(x, ws[i][0], ws[i][1])
        t_min, t_med = time_graph(calls_for(fn, copies, 8 if M <= 8 else 1), iters)
        key = (N, K, M, side)
        res[key] = min(res.get(key, 1e18), t_min)
        print(f"    pass{pass_no} {side:6s} N={N:5d} K={K:5d} M={M:4d} copies={copies} "
              f"min {t_min:8.1f} us med {t_med:8.1f} us", flush=True)
        vramguard.check(f"{side} N={N} K={K} M={M}")
        del x
    del ws
    torch.cuda.empty_cache()


def lm_head(iters):
    rows = LM_ROWS_FULL // LM_SLICE
    W = torch.empty((rows, 5120), dtype=torch.bfloat16, device=dev).normal_(0, 0.02)
    out = {}
    for M in LM_MS:
        x = torch.randn((M, 5120), dtype=torch.bfloat16, device=dev)
        t_min, t_med = time_graph(calls_for(lambda i, x=x: torch.matmul(x, W.T), 1), iters)
        out[M] = t_min
        print(f"    lm_head slice {rows}x5120 bf16 ({rows * 5120 * 2 / 2**20:.0f} MiB) M={M}: min {t_min:.1f} us "
              f"-> x{LM_SLICE} full head {t_min * LM_SLICE / 1e3:.2f} ms ({rows * 5120 * 2 / t_min / 1e3:.0f} GB/s)", flush=True)
    del W
    torch.cuda.empty_cache()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=40)
    ap.add_argument("--out", default="/work/mx/bench_result.json")
    ap.add_argument("--shapes", default="", help="comma list of labels to run (default all)")
    a = ap.parse_args()

    assert hy.LDS_CAPACITY_ELEMENTS == 39321, f"lds-gate-patch not active: {hy.LDS_CAPACITY_ELEMENTS}"
    assert hy.triton_w4a16_skinny_fmt_gemm.__name__ == "gemm", "small-M Triton tile patch not active"
    print(f"# patched W4A16 path active: LDS_CAPACITY_ELEMENTS={hy.LDS_CAPACITY_ELEMENTS} "
          f"MAX_SKINNY={hy.MAX_SKINNY_BATCH_SIZE} triton gemm wrapper={hy.triton_w4a16_skinny_fmt_gemm.__name__} CUs={cu}")
    print(f"# torch {torch.__version__}  iters {a.iters}  footprint/replay >= {FOOTPRINT / 1e6:.0f} MB  start: {gpu_state()}")
    verify_w4a16_format()
    shapes = [s for s in SHAPES if not a.shapes or s[0] in a.shapes.split(",")]

    res, skipped = {}, []
    for pass_no, order in enumerate((("w4a16", "mx"), ("mx", "w4a16"))):
        print(f"== pass {pass_no} order {order}  {gpu_state()}", flush=True)
        for label, N, K, _ in shapes:
            print(f"  {label} {N}x{K}", flush=True)
            for side in order:
                run_side(side, N, K, a.iters, res, pass_no, skipped)
            vramguard.check(f"{label} pass{pass_no}")
    print("== lm_head proxy", flush=True)
    lm = lm_head(a.iters)
    vramguard.check("lm_head")

    print(f"\n== RESULTS (us per call, min over both passes of min-of-{a.iters} graph replays; ratio = W4A16 / MXFP4)")
    print(f"{'shape':12s} {'N':>6s} {'K':>6s} {'M':>4s} | {'w4a16 us':>9s} {'path':>7s} {'GB/s':>5s} | {'mxfp4 us':>9s} {'GB/s':>5s} | ratio")
    table = {}
    for label, N, K, _ in shapes:
        for M in MS:
            if (N, K, M, "w4a16") not in res or (N, K, M, "mx") not in res:
                print(f"{label:12s} {N:6d} {K:6d} {M:4d} | skipped for the VRAM cap (a side did not fit)")
                continue
            ta, tb = res[(N, K, M, "w4a16")], res[(N, K, M, "mx")]
            table[(label, M)] = (ta, tb)
            print(f"{label:12s} {N:6d} {K:6d} {M:4d} | {ta:9.1f} {w4a16_path(M, K):>7s} {bytes_w4a16(N, K) / ta / 1e3:5.0f} | "
                  f"{tb:9.1f} {bytes_mx(N, K) / tb / 1e3:5.0f} | {ta / tb:5.2f}x")

    if skipped:
        print(f"\n== SKIPPED for the VRAM cap ({len(skipped)} timings; not substituted by a chunked variant, so M=4096 and "
              f"large-M serving latency are NOT measured where listed above)")
    if len(shapes) == len(SHAPES):
        counts = {s[0]: s[3] for s in SHAPES}
        # M buckets of the 30d concurrency mix; a bucket uses the measured Ms listed
        buckets = [("M=1", 0.468, [1], 1), ("M=2", 0.244, [2], 2), ("M=3-4", 0.209, [3, 4], 4), ("M=5-8", 0.080, [5, 6, 8], 8)]
        print("\n== workload-weighted decode-step GEMM estimate (ms per step; counts: "
              + ", ".join(f"{k}x{v}" for k, v in counts.items()) + " + lm_head once)")
        print("   bucket mix uses the mean of the measured Ms in the bucket (M=7 not measured); lm_head is the bf16 row slice x16 "
              "from M=1,2,4,8, a linear extrapolation, not a full-head measurement")
        print(f"   CAVEAT M=5 vs M=6: W4A16 takes the skinny kernel up to M={hy.MAX_SKINNY_BATCH_SIZE} (when K*M fits LDS) and the Triton "
              "tile from M=6; MXFP4 stays on its GEMV to M=8. The M=5-8 bucket averages both W4A16 routes; read the M=5 and M=6 rows "
              "of the table separately, the bucket mean hides the step. W4A16 M=5 -> M=6 (us):")
        for l in counts:
            if (l, 5) in table and (l, 6) in table:
                print(f"     {l:12s} {table[(l, 5)][0]:8.1f} -> {table[(l, 6)][0]:8.1f}  (MXFP4 {table[(l, 5)][1]:8.1f} -> {table[(l, 6)][1]:8.1f})")
        tot_a = tot_b = 0.0
        rows = []
        for name, wgt, ms, lm_m in buckets:
            sa = sum(counts[l] * statistics.mean(table[(l, m)][0] for m in ms) for l in counts) / 1e3
            sb = sum(counts[l] * statistics.mean(table[(l, m)][1] for m in ms) for l in counts) / 1e3
            lmt = lm[lm_m] * LM_SLICE / 1e3
            rows.append((name, wgt, sa, sb, lmt))
            tot_a += wgt * (sa + lmt)
            tot_b += wgt * (sb + lmt)
            print(f"   {name:6s} weight {wgt:5.3f}: layers W4A16 {sa:6.2f} ms  MXFP4 {sb:6.2f} ms  (+ lm_head {lmt:5.2f} ms both)  "
                  f"GEMM ratio incl. lm_head {(sa + lmt) / (sb + lmt):.3f}x")
        r = tot_a / tot_b
        print(f"   weighted step GEMM time: W4A16 {tot_a:.2f} ms  MXFP4 {tot_b:.2f} ms  -> r = {r:.3f}x")
        print("   SCENARIOS, not evidence: the non-GEMM shares below are assumed, not profiled; a measured share (decode-step "
              "profile) is needed before comparing against any >=1.15x acceptance gate")
        for s_nongemm in (0.10, 0.20, 0.30):
            e2e = 1 / (s_nongemm + (1 - s_nongemm) / r)
            print(f"   scenario non-GEMM share s={s_nongemm:.2f} (assumed): e2e {e2e:.3f}x; discounted 12% of the gain: {1 + 0.88 * (e2e - 1):.3f}x")
        json.dump({"table": {f"{k[0]}|{k[1]}": v for k, v in table.items()}, "lm_head_us_slice": lm, "r_weighted": r,
                   "rows": rows}, open(a.out, "w"), indent=1)
    vramguard.check("end")


if __name__ == "__main__":
    main()
