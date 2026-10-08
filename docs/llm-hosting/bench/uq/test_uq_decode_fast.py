"""Correctness gate for uq_decode_fast.

GPU (pod image):  python test_uq_decode_fast.py           ctx 1K/4K/64K, also runs the upstream launcher
CPU interpreter:  TRITON_INTERPRET=1 python test_uq_decode_fast.py
                  (no vllm: loads the real upstream files from ./up through cpu_vllm_shim; small ctx)

Reference: upstream ultraquant_full_dequant_kv into bf16 (exact for FP4 x 2^k), then
fp32 softmax attention with the Hadamard-rotated query. Exits non-zero on any failure.

GPU budget: the card is shared with production, so one process may hold at most UQ_VRAM_CAP_GIB (0.45)
of allocator memory, and the cap shrinks further if node free VRAM would drop below FLOOR_GIB (largest
observed Jellyfin excursion 1.82 GiB + margin). UQ_TEST_CTXS overrides the decode contexts.
"""

import glob
import json
import os
import sys

import numpy as np
import torch
import triton
import triton.language as tl

ON_GPU = torch.cuda.is_available() and os.environ.get("TRITON_INTERPRET") != "1"
try:
    import vllm  # noqa: F401
except ImportError:
    import cpu_vllm_shim

    cpu_vllm_shim.install()

import uq_decode_fast as U  # noqa: E402
from vllm.v1.attention.ops.ultraquant import format as F  # noqa: E402
from vllm.v1.attention.ops.ultraquant.triton_dequant import ultraquant_full_dequant_kv  # noqa: E402
from vllm.v1.attention.ops.ultraquant.triton_store import _get_hadamard  # noqa: E402

DEV = torch.device("cuda" if ON_GPU else "cpu")
FLOOR_GIB = 1.84


def node_free_gib():
    """Node-wide free VRAM from amdgpu sysfs (same source as bench/vramfree.sh); None off-GPU."""
    for d in sorted(glob.glob("/sys/class/drm/card*/device")):
        try:
            return (int(open(d + "/mem_info_vram_total").read()) - int(open(d + "/mem_info_vram_used").read())) / 2**30
        except OSError:
            continue
    return None


def guard(where):
    free = node_free_gib()
    if free is None and ON_GPU:
        raise SystemExit(f"ABORT at {where}: amdgpu sysfs unreadable, cannot enforce the {FLOOR_GIB} GiB node floor")
    if free is not None and free < FLOOR_GIB:
        raise SystemExit(f"ABORT at {where}: node free VRAM {free:.3f} GiB < {FLOOR_GIB}")
    return free


MIN_CAP_GIB = 0.1


def set_vram_cap():
    """Allocator cap = min(UQ_VRAM_CAP_GIB, node free VRAM after the context exists - FLOOR_GIB)."""
    free = guard("before the HIP context")  # the context itself costs node VRAM: refuse without headroom before paying it
    if free < FLOOR_GIB + MIN_CAP_GIB:
        raise SystemExit(f"ABORT: node free {free:.3f} GiB leaves no room for a context above the {FLOOR_GIB} GiB floor")
    torch.zeros(1, device=DEV)  # the HIP context costs node VRAM that the allocator cap does not count
    free = guard("after the HIP context")
    cap = min(float(os.environ.get("UQ_VRAM_CAP_GIB", "0.45")), free - FLOOR_GIB)
    if cap < MIN_CAP_GIB:
        raise SystemExit(f"ABORT: only {cap:.3f} GiB of allocator budget (node free {free} GiB, floor {FLOOR_GIB})")
    torch.cuda.set_per_process_memory_fraction(cap * 2**30 / torch.cuda.get_device_properties(0).total_memory)
    print(json.dumps(dict(vram_cap_gib=round(cap, 3), node_free_gib=free)), flush=True)
    return cap


if ON_GPU:
    CAP_GIB = set_vram_cap()
