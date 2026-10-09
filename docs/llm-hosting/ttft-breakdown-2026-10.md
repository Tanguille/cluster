# TTFT breakdown and quality baseline, 2026-10

Stack measured: the 2026-10-09 roll, UltraQuant 4-bit KV
([ultraquant-2026-10-07.md](ultraquant-2026-10-07.md)) on nightly `81198e97` with
`--prefix-match-unit 64` and `blocks_per_chunk` 1.

## Method

- `bench/prodshape.py --levels 1,2,4,8 --warmup --runs 1`, run inside an unrouted clone of the
  production pod with production paused (engine quiet). Long `kubectl port-forward` sessions
  stall after 30-60 min, so clients run in the pod.
- Prefixes 40K, 16K, 64K, 40K, 98K cycled across sessions; 3 turns, each appending the
  previous reply plus a fresh tail.
- Per level: means over the steady window of `request_queue_time`, `request_prefill_time` and
  `kv_offload_lookup_async_delay` (seconds per request).
- Noise: levels 1 and 8 were repeated on a second boot of the same stack (hook-cleanup arm).

## Results

| Sessions | TTFT p50 (s) | queue (s) | prefill (s) | lookup stall (s) | stall / TTFT | agg tok/s | noise (repeat) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 1.92 | 0.003 | 1.75 | 0.003 | 0% | 25.15 | TTFT 1.89, agg 25.22 |
| 2 | 3.32 | 0.23 | 2.52 | 0.23 | 7% | 41.39 | single run |
| 4 | 7.66 | 0.34 | 7.48 | 0.34 | 4% | 48.83 | single run |
| 8 | 17.45 | 9.34 | 9.53 | 0.95 | 5% | 41.16 | TTFT 17.67, agg 40.36 |

Queue time includes the async lookup wait. Cold prefill (no cache): 1,060 tok/s p50 for
16K-64K prompts, 833 tok/s for 98K (118.6 s).

Before the roll (nightly `43b4aaea3`, no `--prefix-match-unit`, `blocks_per_chunk` 4), the
same harness gave agg 17.96 / 24.1 / 26.6 / 8.4 tok/s and TTFT p50 5.05 / 11.4 / 25.9 / 96 s
at 1 / 2 / 4 / 8 sessions.

## Quality baseline

Snapshot names: `base` (pre-roll stack), `c5` (both flags on `43b4aaea3`), `n81` (both flags on
`81198e97`), `c5n` (needles on both flags), `n8c` (the rolled stack: `8cbd5d03`, mnbt 2048). Only
`bench/quality/baseline-n8c.jsonl` is committed, as the reference for the next A/B
(`qualitygate.py agree --compare n8c`).

| Check | Result | Command |
| --- | --- | --- |
| NLL mean (20 texts, 8K-32K) | 2.08628; +0.00000 vs `c5`, `c5` vs pre-roll <= 5e-5 per text | `qualitygate.py nll --name n81 --compare c5` |
| Agree determinism | pre-roll stack, second boot: 60/60; rolled vs `c5`: 60/60 | `qualitygate.py agree --name n81 --compare c5` |
| Agree, `--prefix-match-unit 64` vs pre-roll | 66.4% (27/60), zero cache hits in the run: the extra prefill split, accepted on the rows around it | `qualitygate.py agree --name pmu64 --compare base` |
| GSM8K first 150 | 146/150 pre-roll and with both flags | `qualitygate.py gsm8k` |
| Tools single / 5 concurrent | 12/12 / 12/12 | `qualitygate.py tools --name n81` |
| Needles / cached / offload reload | 3/3 at 17.5K, 52.5K, 175K / PASS / PASS | `qualitygate.py needle --name c5n` |
| Vision | 2/2 | `qualitygate.py vision --name n81` |

## Decision rule

- Lookup stall >= 30% of steady TTFT at 1-2 sessions: the roll includes the lookup fix
  (per-request `kv_load_tiers` first, hook gate as fallback).
- Queueing dominates at 4-5 sessions: check `max-num-batched-tokens` and the
  chunked-prefill interleave before kernel work.

Outcome: lookup stall is 0-7% at 1-2 sessions, so no lookup fix. Prefill dominates through 4
sessions; queueing reaches half of TTFT only at 8, so `max-num-batched-tokens` was swept on the
rolled stack (same harness, `--vram`, single runs):

