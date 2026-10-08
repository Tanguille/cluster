#!/usr/bin/env python3
"""Compile safety of the gfx12 port: torch.compile(fullgraph=True) plus CUDA graph capture/replay.

For M = 1..8 (the W4A8 path) and M = 9 (the dequant fallback inside the same custom op):
  1. torch.compile(fullgraph=True, dynamic=False) of the op and of the kernel class apply_weights
     matches eager bit-for-bit (the fake impl lets dynamo trace; inductor must not reorder it).
  2. torch.compile(fullgraph=True, dynamic=True): one graph serving every M.
  3. The compiled function captured in a torch.cuda.CUDAGraph, replayed with fresh inputs copied
     into the static buffer, matches eager on those inputs; so does the raw op captured directly.
  python3 test_compile.py
"""
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
from vllm.model_executor.layers.quantization.utils.quant_utils import kMxfp4Dynamic  # noqa: E402

dev = torch.device("cuda")
N, K = 1024, 4096
fails = []


def check(tag, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'} {tag} {detail}", flush=True)
    if not cond:
        fails.append(tag)


def main():
    g = torch.Generator(device=dev).manual_seed(0)
    w = torch.randint(0, 256, (N, K // 2), dtype=torch.uint8, device=dev, generator=g)
    s = torch.randint(122, 131, (N, K // 32), dtype=torch.uint8, device=dev, generator=g)
    bias = torch.randn(N, device=dev, dtype=torch.bfloat16, generator=g)
    layer = torch.nn.Module()
    layer.weight = torch.nn.Parameter(w, requires_grad=False)
    layer.weight_scale = torch.nn.Parameter(s, requires_grad=False)
    kern = kmod.RdnaW4A8MxFp4LinearKernel(MxFp4LinearLayerConfig(activation_quant_key=kMxfp4Dynamic))
    kern.process_weights_after_loading(layer)

    def f_op(x):
        return torch.ops.vllm.rdna_mxfp4_w4a8_apply(x, layer.weight, layer.weight_scale, bias, True)

    def f_cls(x):
        return kern.apply_weights(layer, x, bias)

    def xin(M, seed):
        gg = torch.Generator(device=dev).manual_seed(seed)
        return (torch.randn((M, K), device=dev, generator=gg) * 0.1).to(torch.bfloat16)

    Ms = list(range(1, 10))
    print("== static-shape compile, fullgraph=True")
    for name, f in (("op", f_op), ("class.apply_weights", f_cls)):
        for M in Ms:
            torch._dynamo.reset()  # 9 shapes in one dynamic=False function exceed dynamo's 8-specialization limit
            cf = torch.compile(f, fullgraph=True, dynamic=False)
            x = xin(M, M)
            check(f"{name} M={M} compiled == eager", torch.equal(cf(x), f(x)))
    vramguard.check("static compile")

    print("== dynamic-shape compile, fullgraph=True (one graph, M symbolic)")
    torch._dynamo.reset()
    cf = torch.compile(f_cls, fullgraph=True, dynamic=True)
    for M in Ms:
        x = xin(M, 100 + M)
        check(f"class M={M} dynamic compiled == eager", torch.equal(cf(x), f_cls(x)))

    print("== CUDA graph capture + replay (compiled function), M = 1..9 (9 = the dequant fallback inside the graph)")
    for M in Ms:
        torch._dynamo.reset()
        cf = torch.compile(f_cls, fullgraph=True, dynamic=False)
        static = xin(M, 200 + M)
        side = torch.cuda.Stream()
        side.wait_stream(torch.cuda.current_stream())
        with torch.cuda.stream(side):
            for _ in range(3):
                cf(static)
        torch.cuda.current_stream().wait_stream(side)
        torch.cuda.synchronize()
        graph = torch.cuda.CUDAGraph()
        with torch.cuda.graph(graph):
            out = cf(static)
        for trial in range(3):  # new data each replay: the graph must read the static buffer
            static.copy_(xin(M, 300 + 10 * M + trial))
            graph.replay()
            torch.cuda.synchronize()
            check(f"compiled graph M={M} replay {trial} == eager", torch.equal(out, f_cls(static)))
        del graph, out

    print("== CUDA graph capture + replay (raw op, one graph per M, replayed back to back)")
    graphs = {}
    statics = {}
    outs = {}
    for M in Ms:
        statics[M] = xin(M, 400 + M)
        f_op(statics[M])
        torch.cuda.synchronize()
        graphs[M] = torch.cuda.CUDAGraph()
        with torch.cuda.graph(graphs[M]):
            outs[M] = f_op(statics[M])
    for rnd in range(2):
        for M in Ms:
            statics[M].copy_(xin(M, 500 + 10 * M + rnd))
            graphs[M].replay()
        torch.cuda.synchronize()
        for M in Ms:
            check(f"raw-op graph M={M} round {rnd} == eager", torch.equal(outs[M], f_op(statics[M])))
    vramguard.check("end")
    print(f"\nSUMMARY: {len(fails)} failed")
    if fails:
        print("FAILED:", *fails, sep="\n  ")
        sys.exit(1)
    print("RESULT: PASS")


if __name__ == "__main__":
    main()
