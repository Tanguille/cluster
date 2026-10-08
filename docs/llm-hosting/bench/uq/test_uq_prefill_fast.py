"""Correctness gate for uq_prefill_fast (continuation chunk over the UltraQuant cache).

GPU (pod image):  python test_uq_prefill_fast.py        (one process stays under UQ_VRAM_CAP_GIB, default 0.45)
CPU interpreter:  TRITON_INTERPRET=1 python test_uq_prefill_fast.py   (small sizes)

Reference: upstream ultraquant_full_dequant_kv of the whole sequence, fp32 causal softmax with query
token t at position cached_len + t (test_uq_decode_fast.attend_ref, streamed so 49K needs ~60 MiB).
Gate, per query token: cosine > 0.9995 AND relative L2 < 1e-2.
Also here:
  * the picker and its WMMA tile rule,
  * a bf16-intra-chunk reference that sizes the semantic change (upstream reads the chunk's own K/V as
    bf16, the fast path reads them back as FP4),
  * negative variants (off-by-one causal limit, wrong scale, unrotated query) that must FAIL the gate.
Exits non-zero on any failure.
"""

import json
import sys

import torch

import test_uq_decode_fast as T  # installs the CPU shim when vllm is absent
from test_uq_decode_fast import BS, D, DEV, HK, HQ, ON_GPU, PROD_BS, attend_ref, check, make_case
from vllm.v1.attention.ops.ultraquant.triton_store import _get_hadamard, ultraquant_store

U = T.U
COS_MIN, REL_L2_MAX = 0.9995, 1e-2
TILE_R = 16  # query tokens per reference tile


def compare(mine, q, cache, bt, cached_len, scale, H, chunk_kv=None):
    """Worst per-token cosine and relative L2 of mine [q_len, HQ, D] against the fp32 reference, tile by tile."""
    q_len = q.shape[0]
    cos_min, rel_max = 2.0, 0.0
    for t0 in range(0, q_len, TILE_R):
        t1 = min(t0 + TILE_R, q_len)
        limit = cached_len + torch.arange(t0, t1, device=q.device)
        ref = attend_ref(q[t0:t1].float() @ H, cache, bt, limit, scale, chunk_kv, cached_len).flatten(1)
        got = mine[t0:t1].float().flatten(1)
        cos_min = min(cos_min, float(torch.nn.functional.cosine_similarity(got, ref, dim=-1).min()))
        rel_max = max(rel_max, float(((got - ref).norm(dim=-1) / ref.norm(dim=-1)).max()))
    return dict(min_cos_per_token=cos_min, max_rel_l2_per_token=rel_max, finite=bool(torch.isfinite(mine).all()))


def passes(m):
    return m["finite"] and m["min_cos_per_token"] > COS_MIN and m["max_rel_l2_per_token"] < REL_L2_MAX


def make_q(q_len, seed):
    g = torch.Generator(device="cpu").manual_seed(seed + 100)
    return (torch.randn(q_len, HQ, D, generator=g) * 0.1).to(torch.bfloat16).to(DEV)  # cast on the CPU, not on the card


SKIPPED = []


def big_enough(name, need_gib):
    """GPU-only cases whose buffers (q, rotated q, output, partial segments) need more than the allocator cap."""
    cap = T.CAP_GIB
    if cap >= need_gib:
        return True
    print(json.dumps(dict(test=name, skipped=f"needs ~{need_gib} GiB, cap is {cap:.2f} GiB")), flush=True)
    SKIPPED.append(name)
    return False


def block_q_of(kw):
    return kw.get("block_m", 16) // (HQ // HK)


def test_prefill(name, cached_len, q_len, seed, bs=BS, **kw):
    _, cache, bt, _, _ = make_case([cached_len + q_len], seed=seed, bs=bs)
    q, scale, H = make_q(q_len, seed), D**-0.5, _get_hadamard(D, DEV)
    mine = U.uq_prefill_fast(q, cache, bt, cached_len, scale, PiT=H, **kw)
    m = compare(mine, q, cache, bt, cached_len, scale, H)
    check(name, passes(m), cached_len=cached_len, q_len=q_len, bs=bs, q_mod_block_q=q_len % block_q_of(kw), **kw, **m)


