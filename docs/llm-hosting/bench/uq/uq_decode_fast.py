"""RDNA4 split-KV decode kernel for the UltraQuant 4-bit KV cache (vllm#57057).

Drop-in for the 3D path of ultraquant_unified_attention at max_query_len == 1,
no sinks, no sliding window. Writes segm_output/segm_max/segm_expsum in the
layout upstream reduce_segments reads and reuses it unchanged.

Per KV byte: one vector-loaded u8, two nibbles turned into fp16 by bit
placement (no LUT, no fp32 decode), one fp16 multiply by the group scale, then
fp16 WMMA with fp32 accumulators. Even and odd head dims are contracted as two
K=D/2 dots, so packed bytes never need de-interleaving:
    S     = q[0::2] . K_lo^T + q[1::2] . K_hi^T
    out[0::2] = P . V_lo,  out[1::2] = P . V_hi
Slot layout (format.py): [K codes D/2 | K scales D/32 | V codes D/2 | V scales D/32],
element 2c = low nibble of byte c, element 2c+1 = high nibble, group g = bytes
16g..16g+15 (K in Hadamard-rotated order, which q_rot shares).
"""

from __future__ import annotations

import torch
import triton
import triton.language as tl

# fp16 multiplier = 2^(e - 127 + SHIFT); decoded nibbles carry 2^-14, so tiles
# hold value * 2^(SHIFT - 14) and 2^(14 - SHIFT) is folded into scale and output.
# Group exponent y = e - 127 is exact for y in [-9 - SHIFT, 15 - SHIFT], fp16-normal
# (no denormal WMMA inputs) for y in [1 - SHIFT, 15 - SHIFT]. SHIFT=7: exact [-16, 8],
# normal [-6, 8]; y > 8 saturates (group absmax > ~2300), y < -21 flushes to 0.
DEFAULT_SHIFT = 7


@triton.jit
def _fp4x2_to_f16(b):
    """Packed FP4 E2M1 byte tile -> (low nibble, high nibble) as fp16 = fp4 * 2^-14.

    E2M1 bits s|e1 e0|m land on fp16 bit 15 | exponent bits 11..10 | mantissa bit 9;
    e=0 lands in the fp16 subnormal range, which reproduces E2M1's 0.5 exactly.
    """
    x = b.to(tl.int32)
    lo = ((x & 0x07) << 9) | ((x & 0x08) << 12)
    hi = ((x & 0x70) << 5) | ((x & 0x80) << 8)
    return (
        lo.to(tl.int16).to(tl.float16, bitcast=True),
        hi.to(tl.int16).to(tl.float16, bitcast=True),
    )


@triton.jit
def _ue8m0_to_f16(e, UE8M0_BIAS_C: tl.constexpr, SHIFT: tl.constexpr):
    """UE8M0 byte tile -> fp16 2^(e - bias + SHIFT), as the fp16 exponent field.

    Clamping the field to [0, 30] flushes tiny groups to +0 and saturates huge ones
    at 2^15 instead of inf. The zero sentinel e=0 lands below 0 for any SHIFT < 112,
    so it needs no separate select.
    """
    t = e.to(tl.int32) + (SHIFT - UE8M0_BIAS_C + 15)
    t = tl.minimum(tl.maximum(t, 0), 30)
    return (t << 10).to(tl.int16).to(tl.float16, bitcast=True)


@triton.jit
def _dequant_pair(
    KV_ptr,
    rows,  # [TILE] int64 slot byte offsets of this operand's codes
    scale_rows,  # [TILE] int64 slot byte offsets of this operand's scales
    tmask,
    TILE_SIZE: tl.constexpr,
    HALF_D: tl.constexpr,
    N_GROUPS_C: tl.constexpr,
    UE8M0_BIAS_C: tl.constexpr,
    SHIFT: tl.constexpr,
):
    """[TILE, D/2] even-dim and odd-dim fp16 tiles, group scales applied."""
    offs_c = tl.arange(0, HALF_D)
    offs_g = tl.arange(0, N_GROUPS_C)
    codes = tl.load(KV_ptr + rows[:, None] + offs_c[None, :], mask=tmask[:, None], other=0)
    sbytes = tl.load(KV_ptr + scale_rows[:, None] + offs_g[None, :], mask=tmask[:, None], other=0)
    mult = _ue8m0_to_f16(sbytes, UE8M0_BIAS_C, SHIFT)[:, :, None]
    lo, hi = _fp4x2_to_f16(codes)
    BYTES_PER_GROUP: tl.constexpr = HALF_D // N_GROUPS_C
    lo = tl.reshape(tl.reshape(lo, [TILE_SIZE, N_GROUPS_C, BYTES_PER_GROUP]) * mult, [TILE_SIZE, HALF_D])
    hi = tl.reshape(tl.reshape(hi, [TILE_SIZE, N_GROUPS_C, BYTES_PER_GROUP]) * mult, [TILE_SIZE, HALF_D])
    return lo, hi