D, HQ, HK, BS = 256, 24, 4, 64
PROD_BS = 1536  # production KV block size; BS keeps most cases small, bs=PROD_BS cases cover its index math and strides
FAILED = []


def check(name, ok, **info):
    if ON_GPU:
        info.update(peak_alloc_mib=round(torch.cuda.max_memory_allocated() / 2**20), node_free_gib=guard(name))
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.empty_cache()
    print(json.dumps(dict(test=name, ok=bool(ok), **info)), flush=True)
    if not ok:
        FAILED.append(name)


# --- 1. bit tricks, run through the kernel's own helpers (compiled on GPU, interpreted on CPU)


@triton.jit
def _helpers_kernel(lo_ptr, hi_ptr, mult_ptr, prod_ptr, SHIFT: tl.constexpr):
    b = tl.arange(0, 256).to(tl.uint8)
    lo, hi = U._fp4x2_to_f16(b)
    mult = U._ue8m0_to_f16(b, 127, SHIFT)
    tl.store(lo_ptr + tl.arange(0, 256), lo)
    tl.store(hi_ptr + tl.arange(0, 256), hi)
    tl.store(mult_ptr + tl.arange(0, 256), mult)
    # prod[e, code] = decoded code * multiplier(e), the exact fp16 multiply the kernel does
    tl.store(prod_ptr + tl.arange(0, 256)[:, None] * 256 + tl.arange(0, 256)[None, :], lo[None, :] * mult[:, None])


def test_bit_tricks(shift=U.DEFAULT_SHIFT):
    lo, hi, mult = (torch.empty(256, dtype=torch.float16, device=DEV) for _ in range(3))
    prod = torch.empty(256, 256, dtype=torch.float16, device=DEV)
    _helpers_kernel[(1,)](lo, hi, mult, prod, SHIFT=shift)
    lo, hi, mult, prod = (t.cpu().double().numpy() for t in (lo, hi, mult, prod))
    table = np.array(F.FP4_BITS_TO_VALUE)
    b = np.arange(256)
    want_lo, want_hi = table[b & 15] * 2.0**-14, table[b >> 4] * 2.0**-14
    sign_ok = np.array_equal(np.signbit(lo), b & 8 > 0) and np.array_equal(np.signbit(hi), b & 128 > 0)
    check("fp4_bit_trick_all_256_bytes", np.array_equal(lo, want_lo) and np.array_equal(hi, want_hi) and sign_ok)

    y = b - F.UE8M0_BIAS
    exact_win = (y >= -9 - shift) & (y <= 15 - shift)
    want_mult = np.where(b == 0, 0.0, 2.0 ** (y + shift))
    mult_ok = np.array_equal(mult[(b == 0) | ((y + shift >= -14) & (y + shift <= 15))],
                             want_mult[(b == 0) | ((y + shift >= -14) & (y + shift <= 15))])
    check("ue8m0_multiplier", mult_ok and np.isfinite(mult).all(), saturates_above_y=15 - shift)

    # Products: decoded lo nibble (codes 0..15 repeat across bytes) times multiplier(e).
    want_prod = np.where(b[:, None] == 0, 0.0, table[None, b & 15] * 2.0 ** (y[:, None] + shift - 14))
    rows = exact_win | (b == 0)
    bad = np.flatnonzero(~(prod[rows] == want_prod[rows]).all(axis=1))
    check("fp16_scaled_products_exact", bad.size == 0 and np.isfinite(prod).all(),
          exact_y_window=[-9 - shift, 15 - shift], bad_scale_bytes=b[rows][bad].tolist()[:8])


def test_even_odd_algebra():
    rng = np.random.default_rng(0)
    q, k, v, p = rng.normal(size=(6, D)), rng.normal(size=(32, D)), rng.normal(size=(32, D)), rng.random((6, 32))
    s_ok = np.allclose(q @ k.T, q[:, 0::2] @ k[:, 0::2].T + q[:, 1::2] @ k[:, 1::2].T, rtol=0, atol=1e-12)
    out = np.empty((6, D))
    out[:, 0::2], out[:, 1::2] = p @ v[:, 0::2], p @ v[:, 1::2]
    check("even_odd_split_algebra", s_ok and np.allclose(out, p @ v, rtol=0, atol=1e-12))


