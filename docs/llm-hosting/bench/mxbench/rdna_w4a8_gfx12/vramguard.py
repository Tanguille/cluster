"""Per-process VRAM budget for benches that share the R9700 with production.

The node normally has ~2.35 GiB free and must never drop below 1.82 GiB, so one bench process gets
0.45 GiB in total, runtime context included. Three guards:
  * init() refuses to start (exit 3, before any GPU allocation) unless the node has at least
    FLOOR_GIB + BUDGET_GIB free: starting with less would breach the floor by construction.
  * torch's allocator cap only sees tensors, so the non-tensor cost (context, loaded code objects,
    hipBLAS/Triton/quark workspaces) is measured once from node-wide sysfs and reserved on top of a
    fixed margin; the first run of this port measured ~0.21 GiB of it against 0.06 GiB visible at
    the first kernel, hence the default margin.
  * check() fails the run if the node-wide used-VRAM delta since init exceeds the budget, or if node free
    VRAM is below FLOOR_GIB right now (the delta alone passes a start at 2.275 GiB free ending at 1.815).
Without readable amdgpu sysfs every call exits non-zero (fail closed).
Sysfs is node-wide, so production noise shows up in the delta; it is printed, not hidden.
"""
import glob
import os
import sys

import torch

BUDGET_GIB = float(os.environ.get("MXW4A8_BUDGET_GIB", "0.45"))  # raise only while production is paused
FLOOR_GIB = 1.82
_GIB = 1 << 30
_u0 = None
_overhead = 0
_total = 0
_peak_delta = 0
_max_reserved = 0
_cap = 0


def _sysfs(name: str) -> int:
    paths = glob.glob("/sys/class/drm/card*/device/mem_info_vram_total")
    if not paths:
        sys.exit("[vramguard] no amdgpu mem_info_vram_total in sysfs: cannot enforce the VRAM floor, refusing to run")
    return int(open(os.path.join(os.path.dirname(paths[0]), name)).read())


def used() -> int:
    return _sysfs("mem_info_vram_used")


def init(budget_gib: float = BUDGET_GIB, ctx_reserve_gib: float = 0.16) -> None:
    """Call before the first CUDA op."""
    global _u0, _overhead, _total, _cap
    _u0 = used()
    _total = _sysfs("mem_info_vram_total")
    free = (_total - _u0) / _GIB
    need = FLOOR_GIB + budget_gib
    print(f"[vramguard] node free {free:.3f} GiB, need >= {need:.2f} (floor {FLOOR_GIB} + budget {budget_gib})", flush=True)
    if free < need and not os.environ.get("MXW4A8_IGNORE_FLOOR"):
        print("[vramguard] REFUSING to start: free VRAM is below floor + budget", flush=True)
        sys.exit(3)
    torch.zeros(1, device="cuda")
    torch.cuda.synchronize()
    u1 = used()
    _overhead = max(u1 - _u0, 0) + int(ctx_reserve_gib * _GIB)
    cap = _cap = max(budget_gib * _GIB - _overhead, 0)
    torch.cuda.set_per_process_memory_fraction(cap / _total)
    print(f"[vramguard] context overhead {_overhead / _GIB:.3f} GiB (incl. {ctx_reserve_gib} margin), "
          f"tensor cap {cap / _GIB:.3f} GiB of {budget_gib} GiB budget", flush=True)


def tensor_cap_bytes() -> int:
    """Allocator cap set by init(), for callers that skip work that cannot fit."""
    return _cap


def check(tag: str = "") -> None:
    """Report the tensor high-water mark since the last check and the node-wide delta; fail if over."""
    global _peak_delta, _max_reserved
    peak = torch.cuda.max_memory_reserved()
    _max_reserved = max(_max_reserved, peak)
    torch.cuda.reset_peak_memory_stats()
    now = used()
    delta = now - _u0
    _peak_delta = max(_peak_delta, delta)
    print(f"[vramguard] {tag}: tensors peak {peak / _GIB:.3f} GiB (run max {_max_reserved / _GIB:.3f}) + context "
          f"{_overhead / _GIB:.3f}; node-wide used delta {delta / _GIB:+.3f} GiB (max {_peak_delta / _GIB:+.3f}); "
          f"free now {(_total - now) / _GIB:.3f} GiB", flush=True)
    assert (_total - now) >= FLOOR_GIB * _GIB or os.environ.get("MXW4A8_IGNORE_FLOOR"), \
        f"node free VRAM {(_total - now) / _GIB:.3f} GiB below the {FLOOR_GIB} GiB floor"
    assert _max_reserved + _overhead <= BUDGET_GIB * _GIB + (1 << 20), "over the 0.45 GiB per-process budget"
    assert _peak_delta <= (BUDGET_GIB + 0.02) * _GIB, "node-wide VRAM delta over the 0.45 GiB budget"