@triton.jit
def _attend(
    query_ptr,
    tok,  # query token of each row ([BLOCK_M], or a scalar shared by all rows)
    head,  # [BLOCK_M] query head of each row
    row_ok,  # [BLOCK_M]
    KV_ptr,
    bt_row,  # this request's block-table row
    head_off,  # KV head byte offset in a slot row
    tile_lo,
    tile_hi,
    last,  # KV tokens past this are never loaded
    limit,  # causal limit per row, broadcastable to [TILE, BLOCK_M], <= last
    qk_scale,
    query_stride_0: tl.int64,
    query_stride_1: tl.int64,
    stride_cache_block: tl.int64,
    stride_cache_pos: tl.int64,
    BLOCK_SIZE: tl.constexpr,
    TILE_SIZE: tl.constexpr,
    HEAD_SIZE: tl.constexpr,
    BLOCK_M: tl.constexpr,
    K_SCALES_OFFSET: tl.constexpr,
    V_CODES_OFFSET: tl.constexpr,
    V_SCALES_OFFSET: tl.constexpr,
    N_GROUPS_C: tl.constexpr,
    UE8M0_BIAS_C: tl.constexpr,
    SHIFT: tl.constexpr,
):
    """Online softmax of BLOCK_M query rows over KV tiles [tile_lo, tile_hi) of one KV head.

    Returns (M, L, acc_even, acc_odd); acc is in tile units, out_scale undoes them.
    """
    HALF_D: tl.constexpr = HEAD_SIZE // 2
    offs_c = tl.arange(0, HALF_D)
    offs_t = tl.arange(0, TILE_SIZE)

    # Q^T [D/2, BLOCK_M]: scores are computed transposed (K tile as the WMMA A operand) because
    # Triton's AMD chained-dot heuristic puts every warp on the M axis of a dot that feeds
    # another dot. With M = 16 query rows that made each warp decode the whole K tile;
    # with M = TILE the warps split the tokens instead. Masked rows load zeros and only
    # produce scores that are never stored.
    q_cols = query_ptr + (tok.to(tl.int64) * query_stride_0 + head * query_stride_1)[None, :]
    qT_even = tl.load(q_cols + 2 * offs_c[:, None], mask=row_ok[None, :], other=0.0).to(tl.float16)
    qT_odd = tl.load(q_cols + 2 * offs_c[:, None] + 1, mask=row_ok[None, :], other=0.0).to(tl.float16)

    M = tl.full([BLOCK_M], float("-inf"), dtype=tl.float32)
    L = tl.full([BLOCK_M], 1.0, dtype=tl.float32)
    acc_even = tl.zeros([BLOCK_M, HALF_D], dtype=tl.float32)
    acc_odd = tl.zeros([BLOCK_M, HALF_D], dtype=tl.float32)

    for j in range(tile_lo, tile_hi):
        seq_offset = j * TILE_SIZE + offs_t
        tmask = seq_offset <= last
        blk = tl.load(bt_row + seq_offset // BLOCK_SIZE, mask=tmask, other=0).to(tl.int64)
        slot = blk * stride_cache_block + (seq_offset % BLOCK_SIZE).to(tl.int64) * stride_cache_pos + head_off

        k_lo, k_hi = _dequant_pair(
            KV_ptr, slot, slot + K_SCALES_OFFSET, tmask,
            TILE_SIZE, HALF_D, N_GROUPS_C, UE8M0_BIAS_C, SHIFT,
        )
        S_T = tl.dot(k_hi, qT_odd, tl.dot(k_lo, qT_even))  # [TILE, BLOCK_M]
        S_T = tl.where(seq_offset[:, None] <= limit, S_T * qk_scale, float("-inf"))
        m_j = tl.maximum(M, tl.max(S_T, axis=0))
        # Rows whose causal limit is below this tile see only -inf; pin their max to 0 so
        # exp() gives 0 instead of NaN (same guard as upstream unified attention).
        m_safe = tl.where(m_j > float("-inf"), m_j, 0.0)
        P_T = tl.exp(S_T - m_safe[None, :])
        alpha = tl.exp(M - m_safe)
        L = L * alpha + tl.sum(P_T, axis=0)
        M = m_j

        v_lo, v_hi = _dequant_pair(
            KV_ptr, slot + V_CODES_OFFSET, slot + V_SCALES_OFFSET, tmask,
            TILE_SIZE, HALF_D, N_GROUPS_C, UE8M0_BIAS_C, SHIFT,
        )
        P16 = tl.trans(P_T.to(tl.float16))
        acc_even = tl.dot(P16, v_lo, acc_even * alpha[:, None])
        acc_odd = tl.dot(P16, v_hi, acc_odd * alpha[:, None])
    return M, L, acc_even, acc_odd


@triton.jit
def _store_partials(
    segm_output_ptr, segm_max_ptr, segm_expsum_ptr, tok, head, segm_idx, M, L, acc_even, acc_odd, out_scale, row_ok,
    num_query_heads: tl.constexpr,
    NUM_SEGMENTS_PER_SEQ: tl.constexpr,
    HEAD_SIZE: tl.constexpr,
):
    """Partials for reduce_segments: [tok, head, segm, D] with even/odd dims re-interleaved."""
    offs_c = tl.arange(0, HEAD_SIZE // 2)
    segm_off = tok.to(tl.int64) * (num_query_heads * NUM_SEGMENTS_PER_SEQ) + head * NUM_SEGMENTS_PER_SEQ + segm_idx
    out = segm_output_ptr + segm_off[:, None] * HEAD_SIZE + 2 * offs_c[None, :]
    tl.store(out, acc_even * out_scale, mask=row_ok[:, None])
    tl.store(out + 1, acc_odd * out_scale, mask=row_ok[:, None])
    tl.store(segm_max_ptr + segm_off, M, mask=row_ok)
    tl.store(segm_expsum_ptr + segm_off, L, mask=row_ok)


@triton.jit
def uq_decode_fast_kernel(
    segm_output_ptr,  # [num_tokens, Hq, NUM_SEGMENTS, D] fp32
    segm_max_ptr,  # [num_tokens, Hq, NUM_SEGMENTS] fp32
    segm_expsum_ptr,  # [num_tokens, Hq, NUM_SEGMENTS] fp32
    query_ptr,  # [num_tokens, Hq, D] fp32, Hadamard-rotated
    KV_ptr,  # flat uint8 cache
    block_tables_ptr,
    seq_lens_ptr,
    query_start_len_ptr,
    qk_scale,  # softmax scale * 2^(14 - SHIFT)
    out_scale,  # 2^(14 - SHIFT)
    num_query_heads: tl.constexpr,
    num_queries_per_kv: tl.constexpr,
    block_table_stride: tl.int64,
    query_stride_0: tl.int64,
    query_stride_1: tl.int64,
    stride_cache_block: tl.int64,
    stride_cache_pos: tl.int64,
    stride_cache_head: tl.int64,
    BLOCK_SIZE: tl.constexpr,
    TILE_SIZE: tl.constexpr,
    HEAD_SIZE: tl.constexpr,
    BLOCK_M: tl.constexpr,
    NUM_SEGMENTS_PER_SEQ: tl.constexpr,
    K_SCALES_OFFSET: tl.constexpr,
    V_CODES_OFFSET: tl.constexpr,
    V_SCALES_OFFSET: tl.constexpr,
    N_GROUPS_C: tl.constexpr,
    UE8M0_BIAS_C: tl.constexpr,
    SHIFT: tl.constexpr,
):
    seq_idx = tl.program_id(0)
    kv_head_idx = tl.program_id(1)
    segm_idx = tl.program_id(2)

    q_tok = tl.load(query_start_len_ptr + seq_idx)
    if tl.load(query_start_len_ptr + seq_idx + 1) - q_tok < 1:
        return  # padded request with no query token
    # Same segment split as the stock kernel and reduce_segments (max_query_len == 1,
    # so the causal limit is seq_len itself).
    seq_len = tl.load(seq_lens_ptr + seq_idx)
    tiles_per_segment = tl.cdiv(seq_len, NUM_SEGMENTS_PER_SEQ * TILE_SIZE)
    tile_lo = segm_idx * tiles_per_segment
    if tile_lo * TILE_SIZE >= seq_len:
        return
    tile_hi = tl.minimum(tile_lo + tiles_per_segment, tl.cdiv(seq_len, TILE_SIZE))

    offs_m = tl.arange(0, BLOCK_M)
    head = kv_head_idx * num_queries_per_kv + offs_m
    head_mask = offs_m < num_queries_per_kv
    M, L, acc_even, acc_odd = _attend(
        query_ptr, q_tok, head, head_mask, KV_ptr,
        block_tables_ptr + seq_idx.to(tl.int64) * block_table_stride, kv_head_idx.to(tl.int64) * stride_cache_head,
        tile_lo, tile_hi, seq_len - 1, seq_len - 1, qk_scale,
        query_stride_0, query_stride_1, stride_cache_block, stride_cache_pos,
        BLOCK_SIZE, TILE_SIZE, HEAD_SIZE, BLOCK_M,
        K_SCALES_OFFSET, V_CODES_OFFSET, V_SCALES_OFFSET, N_GROUPS_C, UE8M0_BIAS_C, SHIFT,
    )
    _store_partials(
        segm_output_ptr, segm_max_ptr, segm_expsum_ptr, q_tok, head, segm_idx, M, L, acc_even, acc_odd, out_scale,
        head_mask, num_query_heads, NUM_SEGMENTS_PER_SEQ, HEAD_SIZE,
    )


@triton.jit
def uq_prefill_fast_kernel(
    segm_output_ptr,  # [q_len, Hq, NUM_SEGMENTS, D] fp32
    segm_max_ptr,  # [q_len, Hq, NUM_SEGMENTS] fp32
    segm_expsum_ptr,  # [q_len, Hq, NUM_SEGMENTS] fp32
    output_ptr,  # [q_len, Hq, D], written directly when NUM_SEGMENTS_PER_SEQ == 1
    query_ptr,  # [q_len, Hq, D] fp32, Hadamard-rotated
    KV_ptr,  # flat uint8 cache
    block_table_ptr,  # [max_num_blocks] int32, this request's row
    cached_len,  # int32, query token t sits at absolute position cached_len + t
    q_len,
    qk_scale,  # softmax scale * 2^(14 - SHIFT)
    out_scale,  # 2^(14 - SHIFT)
    num_query_heads: tl.constexpr,
    num_queries_per_kv: tl.constexpr,
    query_stride_0: tl.int64,
    query_stride_1: tl.int64,
    output_stride_0: tl.int64,
    output_stride_1: tl.int64,
    stride_cache_block: tl.int64,
    stride_cache_pos: tl.int64,
    stride_cache_head: tl.int64,
    BLOCK_SIZE: tl.constexpr,
    TILE_SIZE: tl.constexpr,
    HEAD_SIZE: tl.constexpr,
    BLOCK_M: tl.constexpr,
    BLOCK_Q: tl.constexpr,
    NUM_SEGMENTS_PER_SEQ: tl.constexpr,
    K_SCALES_OFFSET: tl.constexpr,
    V_CODES_OFFSET: tl.constexpr,
    V_SCALES_OFFSET: tl.constexpr,
    N_GROUPS_C: tl.constexpr,
    UE8M0_BIAS_C: tl.constexpr,
    SHIFT: tl.constexpr,
):
    """uq_decode_fast_kernel with BLOCK_Q query tokens x GQA heads as the BLOCK_M rows.

    Each K/V tile is decoded once per BLOCK_Q tokens instead of once per token, and the
    current chunk is read back from the cache (do_kv_cache_update stores it before
    attention), so the prefix is never materialised in bf16.

    Query blocks run last-first: a block's work grows with its causal limit, so the heavy
    blocks launch first and the light ones fill the tail of the grid.
    """
    qb = tl.num_programs(0) - 1 - tl.program_id(0)
    kv_head_idx = tl.program_id(1)
    segm_idx = tl.program_id(2)

    seq_len = cached_len + q_len
    # Segments split [0, seq_len) exactly as reduce_segments expects for every token.
    tiles_per_segment = tl.cdiv(seq_len, NUM_SEGMENTS_PER_SEQ * TILE_SIZE)
    tile_lo = segm_idx * tiles_per_segment
    if tile_lo * TILE_SIZE >= seq_len:
        return

    offs_m = tl.arange(0, BLOCK_M)
    tok = qb * BLOCK_Q + offs_m // num_queries_per_kv
    head = kv_head_idx * num_queries_per_kv + offs_m % num_queries_per_kv
    row_ok = (offs_m < BLOCK_Q * num_queries_per_kv) & (tok < q_len)
    last = cached_len + tl.minimum(qb * BLOCK_Q + BLOCK_Q, q_len) - 1
    # Segments past this block's causal limit run no tiles and store M = -inf, acc = 0,
    # which reduce_segments weights by exp(-inf) = 0.
    tile_hi = tl.minimum(tile_lo + tiles_per_segment, tl.cdiv(last + 1, TILE_SIZE))
    M, L, acc_even, acc_odd = _attend(
        query_ptr, tok, head, row_ok, KV_ptr, block_table_ptr, kv_head_idx.to(tl.int64) * stride_cache_head,
        tile_lo, tile_hi, last, (cached_len + tok)[None, :], qk_scale,
        query_stride_0, query_stride_1, stride_cache_block, stride_cache_pos,
        BLOCK_SIZE, TILE_SIZE, HEAD_SIZE, BLOCK_M,
        K_SCALES_OFFSET, V_CODES_OFFSET, V_SCALES_OFFSET, N_GROUPS_C, UE8M0_BIAS_C, SHIFT,
    )
    if NUM_SEGMENTS_PER_SEQ == 1:
        # One segment is the whole softmax: normalise here and skip the partials and reduce_segments.
        # Every valid row has L > 0 (it attends at least its own, already stored, token).
        offs_c = tl.arange(0, HEAD_SIZE // 2)
        out = output_ptr + tok.to(tl.int64)[:, None] * output_stride_0 + head[:, None] * output_stride_1 + 2 * offs_c[None, :]
        scale = out_scale / L
        tl.store(out, acc_even * scale[:, None], mask=row_ok[:, None])
        tl.store(out + 1, acc_odd * scale[:, None], mask=row_ok[:, None])
    else:
        _store_partials(
            segm_output_ptr, segm_max_ptr, segm_expsum_ptr, tok, head, segm_idx, M, L, acc_even, acc_odd, out_scale,
            row_ok, num_query_heads, NUM_SEGMENTS_PER_SEQ, HEAD_SIZE,
        )


LAST_KERNEL = None  # compiled-kernel handle of the latest launch; the bench reads n_spills / n_regs from it


def _launch(kernel, grid_0, num_segments, query, kv_cache, scale, PiT, output, seq_lens, query_start_loc,
            tile_size, num_warps, num_stages, shift, direct_out=False, **kernel_args):
    """Host side both kernels share: rotate q, run kernel over (grid_0, Hk, segments), then
    upstream reduce_segments into output. With direct_out and one segment the kernel writes
    output itself and neither the partial buffers nor reduce_segments exist."""
    global LAST_KERNEL
    from vllm.v1.attention.ops.triton_unified_attention import reduce_segments
    from vllm.v1.attention.ops.ultraquant.format import (
        UE8M0_BIAS,
        get_group_size,
        k_scales_offset,
        n_groups,
        slot_size,
        v_codes_offset,
        v_scales_offset,
    )
    from vllm.v1.attention.ops.ultraquant.triton_store import _get_hadamard, _kv_cache_flat

    num_tokens, Hq, D = query.shape
    _, block_size, Hk, padded_slot = kv_cache.shape
    gs = get_group_size()
    if D & (D - 1) or D < 2 * gs or Hq % Hk or padded_slot < slot_size(D, gs):
        raise ValueError(f"UltraQuant fast path: unsupported D={D} Hq={Hq} Hk={Hk} slot={padded_slot}")

    if PiT is None:
        PiT = _get_hadamard(D, query.device)  # symmetric, same matrix the store applied
    q_rot = (query.float() @ PiT.float()).contiguous()
    if output is None:
        output = torch.empty_like(query)

    # reduce_segments uses tl.arange(NUM_SEGMENTS), so the count must stay a power of two.
    num_segments = 1 << (num_segments.bit_length() - 1)
    direct = direct_out and num_segments == 1
    if direct_out:  # the prefill kernel takes output; it writes it only when there is one segment
        kernel_args.update(output_ptr=output, output_stride_0=output.stride(0), output_stride_1=output.stride(1))
    if direct:
        segm_output = segm_max = segm_expsum = output  # dead pointers: the kernel never touches them
    else:
        segm_output = torch.empty((num_tokens, Hq, num_segments, D), dtype=torch.float32, device=query.device)
        segm_max = torch.empty((num_tokens, Hq, num_segments), dtype=torch.float32, device=query.device)
        segm_expsum = torch.empty_like(segm_max)
    fold = 2.0 ** (14 - shift)

    LAST_KERNEL = kernel[(grid_0, Hk, num_segments)](
        segm_output_ptr=segm_output,
        segm_max_ptr=segm_max,
        segm_expsum_ptr=segm_expsum,
        query_ptr=q_rot,
        KV_ptr=_kv_cache_flat(kv_cache),
        qk_scale=float(scale) * fold,
        out_scale=fold,
        num_query_heads=Hq,
        num_queries_per_kv=Hq // Hk,
        query_stride_0=q_rot.stride(0),
        query_stride_1=q_rot.stride(1),
        stride_cache_block=kv_cache.stride(0),
        stride_cache_pos=kv_cache.stride(1),
        stride_cache_head=kv_cache.stride(2),
        BLOCK_SIZE=block_size,
        TILE_SIZE=tile_size,
        HEAD_SIZE=D,
        NUM_SEGMENTS_PER_SEQ=num_segments,
        K_SCALES_OFFSET=k_scales_offset(D, gs),
        V_CODES_OFFSET=v_codes_offset(D, gs),
        V_SCALES_OFFSET=v_scales_offset(D, gs),
        N_GROUPS_C=n_groups(D, gs),
        UE8M0_BIAS_C=UE8M0_BIAS,
        SHIFT=shift,
        num_warps=num_warps,
        num_stages=num_stages,
        **kernel_args,
    )
    if direct:
        return output
    reduce_segments[(num_tokens, Hq)](
        output_ptr=output,
        segm_output_ptr=segm_output,
        segm_max_ptr=segm_max,
        segm_expsum_ptr=segm_expsum,
        seq_lens_ptr=seq_lens,
        num_seqs=query_start_loc.shape[0] - 1,
        num_query_heads=Hq,
        out_scale_inv=1.0,
        output_stride_0=output.stride(0),
        output_stride_1=output.stride(1),
        block_table_stride=0,  # unused by reduce_segments
        TILE_SIZE=tile_size,
        HEAD_SIZE=D,
        HEAD_SIZE_PADDED=D,
        query_start_len_ptr=query_start_loc,
        BLOCK_Q=1,
        NUM_SEGMENTS_PER_SEQ=num_segments,
        USE_FP8=False,
    )
    return output


# Continuation geometry: BLOCK_M rows = BLOCK_M // GQA tokens x GQA heads, K/V tile, warps.
# The transposed score dot is [TILE, BLOCK_M] in 16x16 WMMA tiles and each warp owns whole tiles, so
# (TILE / 16) * (BLOCK_M / 16) >= warps; M16 / tile 64 / 4 warps is the swept winner (every M >= 32 spilled and lost).
GEOMETRY = dict(block_m=16, tile_size=64, num_warps=4)
PROGRAMS_PER_CU = 32  # segment count targets this many programs per CU (swept, 2026-10-07 retune)


def uq_prefill_fast(
    query: torch.Tensor,  # [q_len, Hq, D] bf16/fp16, unrotated
    kv_cache: torch.Tensor,  # [num_blocks, block_size, Hk, padded_slot] uint8, chunk already stored
    block_table: torch.Tensor,  # [1, max_num_blocks] int32
    cached_len: int,
    scale: float,
    PiT: torch.Tensor | None = None,
    block_m: int = GEOMETRY["block_m"],
    tile_size: int = GEOMETRY["tile_size"],
    num_warps: int = GEOMETRY["num_warps"],
    num_stages: int = 1,
    num_segments: int | None = None,
) -> torch.Tensor:
    """Causal attention of one request's continuation chunk over its whole UltraQuant cache.

    Replaces upstream's per-token synthetic decode (q <= 128) and its bf16 dequant + flash-attn
    (q > 128). Geometry defaults to GEOMETRY; any argument overrides it.
    One segment writes the output directly. No sinks, no sliding window.
    """
    q_len, Hq, _ = query.shape
    Hk = kv_cache.shape[2]
    gqa = Hq // Hk
    if block_m < gqa:
        raise ValueError(f"uq_prefill_fast: block_m {block_m} < GQA group {gqa}")
    block_q = block_m // gqa
    num_qb = triton.cdiv(q_len, block_q)
    seq_len = cached_len + q_len
    if num_segments is None:
        # ~32 programs per CU: 64 segments at 1-16 tokens, 16 at 64, 8 at 128. The old ~4 per CU
        # left 64-128 token chunks on 1-2 segments, 10-15% slower than 8 at 16-98K contexts.
        cus = torch.cuda.get_device_properties(query.device).multi_processor_count if query.is_cuda else 64
        num_segments = triton.cdiv(PROGRAMS_PER_CU * cus, num_qb * Hk)
    dev = query.device
    return _launch(
        uq_prefill_fast_kernel, num_qb, min(max(1, num_segments), 64, triton.cdiv(seq_len, tile_size)),
        query, kv_cache, scale, PiT, torch.empty_like(query),
        seq_lens=torch.full((1,), seq_len, dtype=torch.int32, device=dev),
        query_start_loc=torch.arange(0, q_len + 1, q_len, dtype=torch.int32, device=dev),  # [0, q_len], no host copy
        tile_size=tile_size, num_warps=num_warps, num_stages=num_stages, shift=DEFAULT_SHIFT, direct_out=True,
        block_table_ptr=block_table, cached_len=cached_len, q_len=q_len, BLOCK_M=block_m, BLOCK_Q=block_q,
    )


def uq_decode_fast(
    query: torch.Tensor,  # [num_tokens, Hq, D] bf16/fp16, unrotated
    kv_cache: torch.Tensor,  # [num_blocks, block_size, Hk, padded_slot] uint8
    block_table: torch.Tensor,  # [num_seqs, max_num_blocks] int32 (row stride may be 0)
    seq_lens: torch.Tensor,  # [num_seqs] int32
    query_start_loc: torch.Tensor,  # [num_seqs + 1] int32, one token per sequence
    scale: float,
    PiT: torch.Tensor | None = None,
    output: torch.Tensor | None = None,
    max_seq_len: int | None = None,
    num_kv_splits: int = 64,
    tile_size: int = 64,
    num_warps: int = 4,
    num_stages: int = 1,
    shift: int = DEFAULT_SHIFT,
) -> torch.Tensor:
    """ultraquant_unified_attention for max_query_len == 1, no sinks, no sliding window.

    Keep tile_size >= 16 * num_warps: each warp owns 16 tokens of the score WMMA, and
    surplus warps decode the same K tokens again (offline gfx1201 compile, VALU per
    KV element in the tile loop: t128/w8 6.4, t64/w4 6.5 with 8 spills, t32/w8 15.2).
    Defaults: best of 12 geometries weighted by 30d production traffic (1/2/4 seqs x
    16K/40K/98K, bench_uq_workload.py): 5.37 ms per 16-layer step vs 7.33 at s32/t32/w4,
    and within 2% of the per-concurrency best.
    """
    Hq, Hk = query.shape[1], kv_cache.shape[2]
    if max_seq_len is None:
        max_seq_len = block_table.shape[1] * kv_cache.shape[1]
    return _launch(
        uq_decode_fast_kernel, query_start_loc.shape[0] - 1,
        min(max(1, num_kv_splits), triton.cdiv(max(1, max_seq_len), tile_size)),
        query, kv_cache, scale, PiT, output, seq_lens, query_start_loc,
        tile_size=tile_size, num_warps=num_warps, num_stages=num_stages, shift=shift,
        block_tables_ptr=block_table, seq_lens_ptr=seq_lens, query_start_len_ptr=query_start_loc,
        block_table_stride=block_table.stride(0), BLOCK_M=max(16, triton.next_power_of_2(Hq // Hk)),
    )
