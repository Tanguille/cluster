#!/usr/bin/env python3
"""Correctness of the gfx12 port of vllm#60413 (MXFP4 W4A8 GEMV + dequant fallback).

Two separate references, as the plan asks:
  * M <= 8 (int8 per-32 activation path): float64 matmul of the int8-quantized activations (same
    fp32 quantization arithmetic as the kernel) against the weight dequantized by vLLM's own
    dequant_mxfp4 (quark), plus the unquantized-activation product for the int8 rounding cost.
  * M > 8 (dequant + high-precision GEMM fallback): float64 matmul of quant_dequant_mxfp4(x)
    (the checkpoint's MXFP4 activation QDQ) against the same dequantized weight, and bit-equality
    with vLLM's EmulationMxfp4LinearKernel, which is the math this branch must reproduce.
Gates: rel-L2 < 1e-2 and cosine > 0.99995. Negatives (nibble swap, untransposed scale, exponent
+1) must FAIL those gates. The reference runs on CPU so the GPU budget (0.45 GiB) is not spent on
float64 copies.

  python3 test_correctness.py [--real-dir /work/mx]
"""
import argparse
import os
import sys

os.environ["VLLM_ROCM_MXFP4_W4A8"] = "1"
import torch

import vramguard

vramguard.init()

import mxw4a8_ext  # noqa: E402

mxw4a8_ext.load_op()
import rdna_w4a8_gfx12 as kmod  # noqa: E402
from vllm.model_executor.kernels.linear.mxfp4.base import MxFp4LinearLayerConfig  # noqa: E402
from vllm.model_executor.kernels.linear.mxfp4.emulation import EmulationMxfp4LinearKernel  # noqa: E402
from vllm.model_executor.layers.quantization.utils.mxfp4_utils import (  # noqa: E402
    dequant_mxfp4,
    quant_dequant_mxfp4,
)
from vllm.model_executor.layers.quantization.utils.quant_utils import kMxfp4Dynamic  # noqa: E402

REL_GATE = 1e-2
COS_GATE = 0.99995
dev = torch.device("cuda")
fails: list[str] = []
n_checks = 0
skipped_real = ""


def gate(tag: str, out: torch.Tensor, ref: torch.Tensor, expect_fail: bool = False,
         rel_gate: float = REL_GATE, cos_gate: float = COS_GATE) -> tuple[float, float]:
    """rel-L2 and cosine of out vs ref (both moved to CPU float64); record pass/fail."""
    global n_checks
    n_checks += 1
    o, r = out.detach().cpu().double().flatten(), ref.detach().cpu().double().flatten()
    rel = ((o - r).norm() / r.norm()).item()
    cos = (o @ r / (o.norm() * r.norm())).item()
    ok = bool(torch.isfinite(o).all()) and rel < rel_gate and cos > cos_gate
    good = (not ok) if expect_fail else ok
    print(f"  {'PASS' if good else 'FAIL'} {tag:58s} rel-L2 {rel:9.3e} cos {cos:.7f}"
          f"{'  (negative: gate must trip)' if expect_fail else ''}", flush=True)
    if not good:
        fails.append(tag)
    return rel, cos


def check(tag: str, cond: bool, detail: str = "") -> None:
    global n_checks
    n_checks += 1
    print(f"  {'PASS' if cond else 'FAIL'} {tag} {detail}", flush=True)
    if not cond:
        fails.append(tag)


E2M1 = torch.tensor([0, .5, 1, 1.5, 2, 3, 4, 6, -0., -.5, -1, -1.5, -2, -3, -4, -6], dtype=torch.float64)


def lut_dequant(w: torch.Tensor, s: torch.Tensor) -> torch.Tensor:
    """Independent CPU float64 dequant: low nibble = even k, E8M0 scale per 32."""
    w, s = w.cpu(), s.cpu()
    N, kh = w.shape
    codes = torch.stack([w & 0xF, w >> 4], -1).reshape(N, 2 * kh).long()
    return E2M1[codes] * torch.pow(2.0, s.double() - 127.0).repeat_interleave(32, 1)


