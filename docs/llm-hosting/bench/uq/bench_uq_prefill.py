"""Continuation-chunk attention, one layer (Hq 24, Hk 4, D 256): uq_prefill_fast geometry sweep vs upstream.

  python bench_uq_prefill.py [--quick] [--full] [--q 129,...] [--cached 16384,...] [--reps 40]

upstream   a mirror of UltraQuantAttentionImpl._ultraquant_continuation_prefill (bf16 dequant of the whole
           prefix + copy + flash-attn, ops copied from the source; the real method needs a vLLM workspace
           manager and max_model_len buffers). Same process, same caches, same CUDA-event timing.
fused      uq_prefill_fast at M {16 (reference), 32, 64, 128} x tile {32, 64, 128} x warps {4, 8} x segments.
live       M16/T64/w4 at the auto segment count: the spill baseline (gate: spills <= live default's).

Method (docs/llm-hosting/ultraquant-2026-10-07.md, Methodology): a cell is (cached, q). Its caches are
distinct block tables over one arena of >= 256 MiB of random FP4 slots (the 64 MB Infinity Cache cannot
hold two replays). Every number is the minimum of --reps (40) CUDA-event replays, cycling the tables.
Without --full a geometry is first screened with 5 replays (aborted after 2 if > 4x the best so far)
and only its best segment count and the 6 best geometries get the full 40; the second pass repeats
upstream and the finalists in reverse order and keeps the minimum over both passes.

Budget: one process holds at most UQ_VRAM_CAP_GIB (0.45) of allocator memory (test_uq_decode_fast.set_vram_cap
also shrinks it to keep node free VRAM above 1.84 GiB). Upstream needs ~8 KiB per cached token plus ~46 KiB
per chunk token, so cached lengths are shrunk per q (cached_eff, in 1536-token steps) and, when even that
cannot keep 256 MiB of caches, the arena is relaxed to 128 MiB; both are printed per row (shrunk,
distinct_mib). At the 0.45 GiB cap the 47616 and 98304 requests collapse to ~15-23K tokens (16-24 MiB of
FP4 prefix, resident in the 64 MB Infinity Cache and L2, which flatters the fused kernel because it re-reads
the prefix once per query block); true 48K is 49 MiB and true 98K is 102 MiB, above the Infinity Cache. So
48K, 98K and 260K are NOT measured, and summarize() emits hook q-cap advice only when every q has a
cell with cached_eff >= MIN_TRUSTED_CACHED (49152); otherwise the crossover is marked untrusted. The crossover
gates the picker_default rows (U.GEOMETRY: the hook launches nothing else), not the swept winners.

Output: JSON lines (kind = cap | cell | probe | row | best | crossover), then DONE. One process per q keeps the
shared GPU lock short; merge the outputs with  python3 bench_uq_prefill.py --summarize bench_q*.out
"""
import argparse
import itertools
import json
import math
import os
import re
import sys


MIN_TRUSTED_CACHED = 49152  # below this the prefix fits the Infinity Cache and the ratios flatter the fused kernel


def emit(**row):
    print(json.dumps(row), flush=True)


