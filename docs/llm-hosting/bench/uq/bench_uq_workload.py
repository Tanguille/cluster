"""uq_decode_fast / uq_prefill_fast geometry at the production workload's operating points.

decode:  16 layer-calls per step in a HIP graph (as vLLM replays decode), NSEQS sequences of CTX tokens each,
         distinct random blocks per sequence. Prints ms per step for each geometry and kernel module.
prefill: one continuation chunk of Q tokens on a CTX-token cached prefix, eager (as vLLM runs it), ms per layer.
usage: bench_uq_workload.py decode 1,4,8 16384,49152,98304 [uq_decode_fast,uq_decode_fast_before]
       bench_uq_workload.py prefill 16,64,128 16384,49152,98304
"""
import importlib
import itertools
import json
import statistics as st
import sys

import torch

from vllm.v1.attention.ops.ultraquant import format as F
from vllm.v1.attention.ops.ultraquant.triton_store import _get_hadamard

free, total = torch.cuda.mem_get_info()
CAP = min(2.5 * 2**30, free - 1.5 * 2**30)  # leave vLLM and Jellyfin their headroom
torch.cuda.set_per_process_memory_fraction(max(CAP, 2**29) / total)
D, HQ, HK, BS, LAYERS = 256, 24, 4, 64, 16
DEV = torch.device("cuda")
SCALE = D**-0.5
H = _get_hadamard(D, DEV)
gs = F.get_group_size()
SLOT = F.slot_size(D, gs)
mode, a, b = sys.argv[1], [int(x) for x in sys.argv[2].split(",")], [int(x) for x in sys.argv[3].split(",")]
modules = [importlib.import_module(m) for m in (sys.argv[4] if len(sys.argv) > 4 else "uq_decode_fast").split(",")]


def cache(n_tokens, g):
    nb = -(-n_tokens // BS)
    c = torch.randint(0, 256, (nb, BS, HK, SLOT), dtype=torch.uint8, device=DEV, generator=g)
    for off, n in ((F.k_scales_offset(D, gs), F.k_scales_bytes(D, gs)), (F.v_scales_offset(D, gs), F.v_scales_bytes(D, gs))):
        c[..., off:off + n] = (F.UE8M0_BIAS + torch.randint(-3, 3, (nb, BS, HK, n), device=DEV, generator=g)).to(torch.uint8)
    return c


def timed_ms(fn, reps=40):
    """(min, median) ms over single calls; vLLM shares the GPU and only adds time, so min is the uncontended cost."""
    times = []
    for _ in range(reps):
        s, e = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        s.record()
        fn()
        e.record()
        e.synchronize()
        times.append(s.elapsed_time(e))
    return round(min(times), 3), round(st.median(times), 3)


def decode(nseqs, ctx):
    g = torch.Generator(device=DEV).manual_seed(nseqs * ctx)
    nb = ctx // BS
    # Production reads 16 distinct layer caches per step. Rotate enough caches that one step touches
    # >= 256 MiB, 4x the 64 MiB Infinity Cache, else short contexts time cache hits.
    n_caches = -(-(256 * 2**20) // (nseqs * ctx * HK * SLOT))
    caches = [cache(nseqs * ctx, g) for _ in range(n_caches)]
    table = torch.randperm(nseqs * nb, device=DEV, generator=g).int().view(nseqs, nb)
    seq_lens = torch.full((nseqs,), ctx, dtype=torch.int32, device=DEV)
    qsl = torch.arange(nseqs + 1, dtype=torch.int32, device=DEV)
    q = torch.randn(nseqs, HQ, D, dtype=torch.bfloat16, device=DEV, generator=g)
    geoms = list(itertools.product((16, 32, 64), ((32, 2), (32, 4), (64, 4), (128, 8))))
    # Two passes, the second in reverse order, every module back to back per geometry: drift from
    # production traffic or the gfx1201 fast/slow band shows up as pass disagreement, not as a module gap.
    for pass_no, order in enumerate((geoms, geoms[::-1])):
        for splits, (tile, warps) in order:
            row = dict(mode="decode", pass_no=pass_no, nseqs=nseqs, ctx=ctx, splits=splits, tile=tile, warps=warps)
            for mod in modules:
                def step():
                    for i in range(LAYERS):
                        mod.uq_decode_fast(q, caches[i % n_caches], table, seq_lens, qsl, SCALE, PiT=H,
                                           max_seq_len=ctx, num_kv_splits=splits, tile_size=tile, num_warps=warps)
                try:
                    step()
                    torch.cuda.synchronize()
                    graph = torch.cuda.CUDAGraph()
                    with torch.cuda.graph(graph):
                        step()
                    row[mod.__name__], row[mod.__name__ + ":p50"] = timed_ms(graph.replay)
                    del graph
                except Exception as e:  # noqa: BLE001
                    row[mod.__name__] = repr(e)[:120].replace("\n", " ")
            print(json.dumps(row), flush=True)
    del caches
    torch.cuda.empty_cache()


def prefill(q_len, ctx):
    g = torch.Generator(device=DEV).manual_seed(q_len * ctx)
    c = cache(ctx + q_len, g)
    bt = torch.randperm(c.shape[0], device=DEV, generator=g).int()[None]
    q = torch.randn(q_len, HQ, D, dtype=torch.bfloat16, device=DEV, generator=g) * 0.1
    for mod, (tile, warps), segs in itertools.product(modules, ((32, 2), (64, 4), (128, 8)), (None, 8, 32)):
        fn = lambda: mod.uq_prefill_fast(q, c, bt, ctx, SCALE, PiT=H, tile_size=tile, num_warps=warps, num_segments=segs)  # noqa: E731
        try:
            fn()
            ms = timed_ms(fn)
        except Exception as e:  # noqa: BLE001
            ms = repr(e)[:120]
        print(json.dumps(dict(mode="prefill", module=mod.__name__, q_len=q_len, ctx=ctx, tile=tile, warps=warps,
                              segments=segs or "auto", ms_per_layer=ms)), flush=True)
    del c
    torch.cuda.empty_cache()


print(json.dumps(dict(free_gib=round(free / 2**30, 2), cap_gib=round(CAP / 2**30, 2))), flush=True)
for x, ctx in itertools.product(a, b):
    (decode if mode == "decode" else prefill)(x, ctx)
print("DONE", flush=True)
