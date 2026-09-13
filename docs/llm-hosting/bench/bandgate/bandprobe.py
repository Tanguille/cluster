#!/usr/bin/env python3
"""One HIP process = one band draw. Print a few 1-second microbench numbers, then exit.

Run N times from the shell; if the numbers are bimodal across spawns, a boot-time gate exists.
Kept small so it can run next to the serving engine: 256 MB working set, ~1 s of GPU.
"""
import os, statistics, sys, time

import torch

dev = torch.device("cuda")
n = 64 * 1024 * 1024  # 256 MB bf16
a = torch.randn(n, dtype=torch.bfloat16, device=dev)
b = torch.empty_like(a)
# a decode-shaped W4A16-ish read: [5120, 17408] int8 weight streamed by a GEMV
w = torch.randint(-128, 127, (5120, 17408 // 2), dtype=torch.int8, device=dev)
x = torch.randn(17408 // 2, dtype=torch.bfloat16, device=dev)


def t(fn, iters=30):
    for _ in range(5):
        fn()
    torch.cuda.synchronize()
    ts = []
    for _ in range(iters):
        s = torch.cuda.Event(enable_timing=True); e = torch.cuda.Event(enable_timing=True)
        s.record(); fn(); e.record(); e.synchronize(); ts.append(s.elapsed_time(e))
    return statistics.median(ts)


copy_ms = t(lambda: b.copy_(a))
gemv_ms = t(lambda: torch.mv(w.to(torch.bfloat16), x))  # dequant-ish read + mv
small_ms = t(lambda: torch.mv(w[:512].to(torch.bfloat16), x), iters=100)
print(f"pid={os.getpid()} copy {2 * n * 2 / copy_ms / 1e6:6.0f} GB/s ({copy_ms:.3f} ms)  "
      f"gemv {gemv_ms:.3f} ms  small {small_ms * 1e3:.0f} us", flush=True)