def summarize(all_rows, qs):
    """Per q: best geometry by geometric-mean ratio over its cells, with and without the spill gate."""
    best = {}
    for q in qs:
        rows = [r for r in all_rows if r["q"] == q and not r.get("picker_default")]
        cells = sorted({r["cached_eff"] for r in rows})
        out = {}
        for gate in (True, False):
            by_cfg = {}
            for r in rows:
                if gate and not r.get("spill_ok"):
                    continue
                by_cfg.setdefault((r["block_m"], r["tile_size"], r["num_warps"]), {})[r["cached_eff"]] = r
            full = {k: v for k, v in by_cfg.items() if len(v) == len(cells)}  # timed in every cell of this q
            if full:
                k = min(full, key=lambda c: sum(math.log(x["ratio"]) for x in full[c].values()))
                out["gated" if gate else "any"] = dict(
                    block_m=k[0], tile_size=k[1], num_warps=k[2],
                    segments_by_cached={c: full[k][c]["segments"] for c in cells},
                    ratio_by_cached={c: full[k][c]["ratio"] for c in cells},
                    geomean_ratio=round(math.exp(sum(math.log(x["ratio"]) for x in full[k].values()) / len(cells)), 3),
                    spills=max(x.get("spills", -1) for x in full[k].values()))
        best[q] = out
        shrunk = sorted({(r["cached_req"], r["cached_eff"]) for r in rows if r["cached_req"] != r["cached_eff"]})
        emit(kind="best", q=q, cells=cells, shrunk_req_to_eff=shrunk, **out)
    # ponytail: only the longest cached_eff per q is checked, not 98K specifically; add a 98K check if 49K alone proves too weak
    trusted = bool(qs) and all(max((r["cached_eff"] for r in all_rows if r["q"] == q), default=0) >= MIN_TRUSTED_CACHED
                               for q in qs)
    # The crossover gates the exact configuration the hook launches (U.GEOMETRY at the picker's own
    # segment count), not the swept winners: those use per-cell segment counts that the hook never launches.
    ok_through, first_above = None, None
    for q in qs:
        cells = {r["cached_eff"] for r in all_rows if r["q"] == q}
        pick = [r for r in all_rows if r["q"] == q and r.get("picker_default")]
        passed = (len({r["cached_eff"] for r in pick}) == len(cells) and
                  all(r["ratio"] is not None and r["ratio"] <= 1.0 and r.get("spill_ok") is not False for r in pick))
        if passed:
            ok_through = q
        else:
            first_above = q
            break
    emit(kind="crossover", gate="picker_default", largest_q_at_or_below_1x=ok_through, first_q_above_1x=first_above,
         trusted=trusted,
         advice=("hook q cap from largest_q_at_or_below_1x up to just below first_q_above_1x; 128 if null" if trusted else
                 f"NOT DERIVABLE: some q has no cell with cached_eff >= {MIN_TRUSTED_CACHED} (cache-resident, shrunk by the VRAM cap); "
                 "keep the hook q cap at 128 and measure true 48K/98K cells (needs more VRAM or a fused-only pass)"))


if "--summarize" in sys.argv:  # merge per-q runs: bench_uq_prefill.py --summarize out1 out2 ... (no GPU, no torch)
    files = sys.argv[sys.argv.index("--summarize") + 1:]
    merged = [r for f in files for r in map(json.loads, filter(lambda ln: ln.startswith("{"), open(f).read().splitlines()))]
    merged = [r for r in merged if r.get("kind") == "row"]  # includes the picker_default rows
    summarize(merged, sorted({r["q"] for r in merged}))
    raise SystemExit(0)

os.environ.setdefault("FLASH_ATTENTION_TRITON_AMD_ENABLE", "TRUE")

import torch  # noqa: E402

import test_uq_decode_fast as T  # noqa: E402  (VRAM cap and node-free guard run on import)
import uq_decode_fast as U  # noqa: E402
from test_uq_decode_fast import BS, D, DEV, HK, HQ  # noqa: E402
from vllm.v1.attention.backends.fa_utils import flash_attn_varlen_func  # noqa: E402
from vllm.v1.attention.ops.ultraquant import format as F  # noqa: E402
from vllm.v1.attention.ops.ultraquant.triton_dequant import ultraquant_full_dequant_kv  # noqa: E402
from vllm.v1.attention.ops.ultraquant.triton_store import _get_hadamard  # noqa: E402

MIB = 2**20
SLOT_BYTES_PER_TOKEN = HK * F.slot_size(D)  # 1088
SCALE = D**-0.5
H = _get_hadamard(D, DEV)
CAP = T.CAP_GIB * 2**30
CUS = torch.cuda.get_device_properties(0).multi_processor_count