# --- 2. end-to-end attention


def make_case(lens, seed, share_table=False, y_range=(-6, 4), bs=BS):
    g = torch.Generator(device="cpu").manual_seed(seed)
    nblk = [-(-n // bs) for n in lens]
    total = (max(nblk) if share_table else sum(nblk)) + 3
    cache = torch.randint(0, 256, (total, bs, HK, F.slot_size(D)), dtype=torch.uint8, generator=g)
    for off in (F.k_scales_offset(D), F.v_scales_offset(D)):
        n = F.k_scales_bytes(D)
        sc = F.UE8M0_BIAS + torch.randint(*y_range, (total, bs, HK, n), generator=g)
        sc[torch.rand(sc.shape, generator=g) < 0.01] = 0  # zero-group sentinel
        cache[..., off:off + n] = sc.to(torch.uint8)
    perm = torch.randperm(total, generator=g).int()
    width = max(nblk) + 2
    if share_table:  # continuation-chunk shape: one table row expanded with stride 0
        bt = perm[:width][None].expand(len(lens), -1)
    else:
        bt = torch.zeros(len(lens), width, dtype=torch.int32)
        start = 0
        for i, n in enumerate(nblk):
            bt[i, :n] = perm[start:start + n]
            start += n
    q = (torch.randn(len(lens), HQ, D, generator=g) * 0.1).to(torch.bfloat16)
    seq_lens = torch.tensor(lens, dtype=torch.int32)
    qsl = torch.arange(len(lens) + 1, dtype=torch.int32)
    return [t.to(DEV) for t in (q, cache)] + [bt.to(DEV), seq_lens.to(DEV), qsl.to(DEV)]


def make_stored_case(n, seed, bs=BS):
    """Realistic scales: Gaussian K (a few 8x outlier dims) and V written by upstream ultraquant_store."""
    from vllm.v1.attention.ops.ultraquant.triton_store import ultraquant_store

    g = torch.Generator(device="cpu").manual_seed(seed)
    nb = -(-n // bs) + 2
    bt = torch.randperm(nb, generator=g).int()[None]
    k = torch.randn(n, HK, D, generator=g) * 2
    k[..., :4] *= 8
    v = torch.randn(n, HK, D, generator=g) * 0.5
    pos = torch.arange(n)
    slots = (bt[0, pos // bs].long() * bs + pos % bs).to(DEV)
    cache = torch.zeros(nb, bs, HK, F.slot_size(D), dtype=torch.uint8, device=DEV)
    k, v = k.to(DEV, torch.bfloat16), v.to(DEV, torch.bfloat16)
    ultraquant_store(k, v, cache, slots)
    q = (torch.randn(1, HQ, D, generator=g) * 0.5).to(DEV, torch.bfloat16)
    sl = torch.tensor([n], dtype=torch.int32, device=DEV)
    qsl = torch.tensor([0, 1], dtype=torch.int32, device=DEV)
    return (q, cache, bt.to(DEV), sl, qsl), (k, v)


REF_SECTION = 4096  # tokens dequantised at a time, so a 49K reference needs ~60 MiB instead of ~500


def attend_ref(q_rot, cache, bt, limit, scale, chunk_kv=None, cached_len=0):
    """fp32 causal attention of Hadamard-rotated queries [R, HQ, D] over a sequence's dequantised cache.

    Row r sees keys 0..limit[r]. Dequant and online softmax run per REF_SECTION tokens. chunk_kv =
    (k_rot [q_len, HK, D], v [q_len, HK, D]) replaces the keys at positions >= cached_len, to model
    upstream's bf16 intra-chunk K/V instead of the FP4 read-back.
    """
    R = q_rot.shape[0]
    bs = cache.shape[1]
    section = max(REF_SECTION // bs, 1) * bs  # whole blocks per section
    n = int(limit.max()) + 1
    G = HQ // HK
    qg = q_rot.float().view(R, HK, G, D)
    m = torch.full((R, HK, G), float("-inf"), device=q_rot.device)
    l = torch.zeros((R, HK, G), device=q_rot.device)
    acc = torch.zeros((R, HK, G, D), device=q_rot.device)
    for s0 in range(0, n, section):
        s1 = min(s0 + section, n)
        alloc = -(-(s1 - s0) // bs) * bs
        kb = torch.empty(1, HK, alloc, D, dtype=torch.bfloat16, device=q_rot.device)
        vb = torch.empty_like(kb)
        ultraquant_full_dequant_kv(cache, bt[:, s0 // bs:s0 // bs + alloc // bs], kb, vb, alloc)
        k, v = kb[0, :, :s1 - s0].float(), vb[0, :, :s1 - s0].float()
        if chunk_kv is not None and s1 > cached_len:
            lo = max(s0, cached_len)
            k[:, lo - s0:] = chunk_kv[0][lo - cached_len:s1 - cached_len].permute(1, 0, 2).float()
            v[:, lo - s0:] = chunk_kv[1][lo - cached_len:s1 - cached_len].permute(1, 0, 2).float()
        s = scale * torch.einsum("rhgd,hnd->rhgn", qg, k)
        keep = torch.arange(s0, s1, device=q_rot.device)[None, :] <= limit[:, None]
        s = s.masked_fill(~keep[:, None, None, :], float("-inf"))
        m_new = torch.maximum(m, s.amax(-1))
        m_safe = torch.where(torch.isfinite(m_new), m_new, torch.zeros_like(m_new))
        p = torch.exp(s - m_safe[..., None])
        alpha = torch.exp(m - m_safe)
        l = l * alpha + p.sum(-1)
        acc = acc * alpha[..., None] + torch.einsum("rhgn,hnd->rhgd", p, v)
        m = m_new
    return (acc / l[..., None]).view(R, HQ, D)


def reference(q, cache, bt, seq_lens, scale, H):
    out = torch.empty(q.shape, dtype=torch.float32, device=q.device)
    q_rot = q.float() @ H
    for i, n in enumerate(seq_lens.tolist()):
        out[i] = attend_ref(q_rot[i:i + 1], cache, bt[i:i + 1], torch.tensor([n - 1], device=q.device), scale)[0]
    return out


def errors(out, ref):
    out = out.float()
    cos = torch.nn.functional.cosine_similarity(out.flatten(1), ref.flatten(1), dim=-1)  # per sequence
    return dict(max_abs=float((out - ref).abs().max()), min_cos=float(cos.min()), ref_max=float(ref.abs().max()))


def test_attention(name, case, splits=32, tile=32, raw_kv=None):  # tile 32: the upstream tuned geometry
    q, cache, bt, seq_lens, qsl = case
    lens = seq_lens.tolist()
    scale = D**-0.5
    H = _get_hadamard(D, DEV)
    ref = reference(q, cache, bt, seq_lens, scale, H)
    mine = U.uq_decode_fast(q, cache, bt, seq_lens, qsl, scale, PiT=H, max_seq_len=max(lens),
                            num_kv_splits=splits, tile_size=tile)
    e_mine = errors(mine, ref)
    # Both kernels write bf16 output, so this rounding is the error floor.
    bf16_floor = float((ref.to(torch.bfloat16).float() - ref).abs().max())
    info = dict(lens=lens, splits=splits, tile=tile, bf16_floor_max_abs=bf16_floor, fast=e_mine)
    ok = e_mine["min_cos"] > 0.9995 and e_mine["max_abs"] < 1e-2 * e_mine["ref_max"] + 1e-3
    if raw_kv is not None:  # unquantized, unrotated attention: catches a rotation-convention mismatch
        k, v = (t.float().permute(1, 0, 2) for t in raw_kv)
        qg = q[0].float().view(HK, HQ // HK, D)
        p = torch.softmax(scale * torch.einsum("kgd,knd->kgn", qg, k), dim=-1)
        info["vs_unquantized_cos"] = errors(mine, torch.einsum("kgn,knd->kgd", p, v).reshape(1, HQ, D))["min_cos"]
        ok = ok and info["vs_unquantized_cos"] > 0.95
        for part, off in (("k", F.k_scales_offset(D)), ("v", F.v_scales_offset(D))):
            y = cache[..., off:off + F.k_scales_bytes(D)].int()
            y = y[y > 0] - F.UE8M0_BIAS
            info[f"{part}_scale_y_range"] = [int(y.min()), int(y.max())]
    if ON_GPU:
        from vllm.v1.attention.ops.ultraquant.triton_unified_attention import ultraquant_unified_attention

        try:  # zz_lds_gate_impl (if loaded) adds its warps/stages to this launch
            stock = ultraquant_unified_attention(q, cache, bt, seq_lens, qsl, scale, PiT=H, max_query_len=1,
                                                 max_seq_len=max(lens), num_kv_splits=splits, tile_size=tile)
        except Exception as e:  # noqa: BLE001  (stock may not compile at this geometry; gate on fast alone)
            info["stock_error"] = repr(e)[:160]
            return check(name, ok, **info)
        e_stock = errors(stock, ref)
        info["stock"] = e_stock
        info["fast_vs_stock_max_abs"] = float((mine.float() - stock.float()).abs().max())
        ok = ok and e_mine["max_abs"] <= 2 * e_stock["max_abs"] + 1e-4
    check(name, ok, **info)


if __name__ == "__main__":
    test_even_odd_algebra()
    test_bit_tricks()
    if ON_GPU:  # 64K needs UQ_VRAM_CAP_GIB ~1.5 and UQ_TEST_CTXS=1024,4096,65536 (ragged sets sum to ~3 contexts)
        ctxs = tuple(int(c) for c in os.environ.get("UQ_TEST_CTXS", "1024,4096,16384").split(","))
    else:
        ctxs = (100, 700)
    rng = np.random.default_rng(7)
    for ctx in ctxs:
        test_attention(f"attn_ctx{ctx}_1seq", make_case([ctx], seed=ctx))
        ragged = sorted({ctx, *rng.integers(1, ctx, size=4).tolist()}, reverse=True)
        test_attention(f"attn_ctx{ctx}_{len(ragged)}seq_ragged", make_case(ragged, seed=ctx + 1))
    test_attention("attn_tile64_splits8", make_case([ctxs[-1], 33], seed=2), splits=8, tile=64)
    test_attention("attn_tile128_spans_blocks", make_case([ctxs[-1], 130, 5], seed=5), tile=128)
    # Group exponents below 1 - SHIFT make fp16 subnormal WMMA inputs; on GPU this fails
    # (cosine drops) if the hardware flushes them.
    test_attention("attn_subnormal_operands", make_case([ctxs[0]], seed=6, y_range=(-15, -6)), tile=128)
    lens = [ctxs[0] - 2, ctxs[0] - 1, ctxs[0]]
    test_attention("attn_continuation_shared_table", make_case(lens, seed=3, share_table=True))
    case, raw = make_stored_case(ctxs[1] if ON_GPU else 300, seed=4)
    test_attention("attn_stored_gaussian", case, raw_kv=raw)
    # Production block size: block-index div/mod and cache strides differ from BS=64, so cover boundaries and the shared table.
    test_attention("attn_bs1536_boundary_ragged", make_case([PROD_BS + 37, PROD_BS, 5], seed=7, bs=PROD_BS))
    test_attention("attn_bs1536_continuation_shared_table",
                   make_case([PROD_BS - 1, PROD_BS, PROD_BS + 1], seed=8, share_table=True, bs=PROD_BS))
    print("FAILED: %s" % FAILED if FAILED else "ALL PASSED", flush=True)
    sys.exit(1 if FAILED else 0)
