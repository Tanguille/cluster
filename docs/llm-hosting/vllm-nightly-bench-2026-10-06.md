# vLLM nightly bump bench, 2026-10-06

qwen38-27b-vllm on control-1 (R9700, gfx1201), Swift AWQ, `parallelSlots: 5`.
Bump `21d93d0d8` (image `b9a8a8f`, `0.30.1rc1.dev709`) to `bb87d227d` (image
`615b175`), 44 commits, #5602. Base layers unchanged: ROCm 7.2.3, torch
2.13.0+git733fca1, Triton `669b31a`, aiter `v0.1.24.post1` (`crane config` of both
digests).

**Verdict: no config change.** All three out-of-tree patches and the AITER backend
stay. The M<=32 Triton patch had to be fixed to boot (see Findings).

## Method

No Flux suspend and no Hermes pause: the bench ran on the live pod after the merge.
Other litellm consumers stay up, and Kritika reviews sent 1.5-3 chat requests/min
during the bench (`litellm_proxy_total_requests_metric_total`), so a gap in traffic
is not idle:

- Quiet gate before every block: `num_requests_running + waiting` 0, no request
  finished and `vcn_busy_percent` 0 on 12 consecutive 5 s reads (60 s).
- `longctx.py`, one rep per call: clean only if the ITL event count is 511.
  Contaminated reps read 991-1547 (other requests' tokens in the window). 3 clean reps.
- `concsweep.py`, `longconcsweep.py`, `greedy`: clean only if
  `vllm:request_success_total` grew by exactly the requests sent and the engine is
  idle after. Reps 1-2 of `concsweep.py` are warmups; 4 kept.

Blocks, `longctx.py` and `concsweep.py` as in [09-29](vllm-nightly-bench-2026-09-29.md):
4K band check, 6 x `concsweep.py 18000 qwen-3.8 1,2,3,4,5`, 4K decode, 64K decode
(`longctx.py 18000 qwen-3.8 50000`, 63,894 tokens, cold), `longconcsweep.py 38000
1,2,3,4,5` (48.6K prefix-cached), a temperature-0 completion, band re-check.

Baseline: the old pod was replaced mid-run (Flux applied #5602 40 min after the
merge), so only its band check (32.57 tok/s) and one `concsweep.py` rep
(31.16 / 57.17 / 75.44 / 96.59 / 118.03) exist. The comparison uses the 09-29
table. Then #5567 merged and rolled the pod again (probe change), so arm B spans
two pods of identical config, both fast band.

## Results

Warm, fast band, band check 32.60 before and 32.55 after.

| metric | 09-29 (old image) | new image | delta |
|---|---|---|---|
| M1 agg tok/s | 31.3 | 31.48 (pstdev 0.09) | +0.6% |
| M2 | 56.9 | 56.89 (0.22) | 0.0% |
| M3 | 74.3 | 74.80 (0.58) | +0.7% |
| M4 | 97.8 | 97.96 (0.80) | +0.2% |
| M5 | 117.4 | 118.69 (0.67) | +1.1% |
| 4K decode tok/s | 32.49 | 32.56 | +0.2% |
| 64K decode tok/s | 29.33 | 29.36 | +0.1% |
| 64K TTFT | 59 s | 58.5 s | -0.8% |
| 64K prompt processing | 1,083 tok/s | 1,092 tok/s | +0.8% |

Production-context sweep (no baseline): 26.95 / 46.82 / 57.04 / 69.13 / 78.00 agg
tok/s at 1-5 streams. Boot: KV pool 304,808 tokens, attention block 832, capture
2 s and 0.14 GiB, the same five kernels JIT-compile on first use as on the old pod
(`kernel_unified_attention_2d`/`3d`, `reduce_segments`,
`fused_sigmoid_gating_delta_rule_update_kernel`, `_triton_w4a16_skinny_fmt_kernel`).
Boot with a warm compile cache: Ready in about 2.4 min.

## Decisions

| Item | Decision | Basis |
|---|---|---|
| Nightly bump | Keep | Every M within 1.1% (pstdev <= 0.8), same 64K decode and TTFT, same KV pool. |
| AITER backend and all three patches | Keep | Three `[lds-gate-patch]` lines in the boot log, throughput unchanged. |
| `ROCM_SEGMENTED_ATTN` (#59132) | Not usable | Inherits `RocmAttentionBackend.supports_kv_connector() = False`; the OffloadingConnector excludes it. Dropping the fs tier halved decode. |
| ROCm 10.x | Not tested | No vLLM 10.1 image. `nightly-rocm100*` (SDK 10.0.0, TheRock base, same aiter and Triton) exists. The 10.1 notes list nothing for gfx1201 decode and not ROCm/ROCm#6347. |
| `sclk_min=3100` | Keep | See the [band doc](gfx1201-fast-slow-band-2026-09-12.md): the drift slows window filling, it does not kill. |

## Upstream range

Nothing on our decode path. #56301 pads gfx11 weight and activation strides only
when `_on_gfx1151()`. #56318 adds metrics. #56531 (GDN spec rows) is gated on
`num_speculative_tokens > 0`. #57750 (AITER query quantization) is RDNA3 only.
The rest is DeepSeek V4, Qwen4Exp, NIXL, HiSparse, Kimi, GLM, CPU and TPU.

## Findings

- #56301 added `stride_bn` and `stride_am` between `num_groups` and `group_size`
  in `_triton_w4a16_skinny_fmt_kernel`. `zz_lds_gate_impl.py` launched the kernel
  with the old positional list, so every M<=32 Triton call (down_proj at M=3-5 in
  graph capture) would raise `missing a required argument: 'stride_bn'`. A CPU stub
  reproduced it. The fix passes the strides only when
  `"stride_bn" in kernel.arg_names` and so works on both signatures. The boot on
  `615b175` is the GPU proof. Merging the bump without it would have crash-looped
  the pod.
- New metric `vllm:prompt_tokens_cached_by_source_total{source=device|host|p2p|disk|external_unspecified}`
  (#56318), counted at admission. Two hours after the roll: device 1,176,448, host
  3,328, disk 3,328. Dashboard: `vllm-kv-tiers`.
- Flux applied the two merges 40 min and 2.5 min after the merge, rolling the pod
  each time. Check the PR state before starting a bench, the merge can land under it.
- `/tmp/band` in the pod after 39 min held `1 1 1 1`: the VCN-aware probe from #5567
  (merged 21:19Z) counts fast samples and has not fired.