# --- cell memory model (bytes); the allocator cap is the real limit, this only picks cached_eff -----------------

def seq_bytes(cached, q):
    return -(-(cached + q) // BS) * BS * SLOT_BYTES_PER_TOKEN


def arena_sets(cached, q, arena_mib):
    return max(2, -(-arena_mib * MIB // seq_bytes(cached, q)))


def upstream_bytes(cached, q):
    alloc = -(-cached // BS) * BS
    # kb, vb (alloc) + kf, vf (cached + q) + q + kc + q_rot bf16 + kc fp32 temp + flash output
    return 4096 * alloc + 4096 * (cached + q) + 12288 * q + 2048 * q + 12288 * q + 4096 * q + 12288 * q


def fused_bytes(q, segments):
    return 12288 * q + 24576 * q + 12288 * q + (24576 * q * segments if segments > 1 else 0)


def fits(cached, q, arena_mib):
    arena = arena_sets(cached, q, arena_mib) * seq_bytes(cached, q)
    return arena + upstream_bytes(cached, q) + 8 * MIB <= CAP


def plan_cell(cached_req, q):
    """(cached_eff, arena_mib): the longest cached <= cached_req that fits with a 256 MiB arena; if that is
    below min(cached_req, 12288), retry with 128 MiB."""
    for arena_mib in (256, 128):
        c = cached_req // 1536 * 1536
        while c >= 1536 and not fits(c, q, arena_mib):
            c -= 1536
        if c >= min(cached_req, 12288) or arena_mib == 128:
            return (c, arena_mib) if c >= 1536 else (0, arena_mib)


def make_arena(nblocks, seed=1):
    g = torch.Generator(device=DEV).manual_seed(seed)
    arena = torch.randint(0, 256, (nblocks, BS, HK, F.slot_size(D)), dtype=torch.uint8, device=DEV, generator=g)
    n = F.k_scales_bytes(D)
    for off in (F.k_scales_offset(D), F.v_scales_offset(D)):  # valid UE8M0 group scales, as in the tests
        arena[..., off:off + n] = torch.randint(F.UE8M0_BIAS - 4, F.UE8M0_BIAS + 2, (nblocks, BS, HK, n),
                                                dtype=torch.uint8, device=DEV, generator=g)
    return arena


# --- timing -------------------------------------------------------------------------------------------------

def timed(fn, nsets, reps, warm=2, give_up_after=None, give_up_ms=None):
    """Minimum CUDA-event ms of fn(set_index) over reps replays that cycle the cache sets."""
    for i in range(warm):
        fn(i % nsets)
    best = math.inf
    for i in range(reps):
        a, b = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
        a.record()
        fn(i % nsets)
        b.record()
        b.synchronize()
        best = min(best, a.elapsed_time(b))
        if give_up_after and i + 1 == give_up_after and best > give_up_ms:
            return best, i + 1
    return best, reps


def kernel_stats(kern):
    """Spill and register counts of the compiled kernel handle U.LAST_KERNEL (None under the interpreter)."""
    if kern is None:
        return {}
    isa = kern.asm.get("amdgcn", "") if hasattr(kern, "asm") else ""
    st = {}
    for key, pat in (("spills", r"\.vgpr_spill_count:\s+(\d+)"), ("vgprs", r"\.vgpr_count:\s+(\d+)"),
                     ("scratch_bytes", r"\.private_segment_fixed_size:\s+(\d+)"),
                     ("lds_bytes", r"\.group_segment_fixed_size:\s+(\d+)")):
        m = re.search(pat, isa)
        if m:
            st[key] = int(m.group(1))
    if "spills" not in st and hasattr(kern, "n_spills"):
        st["spills"] = int(kern.n_spills)
    return st


# --- one cell -----------------------------------------------------------------------------------------------

def upstream_runner(arena, bts, cached, q_t, kc):
    """Mirror of _ultraquant_continuation_prefill, buffers allocated once (production reuses them per layer)."""
    q_len = q_t.shape[0]
    n = cached + q_len
    alloc = -(-cached // BS) * BS
    kb, vb = (torch.empty(1, HK, alloc, D, dtype=torch.bfloat16, device=DEV) for _ in range(2))
    kf, vf = (torch.empty(n, HK, D, dtype=torch.bfloat16, device=DEV) for _ in range(2))
    cu_q = torch.tensor([0, q_len], dtype=torch.int32, device=DEV)
    cu_k = torch.tensor([0, n], dtype=torch.int32, device=DEV)

    def run(i):
        ultraquant_full_dequant_kv(kv_cache=arena, block_table=bts[i], k_out=kb, v_out=vb, alloc_len=alloc)
        kf[:cached] = kb[0, :, :cached].transpose(0, 1)
        vf[:cached] = vb[0, :, :cached].transpose(0, 1)
        kf[cached:] = (kc.float() @ H).to(torch.bfloat16)
        vf[cached:] = kc
        q_rot = (q_t.float() @ H).to(torch.bfloat16)
        return flash_attn_varlen_func(q=q_rot, k=kf, v=vf, cu_seqlens_q=cu_q, cu_seqlens_k=cu_k, max_seqlen_q=q_len,
                                      max_seqlen_k=n, softmax_scale=SCALE, causal=True)
    return run


def auto_segments(q, block_m, tile, cached, base_bytes):
    """The picker's segment count (U.PROGRAMS_PER_CU programs per CU), halved until the partial buffers fit."""
    nqb = -(-q // (block_m // (HQ // HK)))
    s = min(max(1, -(-U.PROGRAMS_PER_CU * CUS // (nqb * HK))), 64, -(-(cached + q) // tile))
    s = 1 << (s.bit_length() - 1)
    while s > 1 and fused_bytes(q, s) + base_bytes + 8 * MIB > CAP:
        s //= 2
    return s


def fused_runner(arena, bts, cached, q_t, **geom):
    return lambda i: U.uq_prefill_fast(q_t, arena, bts[i], cached, SCALE, PiT=H, **geom)


def run_cell(cached_req, cached, arena_mib, q, args, live_spills):
    nb = -(-(cached + q) // BS)
    nsets = arena_sets(cached, q, arena_mib)
    arena = make_arena(nsets * nb)
    g = torch.Generator(device="cpu").manual_seed(cached + q)
    bts = [(s * nb + torch.randperm(nb, generator=g)).int()[None].to(DEV) for s in range(nsets)]
    q_t = (torch.randn(q, HQ, D, generator=g) * 0.1).to(torch.bfloat16).to(DEV)
    kc = torch.randn(q, HK, D, generator=g).to(torch.bfloat16).to(DEV)
    cell = dict(q=q, cached_req=cached_req, cached_eff=cached, shrunk=cached != cached_req, sets=nsets,
                distinct_mib=round(nsets * nb * BS * SLOT_BYTES_PER_TOKEN / MIB), arena_relaxed=arena_mib < 256)
    emit(kind="cell", **cell)

    up = upstream_runner(arena, bts, cached, q_t, kc)
    reps = args.reps
    up_ms, _ = timed(up, nsets, reps)
    del up
    torch.cuda.empty_cache()
    T.guard(f"cell q={q} cached={cached}")

    results = {}  # (M, T, W) -> {S: [ms, stats]}

    def measure(geom, segs, n_reps, give_up_ms=None):
        key = (geom["block_m"], geom["tile_size"], geom["num_warps"])
        fn = fused_runner(arena, bts, cached, q_t, num_segments=segs, **geom)
        try:
            ms, done = timed(fn, nsets, n_reps, give_up_after=2 if give_up_ms else None, give_up_ms=give_up_ms)
        except Exception as e:  # noqa: BLE001  (OOM under the cap, or a geometry that does not compile)
            torch.cuda.empty_cache()
            emit(kind="probe", q=q, cached_eff=cached, **geom, segments=segs,
                 error="oom" if isinstance(e, torch.cuda.OutOfMemoryError) else repr(e)[:160])
            return None
        st = kernel_stats(U.LAST_KERNEL)
        st["spill_ok"] = st.get("spills", 0) <= live_spills[segs == 1] if live_spills else None
        prev = results.setdefault(key, {}).get(segs)
        results[key][segs] = [min(ms, prev[0]) if prev else ms, st, max(done, prev[2]) if prev else done]
        emit(kind="probe", q=q, cached_eff=cached, **geom, segments=segs, reps=done, ms=round(ms, 3), **st)
        return ms

    geoms = [dict(block_m=m, tile_size=t, num_warps=w) for m, t, w in
             itertools.product(args.ms, args.ts, args.ws)]
    best = math.inf
    base = nsets * nb * BS * SLOT_BYTES_PER_TOKEN + 14336 * q  # arena + q_t + kc
    for geom in geoms:  # screen: auto segment count
        s = auto_segments(q, geom["block_m"], geom["tile_size"], cached, base)
        ms = measure(geom, s, reps if args.full else args.probe_reps, None if args.full else 4 * best)
        if ms is not None:
            best = min(best, ms)
    if not results:
        emit(kind="cell", q=q, cached_eff=cached, skipped="no geometry ran")
        return []
    ranked = sorted(results, key=lambda k: min(v[0] for v in results[k].values()))
    finalists = ranked[: (len(ranked) if args.full else 6)]
    for key in finalists:  # sweep segments on the screened winners
        geom = dict(block_m=key[0], tile_size=key[1], num_warps=key[2])
        max_s = min(64, -(-(cached + q) // key[1]))
        for s in (2**i for i in range(7)):
            if s <= max_s and fused_bytes(q, s) + base + 8 * MIB <= CAP:
                if s not in results[key]:
                    measure(geom, s, reps if args.full else args.probe_reps)
    best_cfg = {}
    for key in finalists:  # full reps on each finalist's best segment count
        s = min(results[key], key=lambda x: results[key][x][0])
        geom = dict(block_m=key[0], tile_size=key[1], num_warps=key[2])
        if results[key][s][2] < reps:
            measure(geom, s, reps)
        best_cfg[key] = s
    pgeom = U.GEOMETRY
    try:
        picker_ms, _ = timed(fused_runner(arena, bts, cached, q_t), nsets, reps)
        pst = kernel_stats(U.LAST_KERNEL)
        pseg = auto_segments(q, pgeom["block_m"], pgeom["tile_size"], cached, nsets * nb * BS * SLOT_BYTES_PER_TOKEN + 14336 * q)
        pst["segments"] = pseg
        pst["spill_ok"] = pst.get("spills", 0) <= live_spills[pseg == 1] if live_spills else None
    except torch.cuda.OutOfMemoryError:  # the picker's default segment count can exceed the cap at large q
        picker_ms, pst = math.nan, {}
    # second pass, reverse order: upstream last-to-first with the finalists
    for key in reversed(finalists):
        measure(dict(block_m=key[0], tile_size=key[1], num_warps=key[2]), best_cfg[key], reps)
    up2, _ = timed(upstream_runner(arena, bts, cached, q_t, kc), nsets, reps)
    up_ms = min(up_ms, up2)
    rows = []
    for key in finalists:
        s = best_cfg[key]
        ms, st, _ = results[key][s]
        rows.append(dict(kind="row", q=q, cached_req=cached_req, cached_eff=cached, block_m=key[0], tile_size=key[1],
                         num_warps=key[2], segments=s, ms=round(ms, 3), upstream_ms=round(up_ms, 3),
                         ratio=round(ms / up_ms, 3), reps=reps, **st))
    for r in rows:
        emit(**r)
    picker_row = dict(kind="row", q=q, cached_req=cached_req, cached_eff=cached, picker_default=True,
                      ms=None if math.isnan(picker_ms) else round(picker_ms, 3), upstream_ms=round(up_ms, 3),
                      ratio=None if math.isnan(picker_ms) else round(picker_ms / up_ms, 3), reps=reps, **pgeom, **pst)
    emit(**picker_row)
    return rows + [picker_row]  # summarize() filters the picker row out of the sweep and gates the crossover on it


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--q", default="129,256,512,1001,1536,3072,4096")
    ap.add_argument("--cached", default="16384,47616,98304")
    ap.add_argument("--ms", default="16")  # the 2026-10-08 sweep used 16,32,64,128: every M>=32 geometry spilled and lost
    ap.add_argument("--ts", default="32,64,128")
    ap.add_argument("--ws", default="4,8")
    ap.add_argument("--reps", type=int, default=40)
    ap.add_argument("--probe-reps", type=int, default=5, help="screening replays per geometry and segment count")
    ap.add_argument("--full", action="store_true", help="40 replays for every geometry x segment point (slow)")
    ap.add_argument("--quick", action="store_true", help="smoke: q 129,1001; cached 16384; M 16,64; 5 replays")
    args = ap.parse_args()
    if args.quick:
        args.q, args.cached, args.ms, args.reps = "129,1001", "16384", "16,64", 5
    qs = [int(x) for x in args.q.split(",")]
    cached_req = [int(x) for x in args.cached.split(",")]
    args.ms, args.ts, args.ws = ([int(x) for x in s.split(",")] for s in (args.ms, args.ts, args.ws))
    emit(kind="cap", cap_gib=T.CAP_GIB, node_free_gib=T.node_free_gib(), cus=CUS, floor_gib=T.FLOOR_GIB)

    # spill baseline: the live default (M16/T64/w4) compiled with and without the direct-store path
    nb = 64
    probe_arena = make_arena(nb, seed=2)
    probe_bt = torch.arange(nb, dtype=torch.int32, device=DEV)[None]
    probe_q = torch.zeros(129, HQ, D, dtype=torch.bfloat16, device=DEV)
    live_spills = {}
    for direct in (True, False):
        U.uq_prefill_fast(probe_q, probe_arena, probe_bt, 1024, SCALE, PiT=H, **U.GEOMETRY,
                          num_segments=1 if direct else 2)
        live_spills[direct] = kernel_stats(U.LAST_KERNEL).get("spills", 0)
    del probe_arena, probe_bt, probe_q
    emit(kind="live_default", geometry=U.GEOMETRY, spills_direct=live_spills[True], spills_segmented=live_spills[False])

    all_rows = []
    for q in qs:
        seen = {}
        for c_req in cached_req:
            c_eff, arena_mib = plan_cell(c_req, q)
            if c_eff == 0:
                emit(kind="cell", q=q, cached_req=c_req, skipped="does not fit the allocator cap even at 1536 cached tokens")
                continue
            if c_eff in seen:  # a shrunk cell already measured
                emit(kind="cell", q=q, cached_req=c_req, cached_eff=c_eff, duplicate_of=seen[c_eff])
                continue
            while c_eff >= 1536:
                oom = False
                try:
                    rows = run_cell(c_req, c_eff, arena_mib, q, args, live_spills)
                except torch.cuda.OutOfMemoryError:  # the model above is an estimate; the cap is the truth
                    oom = True
                torch.cuda.empty_cache()  # outside the except block, so the failed cell's tensors are already released
                if not oom:
                    seen[c_eff] = c_req
                    all_rows += rows
                    break
                emit(kind="cell", q=q, cached_req=c_req, cached_eff=c_eff, oom="upstream or setup", retry="1536 fewer cached tokens")
                c_eff -= 1536
    summarize(all_rows, qs)
    print("DONE", flush=True)