def vllm_dequant(w: torch.Tensor, s: torch.Tensor, chunk: int = 1024) -> torch.Tensor:
    """vLLM's dequant_mxfp4 (quark), row-chunked so the bf16 copy stays small; CPU float64 result."""
    parts = []
    for n0 in range(0, w.shape[0], chunk):
        parts.append(dequant_mxfp4(w[n0:n0 + chunk], s[n0:n0 + chunk], torch.bfloat16).cpu().double())
    return torch.cat(parts)


def act_int8_qdq(x: torch.Tensor) -> torch.Tensor:
    """The kernel's activation quant: per-32 symmetric int8, fp32 arithmetic, rint, clamp +-127."""
    M, K = x.shape
    xg = x.cpu().float().view(M, K // 32, 32)
    sc = (xg.abs().amax(-1) / 127.0).clamp_min(1e-12)
    q = torch.round(xg / sc[..., None]).clamp(-127, 127)
    return (q * sc[..., None]).view(M, K).double()


def ref_matmul(xq: torch.Tensor, w: torch.Tensor, s: torch.Tensor, chunk: int = 1024) -> torch.Tensor:
    return torch.cat([xq @ vllm_dequant(w[n0:n0 + chunk], s[n0:n0 + chunk]).T for n0 in range(0, w.shape[0], chunk)], 1)


def rand_mxfp4(N, K, seed=0):
    g = torch.Generator(device=dev).manual_seed(seed)
    w = torch.randint(0, 256, (N, K // 2), dtype=torch.uint8, device=dev, generator=g)
    s = torch.randint(122, 131, (N, K // 32), dtype=torch.uint8, device=dev, generator=g)
    return w, s


def rand_x(M, K, dtype, seed=1, scale=0.1, outliers=False):
    g = torch.Generator(device=dev).manual_seed(seed)
    x = torch.randn((M, K), device=dev, generator=g) * scale
    if outliers:  # LLM-like activation: a few channels 30x larger
        x[:, torch.randperm(K, generator=g, device=dev)[: max(K // 128, 1)]] *= 30.0
    return x.to(dtype)


def make_layer(w, s):
    layer = torch.nn.Module()
    # shares storage with w/s (the 0.45 GiB budget has no room for copies of the big shapes)
    layer.weight = torch.nn.Parameter(w, requires_grad=False)
    layer.weight_scale = torch.nn.Parameter(s, requires_grad=False)
    return layer


def run_case(label, w, s, Ms, dtype=torch.bfloat16, outliers=False):
    """Kernel class vs both references for each M; also the raw op at M <= 8."""
    N, K = w.shape[0], 2 * w.shape[1]
    print(f"[{label}] N={N} K={K} {str(dtype).split('.')[-1]} outliers={outliers}", flush=True)
    kern = kmod.RdnaW4A8MxFp4LinearKernel(MxFp4LinearLayerConfig(activation_quant_key=kMxfp4Dynamic))
    layer = make_layer(w, s)
    kern.process_weights_after_loading(layer)
    emu_layer = make_layer(w, s)
    emu = EmulationMxfp4LinearKernel(MxFp4LinearLayerConfig(activation_quant_key=kMxfp4Dynamic))
    emu.process_weights_after_loading(emu_layer)
    for M in Ms:
        x = rand_x(M, K, dtype, outliers=outliers)
        out = kern.apply_weights(layer, x)
        torch.cuda.synchronize()
        assert out.shape == (M, N) and out.dtype == dtype
        if M <= kmod.MAX_W4A8_BATCH_SIZE:
            xq = act_int8_qdq(x)
            gate(f"M={M} W4A8 vs int8-act ref", out, ref_matmul(xq, w, s))
            # The cost of quantizing activations to int8 per 32. It is a property of the scheme, not
            # a kernel error (the gate above uses the matching quantization), and it grows with
            # outlier channels: the first run measured 5-7e-3 on gaussian x and 1.1-1.2e-2 with
            # 30x outlier channels, so outlier cases get a looser bound that still catches blowups.
            lo = (3e-2, 0.9995) if outliers else (REL_GATE, COS_GATE)
            gate(f"M={M} W4A8 vs unquantized-act ref (int8 rounding cost{', outliers' if outliers else ''})",
                 out, ref_matmul(x.cpu().double(), w, s), rel_gate=lo[0], cos_gate=lo[1])
            raw = mxw4a8_ext.gemv(x, w, s)
            check(f"M={M} raw op == class output", torch.equal(raw, out))
            # info only: the checkpoint semantic (MXFP4 act QDQ) is a coarser activation quant
            ex = emu.apply_weights(emu_layer, x)
            o, r = out.double().cpu().flatten(), ex.double().cpu().flatten()
            print(f"  info M={M} W4A8 vs emulation(MXFP4 act QDQ): rel-L2 {((o - r).norm() / r.norm()).item():.3e}", flush=True)
        else:
            xq = quant_dequant_mxfp4(x).cpu().double()
            gate(f"M={M} fallback vs MXFP4-act-QDQ ref", out, ref_matmul(xq, w, s))
            ex = emu.apply_weights(emu_layer, x)
            check(f"M={M} fallback == EmulationMxfp4LinearKernel (bit-exact)", torch.equal(out, ex))
            try:
                mxw4a8_ext.gemv(x, w, s)
                check(f"M={M} raw op rejects M>8", False)
            except RuntimeError as e:
                check(f"M={M} raw op rejects M>8", "1 <= M <= 8" in str(e))
    vramguard.check(label)
    del layer, emu_layer
    torch.cuda.empty_cache()


def main():
    global skipped_real
    ap = argparse.ArgumentParser()
    ap.add_argument("--real-dir", default="/work/mx")
    a = ap.parse_args()

    print("== platform / registration")
    check("is_supported with env on", kmod.RdnaW4A8MxFp4LinearKernel.is_supported()[0],
          str(kmod.RdnaW4A8MxFp4LinearKernel.is_supported()))
    os.environ["VLLM_ROCM_MXFP4_W4A8"] = "0"
    check("is_supported off without env", not kmod.RdnaW4A8MxFp4LinearKernel.is_supported()[0])
    os.environ["VLLM_ROCM_MXFP4_W4A8"] = "1"
    check("can_implement(kMxfp4Dynamic)", kmod.RdnaW4A8MxFp4LinearKernel.can_implement(MxFp4LinearLayerConfig(kMxfp4Dynamic))[0])
    check("torch.ops.vllm.rdna_mxfp4_w4a8_apply registered", hasattr(torch.ops.vllm, "rdna_mxfp4_w4a8_apply"))

    print("== dequant layout sanity: independent LUT dequant == vLLM dequant_mxfp4")
    w, s = rand_mxfp4(256, 512)
    check("LUT dequant == vllm dequant_mxfp4", torch.equal(lut_dequant(w, s), vllm_dequant(w, s)))

    print("== op schema / fake impl (opcheck)")
    x = rand_x(4, 512, torch.bfloat16)
    torch.library.opcheck(torch.ops.mxw4a8.gemv, (x, w, s))
    check("opcheck(mxw4a8.gemv)", True)

    print("== random MXFP4, QuarkOCP_MX layout (weight uint8 [N,K/2], scale uint8 e8m0 [N,K/32])")
    for N, K, tag in [(64, 256, "small"), (256, 512, "small2"), (128, 4096, "128x4096"), (40, 5120, "N=40"),
                      (37, 512, "N=37 (not a multiple of 8)"), (96, 5120, "in_proj_ba N=96")]:
        w, s = rand_mxfp4(N, K)
        run_case(tag, w, s, [1, 4, 8, 9])
    w, s = rand_mxfp4(5120, 6144)
    run_case("o_proj-size 5120x6144 + outliers", w, s, [1, 4, 8, 9], outliers=True)
    w, s = rand_mxfp4(5120, 17408)
    # no M=9 here: the fallback's bf16 dequant temp is 178 MB, over the budget (M=9 is covered at K=6144)
    run_case("down-size 5120x17408", w, s, [1, 8])
    w, s = rand_mxfp4(256, 512)
    run_case("fp16", w, s, [1, 4, 8, 9], dtype=torch.float16)

    print("== mixed batch: rows with magnitudes 1e-3 .. 1e3 in one M=7 call, plus a mixed-dtype-range M=8")
    w, s = rand_mxfp4(512, 4096)
    K = 4096
    g = torch.Generator(device=dev).manual_seed(5)
    mags = torch.tensor([1e-3, 1e-2, 1e-1, 1.0, 10.0, 100.0, 1e3], device=dev)
    xm = (torch.randn((7, K), device=dev, generator=g) * mags[:, None]).to(torch.bfloat16)
    om = mxw4a8_ext.gemv(xm, w, s)
    refm = ref_matmul(act_int8_qdq(xm), w, s)
    for i in range(7):
        gate(f"mixed batch row {i} (|x|~{mags[i].item():g})", om[i], refm[i])
    xm8 = rand_x(8, K, torch.bfloat16, outliers=True)
    xm8[3] = 0  # an all-zero row exercises the 1e-12 scale floor
    om8 = mxw4a8_ext.gemv(xm8, w, s)
    gate("mixed batch M=8 with an all-zero row", om8, ref_matmul(act_int8_qdq(xm8), w, s))
    check("all-zero row gives exact zeros", bool((om8[3] == 0).all()))

    print("== negatives (the gates must trip): N=128 K=4096 so that K/32 == N for the scale transpose")
    w, s = rand_mxfp4(128, 4096, seed=3)
    x = rand_x(4, 4096, torch.bfloat16, seed=4)
    ref = ref_matmul(act_int8_qdq(x), w, s)
    gate("positive control (unmodified inputs)", mxw4a8_ext.gemv(x, w, s), ref)
    w_swap = ((w & 0x0F) << 4) | (w >> 4)
    gate("negative: nibble swap", mxw4a8_ext.gemv(x, w_swap, s), ref, expect_fail=True)
    gate("negative: untransposed scale ([K/32,N] as [N,K/32])", mxw4a8_ext.gemv(x, w, s.t().contiguous()), ref, expect_fail=True)
    gate("negative: scale exponent +1", mxw4a8_ext.gemv(x, w, s + 1), ref, expect_fail=True)

    print("== real checkpoint layers (Swift-1.5-Qwen3.8-27b-Quark-RTN-MXFP4@459bc7fe8340, Range-fetched)")
    try:
        from safetensors.torch import load_file
        o = load_file(os.path.join(a.real_dir, "real_o_proj.safetensors"))
        pa = load_file(os.path.join(a.real_dir, "real_in_proj_a.safetensors"))
        pb = load_file(os.path.join(a.real_dir, "real_in_proj_b.safetensors"))
    except FileNotFoundError as e:  # a corrupt or unreadable layer file is a failure, only an absent one skips
        skipped_real = str(e)
        print(f"  SKIP real layers: {e}")
    else:
        ew = o["weight_scale"].to(torch.int32)
        print(f"  o_proj: weight {tuple(o['weight'].shape)} scale {tuple(o['weight_scale'].shape)} "
              f"e8m0 range {ew.min().item()}..{ew.max().item()}")
        run_case("real o_proj (layer 3 self_attn, N=5120 K=6144)", o["weight"].to(dev), o["weight_scale"].to(dev),
                 [1, 4, 8, 9], outliers=True)
        wba = torch.cat([pa["weight"], pb["weight"]]).to(dev)
        sba = torch.cat([pa["weight_scale"], pb["weight_scale"]]).to(dev)
        run_case("real in_proj_ba (layer 0 linear_attn a|b, N=96 K=5120)", wba, sba, [1, 4, 8, 9], outliers=True)

    vramguard.check("end")
    print(f"\nSUMMARY: {n_checks} checks, {len(fails)} failed"
          + (f", real-layer coverage SKIPPED ({skipped_real})" if skipped_real else ", real layers covered"))
    if fails:
        print("FAILED:", *fails, sep="\n  ")
        sys.exit(1)
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