| mnbt | cold prefill p50 (tok/s) | 40K / 98K cold TTFT (s) | agg tok/s 1 / 4 / 8 | min free VRAM at 8 (GiB) | quality vs 4096 |
| --- | --- | --- | --- | --- | --- |
| 2048 | 1,080-1,154 | 36.1 / 114.4 | 25.2 / 53.4 / 43.0 | 2.53 | NLL +0.00035, GSM8K 145, needles 3/3 |
| 4096 | 1,055-1,061 | 38.0 / 118.9 | 25.3 / 48.8 / 43.7 | 2.02 | reference (146) |
| 8192 | 757 | 53.3 / 160.3 | 25.1 / 51.2 / 39.5 | 1.41 (below the 1.82 floor) | not gated |

2048 rolled with nightly `8cbd5d03` (NLL +0.00006 vs `81198e97` at 2048, GSM8K 146/150,
needles 3/3, tools 12/12, vision 2/2; agg 25.1 / 41.2 / 53.4 / 41.3 tok/s at 1 / 2 / 4 / 8
sessions, min free VRAM 2.40 GiB). Smaller chunks put more of the prefix through 4-bit KV during
prefill, which is the NLL cost; the gain is cold prefill and VRAM headroom against Jellyfin.

## Prefill kernel profile and W4A16 tiles

Torch profiler on an unrouted clone (GPU kernels only), one cold 24K prompt and one 32K-cached + 3.8K-new resume:

| GPU time | Cold 24K | Resume |
| --- | --- | --- |
| W4A16 GEMM (`_triton_w4a16_skinny_fmt_kernel`) | 73% | 56% |
| Attention (flash-attn `attn_fwd`) | 15% | 32% |
| GDN, norms, other | 11% | 11% |
| UltraQuant dequant and store | 0.4% | 1.3% |

Upstream's gfx12 tiles for M > 512 were tuned on Llama-3.1-8B shapes. 256x128x64, 8 warps, 1 stage beats
upstream in all 15 cells (5 Qwen3.8 shapes x M = 768, 1280, 2048; 1.02-1.25x) and is the fastest config in
9, including all five at 2048 (`bench/downfix/triton_prefill.out`). At uneven M just above 512 it pads and
loses on some shapes (0.84x at 520, 0.80x qkv at 640; 1.08-1.23x at 1000), so the hook uses it for M >= 768
only. Rolled
2026-10-09: agreement 60/60, NLL +0.00000, GSM8K 145/150, needles 3/3, tools 12/12, vision 2/2; cold
prefill 1,111-1,118 -> 1,210-1,215 tok/s, 40K / 64K cold TTFT 36.1 / 65.1 -> 33.1 / 60.1 s, 1-session
resume TTFT 1.96 -> 1.76 s. At M = 2048 bf16 hipBLASLt runs the same shapes at 119-130 TFLOP/s against
94-108 for the tuned W4A16 kernel, so little headroom is left in the GEMM; attention is the next prefill
cost at long context.

## flash-attn compile stalls

UltraQuant's continuation prefill calls flash-attn's Triton AMD kernel, which takes `max_seqlen_q` and
`max_seqlen_k` as constexprs: every new prompt length JIT-compiles a variant (3.4-5 s, engine stalled;
519 `attn_fwd` variants compiled on 2026-10-09). fp8 never hit this, its prefill attention ran another
kernel. The hook now rounds `max_seqlen_q` up to 256 and pins `max_seqlen_k` to `max_model_len`; varlen
reads the real lengths from `cu_seqlens`, so results are unchanged (agreement 60/60, NLL +0.00000,
needles 3/3) and at most 8 variants ever compile.

`bench/compile_stall.py` (cached ~32K prefix, resumes at never-seen lengths), idle clone:

| Hook | Resume TTFT p50 | max | Stalled |
| --- | --- | --- | --- |
| exact lengths | 4.44 s | 5.24 s | 5 of 6 |
| buckets, first pass | 1.80 s | 4.77 s | 2 of 8 (compiling buckets) |
| buckets, warm | 2.06 s | 2.10 s | 0 of 6 |