def test_negative(name, cached_len, q_len, seed, cached_len_shift=0, scale_mul=1.0, unrotated=False):
    """The honest call must pass the gate, the mutated one must fail it (the gate has teeth at this size)."""
    _, cache, bt, _, _ = make_case([cached_len + q_len], seed=seed)
    q, scale, H = make_q(q_len, seed), D**-0.5, _get_hadamard(D, DEV)
    honest = passes(compare(U.uq_prefill_fast(q, cache, bt, cached_len, scale, PiT=H), q, cache, bt, cached_len, scale, H))
    mine = U.uq_prefill_fast(q, cache, bt, cached_len + cached_len_shift, scale * scale_mul,
                             PiT=torch.eye(D, device=DEV) if unrotated else H)
    m = compare(mine, q, cache, bt, cached_len, scale, H)  # the reference always uses the true cached_len, scale and H
    check(name, honest and not passes(m), control_passes=honest, mutated_fails_gate=not passes(m), **m)


def test_intra_chunk_semantics(name, cached_len, q_len, seed, bs=BS):
    """Fast path vs the FP4-dequant reference (the gate) and vs a reference that keeps the chunk's own K/V in bf16."""
    from vllm.v1.attention.ops.ultraquant.format import slot_size

    n = cached_len + q_len
    g = torch.Generator(device="cpu").manual_seed(seed)
    nb = -(-n // bs) + 2
    bt = torch.randperm(nb, generator=g).int()[None]
    k = torch.randn(n, HK, D, generator=g) * 2
    k[..., :4] *= 8  # a few outlier dims, as real K has
    v = torch.randn(n, HK, D, generator=g) * 0.5
    pos = torch.arange(n)
    slots = (bt[0, pos // bs].long() * bs + pos % bs).to(DEV)
    cache = torch.zeros(nb, bs, HK, slot_size(D), dtype=torch.uint8, device=DEV)
    k, v = k.to(torch.bfloat16).to(DEV), v.to(torch.bfloat16).to(DEV)
    ultraquant_store(k, v, cache, slots)  # rotates K and quantises both, as do_kv_cache_update does
    bt = bt.to(DEV)
    q = (torch.randn(q_len, HQ, D, generator=g) * 0.5).to(torch.bfloat16).to(DEV)
    scale, H = D**-0.5, _get_hadamard(D, DEV)
    mine = U.uq_prefill_fast(q, cache, bt, cached_len, scale, PiT=H)
    gate = compare(mine, q, cache, bt, cached_len, scale, H)
    chunk_kv = ((k[cached_len:].float() @ H).to(torch.bfloat16), v[cached_len:])  # what upstream feeds flash-attn
    sem = compare(mine, q, cache, bt, cached_len, scale, H, chunk_kv=chunk_kv)
    check(name, passes(gate) and sem["finite"] and sem["min_cos_per_token"] > 0.9,  # sizing, not a pass bar
          cached_len=cached_len, q_len=q_len, **gate,
          vs_bf16_chunk_min_cos=sem["min_cos_per_token"], vs_bf16_chunk_max_rel_l2=sem["max_rel_l2_per_token"])


if __name__ == "__main__":
    big = 1 if not ON_GPU else 64
    # --- chunks up to 128 tokens (M16 default)
    test_prefill("prefill_first_chunk_like", 0, 37, seed=1)  # cached_len 0: pure causal self-attention
    test_prefill("prefill_small_tail", 5 * BS * big + 3, 9, seed=2)
    test_prefill("prefill_q1", 2 * BS + 2, 1, seed=20)
    test_prefill("prefill_q2", 3 * BS + 1, 2, seed=21)
    test_prefill("prefill_tail_spans_blocks", 3 * BS * big - 7, 70, seed=3, tile_size=64)
    test_prefill("prefill_block_m32", 2 * BS * big + 11, 41, seed=4, block_m=32)
    test_prefill("prefill_one_segment", 4 * BS * big, 33, seed=5, num_segments=1)
    test_prefill("prefill_many_segments", 4 * BS * big, 33, seed=6, num_segments=64)
    test_prefill("prefill_tile128", 7 * BS * big + 1, 130, seed=7, tile_size=128, num_warps=8)
    test_prefill("prefill_q128_small_geometry", 77, 128, seed=22)
    # --- chunks above 128 tokens (default geometry). cached_len 0 and not a multiple of 64; q not a multiple of BLOCK_Q.
    test_prefill("prefill_q129_cached0", 0, 129, seed=10)
    test_prefill("prefill_q130_cached_odd", 1003 if ON_GPU else 77, 130, seed=11)
    test_prefill("prefill_q200_cached_odd", 5000 if ON_GPU else 389, 200, seed=12)
    test_prefill("prefill_q129_s64", 3 * BS + 5, 129, seed=13, num_segments=64)
    test_prefill("prefill_q129_s1_direct", 3 * BS + 5, 129, seed=14, num_segments=1)
    # --- production block size: boundary crossings, a table spanning blocks, q not a multiple of BLOCK_Q
    test_prefill("prefill_bs1536_cross_block", PROD_BS - 7, 70, seed=60, bs=PROD_BS)
    test_prefill("prefill_bs1536_aligned_block", PROD_BS, 33, seed=61, bs=PROD_BS)  # the chunk starts exactly on a block boundary
    # --- semantic change and negative variants (small cached_len, where the chunk's own keys carry the weight)
    test_intra_chunk_semantics("semantic_intra_chunk_fp4_vs_bf16", 2051 if ON_GPU else 300, 1001 if ON_GPU else 131, seed=30)
    test_negative("negative_causal_limit_plus1", 37, 131, seed=40, cached_len_shift=1)
    test_negative("negative_causal_limit_minus1", 37, 131, seed=41, cached_len_shift=-1)
    test_negative("negative_scale_x1.1", 37, 131, seed=42, scale_mul=1.1)
    test_negative("negative_query_not_rotated", 37, 131, seed=43, unrotated=True)
    if ON_GPU:  # these bs=1536 cases run the CPU interpreter for minutes each, so GPU only
        test_prefill("prefill_bs1536_two_blocks_aligned", 2 * PROD_BS, 33, seed=63, bs=PROD_BS)
        test_intra_chunk_semantics("semantic_intra_chunk_bs1536", PROD_BS - 40, 131, seed=31, bs=PROD_BS)
    if ON_GPU:  # buffers: q 12 KiB + rotated q 24 KiB + out 12 KiB per token, plus 24 KiB per token per extra segment
        test_prefill("prefill_q1001_cached_aligned", 4608, 1001, seed=50)
        test_prefill("prefill_q1001_s1_direct", 4608, 1001, seed=51, num_segments=1)
        test_prefill("prefill_q1536_cached_odd", 20003, 1536, seed=52)
        test_prefill("prefill_bs1536_q1536_cross_blocks", 4 * PROD_BS + 11, 1536, seed=57, bs=PROD_BS)
        test_prefill("prefill_q3072_s1_direct", 777, 3072, seed=56, num_segments=1)
        test_prefill("prefill_q4096_s1_direct", 0, 4096, seed=54, num_segments=1)
        test_prefill("prefill_48k_tail1001", 47616, 1001, seed=8)
        if big_enough("prefill_q3072_default_segments", 0.35):
            test_prefill("prefill_q3072_default_segments", 777, 3072, seed=53)
        if big_enough("prefill_48k_chunk4096_s1", 0.35):
            test_prefill("prefill_48k_chunk4096_s1", 45056, 4096, seed=9, num_segments=1)
        if big_enough("prefill_q4096_default_segments", 0.43):
            test_prefill("prefill_q4096_default_segments", 129, 4096, seed=55)
    print("FAILED: %s" % T.FAILED if T.FAILED else "ALL PASSED" + (" (skipped for the memory cap: %s)" % SKIPPED if SKIPPED else ""),
          flush=True)
    sys.exit(1 if T.FAILED else 0)
