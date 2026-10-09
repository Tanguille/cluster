# UltraQuant KV, 262K context and GPU findings, 2026-10-07

qwen38-27b-vllm on control-1 (R9700, gfx1201, 32 GB shared with Jellyfin), Swift
1.5 W4A16 AWQ, nightly `43b4aaea3` (image `c36857e9`, `0.31.1rc1.dev23`).
Live-applied with Flux `llmkube-models` suspended; branch
`feat/vllm-ultraquant-shared-buffer` syncs git to the live spec.

**Status:** UltraQuant 4-bit KV plus our decode kernel has been live since 17:26Z,
8 slots since 17:48Z, and `maxModelLen` 262144 plus the workload retune since 19:33Z.
**Verdict:** per-stream decode looks at least on par with fp8: 32.00 tok/s at 48.6K vs
fp8's 29.36 at 64K. The context lengths differ, so this is not a matched comparison;
the matched-length A/B is the 48K table below. Throughput at production shape is still
11-34% below fp8 after the retune:

| 48K cached prompt | fp8 | UltraQuant (retuned) |
| --- | ---: | ---: |
| 1 stream, aggregate tok/s | 26.95 | 24.07 |
| 2 streams | 46.82 | 37.42 |
| 5 streams | 78.00 | 51.62 |
| TTFT median, 1 / 5 streams | 0.54 / 2.13 s | 1.27 / 6.35 s |

Computed tokens per request have a p50 of 1,254, so most requests take the
cached-prefix continuation path. **The owner keeps UltraQuant (quality, headroom)**; the
2026-10-09 fix is in [ttft-breakdown-2026-10.md](ttft-breakdown-2026-10.md).

## Production-shape baseline (`bench/prodshape.py`, 2026-10-07 20:07-20:42Z)

Setup:

- Independent sessions with their own prefixes (16K/40K/64K/98K tokens, cycled), 3
  steady turns per session.
- Each turn adds a 1,254-token random tail and generates 235 tokens.
- Engine quiet at start; no level was contaminated.
- Free VRAM sampled every 3 s through the `uq-bench` pod (`bench/vramloop.sh`, 306
  samples): minimum 2.347 GiB.

| Sessions | Agg tok/s | Decode/stream | TTFT p50 / p90 | ITL p50 | Server hit | Recomputed tokens |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 17.96 | 31.5 | 5.05 / 8.99 s | 31.6 ms | 0.956 | 5,669 |
| 2 | 24.10 | 29.4 | 11.38 / 12.59 s | 34.1 ms | 0.935 | n/a |
| 4 | 26.63 | 22.6 | 25.93 / 28.39 s | 44.5 ms | 0.955 | 23,220 |
| 5 | 28.93 | 19.9 | 23.14 / 32.92 s | 50.2 ms | 0.964 | 29,506 |
| 8 | 6.69 | 1.5 | 116.8 / 213.7 s | 67.0 ms | 0.317 | 656,147 |

- **Cold prefill is 950-970 tok/s at 16-40K and falls to about 880-910 at 64K and 808
  at 98K.** Measured: 16,101 tokens in 16.9 s, 40,311 in 41.5 s, 64,427 in 70.9 s,
  98,637 in 122.1 s. Up to 40K, prefill is bound by the GEMMs and GDN. Attention costs
  about 15% more by 98K.
- **8 sessions collapse on KV capacity.** The 8 sessions cycle 40K/16K/64K/40K/98K
  prefixes (378K) and add about 36K over three turns (8 x 3 x (1,254 tail + 235
  reply)) = about 414K. That exceeds the pool less its 5% watermark (430,982 x 0.95 =
  409K), so sessions evict each other and 656K tokens are recomputed. Fixed on 2026-10-09
  by `blocks_per_chunk` 1 and `--prefix-match-unit 64`
  ([ttft-breakdown-2026-10.md](ttft-breakdown-2026-10.md)). The offload tier served 270K (`external_kv_transfer`). fp8's
  304,808-token pool would hit this wall at about 5-6 sessions.
- **The offload lookup stall did not fire during the run.** The
  `kv_offload_lookup_async_delay_seconds` count stayed at 45. The 5-11 s TTFT at 1-2
  sessions is therefore prefill and queueing; ttft-breakdown-2026-10.md splits it.

## Live config vs the fp8 baseline

| Setting | fp8 (main until this branch) | Live |
| --- | --- | --- |
| KV dtype | `kvCacheDtype: fp8_e4m3` | `kvCacheDtype: auto`, `kvCacheCustomDtype: ultraquant_4bit` |
| `--kv-cache-memory` | 10200547328 (9.5 GiB) | 7784628224 (7.25 GiB) |
| KV pool (boot log) | 304,808 tokens, 33,465 B/token | 430,982 tokens, 18,091 B/token |
| `maxModelLen` (pool/cap) | 246944 (1.23x) | 262144 (1.64x) |
| `parallelSlots`, graph capture sizes | 5, `[1..5]` | 8, `[1..8]` |
| env | none | `UQ_FAST=1`, `FLASH_ATTENTION_TRITON_AMD_ENABLE=TRUE` |
| `lds-gate-patch` ConfigMap | pth + impl | plus `uq_decode_fast.py` |

Why each one:

- `kvCacheCustomDtype`: the CRD enum for `kvCacheDtype` only has auto/fp8_e5m2/fp8_e4m3.
- `FLASH_ATTENTION_TRITON_AMD_ENABLE`: UltraQuant continuation prefill calls flash-attn.
  The CK build segfaults on gfx1201 in `BlockFmhaPipelineQRKSVSAsync` (found with
  `PYTHONFAULTHANDLER`). The Triton build works. Anything else that reaches CK
  flash-attn (the vision tower, for example) is a risk; a vision check was not run.
- `--kv-cache-memory 7.25 GiB`: the flag skips profiling, so the headroom is manual.
  - UltraQuant needs about 2.2 GiB more transient VRAM than fp8: dequant scratch,
    one shared continuation buffer and transients. That is an estimate.
  - Measured: 9.5 GiB OOMed on the first request, 8 GiB booted at 474,664 tokens, and
    7.25 GiB passed every gate.
  - Free VRAM at the stress peak is unmeasured (the sampler read 0.00; the gate was
    2.3 GiB). A bench pod saw 1.86 GiB free on the node during production.
- Pool is 1.41x fp8, not 2x: the attention KV is 17,408 B/token (16 layers x 4 KV
  heads x 272 B), and the fixed UltraQuant overhead eats the rest.
- `maxModelLen` 262144 is the trained max (`max_position_embeddings`). fp8 stayed at
  246944 because decode roughly halved below about 1.17x pool/cap (bisected
  2026-08-19, cause unexplained). Clients still cap lower: Hermes 238752, litellm
  246944.
- 8 slots: the user asked to test more than 5 streams; capture sizes must cover every
  slot (see Results for the M=6 dip).
- Manager block size is 1536 tokens under UltraQuant (832 under fp8: "Setting
  attention block size to 1536 tokens ... >= mamba page size"). The offload chunk
  becomes 4 x 1536 = 6,144 tokens, and the average recomputed tail roughly doubles
  (768 vs 416 tokens, estimate).

## Results

fp8 baseline: 2026-10-06 ~21:43Z on the same nightly, live pod. UltraQuant runs:
2026-10-07, `quick.sh`, `concsweep.py`, `longconcsweep.py 38000` (48,617-token cached
prompt). All are aggregate tok/s unless noted.

| Config | 4K decode | 64K decode | 64K cold TTFT | short ctx agg, 1/2/3/4/5 | 48K cached agg, 1/5 | 48K cached TTFT median, 1/5 |
| --- | --- | --- | --- | --- | --- | --- |
| fp8 | 32.56 | 29.36 | 58.5 s | 31.4/57.3/75.5/99.1/119.9 | 26.95/78.00 | 0.54/2.13 s |
| UltraQuant, upstream kernel | 30.54 | 18.11 | 66.8 s | 31.4/57.1/74.6/96.3/115.7 | not run | not run |
| UltraQuant, upstream tuned geometry | contaminated | 23.57 | 67.0 s | 30.0/55.1/72.7/94.4/113.1 | not run | not run |
| UltraQuant + `UQ_FAST=1` (pre-retune) | 33.04 | 29.60 | 67.1-67.3 s | 31.7/58.2/74.0/98.4/118.6 | 23.49/49.57 | 1.29/6.37 s |
| UltraQuant + `UQ_FAST=1`, retuned, 262K (2026-10-07 19:49Z) | 34.02 (0.4K) | 32.00 (48.6K, 1 rep) | 50.74 s (48.6K) | not run | 24.07/51.62 (2: 37.42) | 1.27/6.35 s (2: 2.40) |

- **8 slots, short context** (`concsweep.py`, 3 reps): aggregate 1..8 =
  31.9/57.7/75.7/98.6/119.7/110.0/126.3/143.4. M=6 dips because
  `MAX_SKINNY_BATCH_SIZE=5` sends every W4A16 projection to Triton; 8 is +19% over 5.
- **8 slots, 48K cached** (`longconcsweep.py 38000 1,5,6,8`): aggregate
  23.49/49.57/47.96/52.44, so it is flat from 5 to 8 streams.
- **Production context is where UltraQuant loses:** -13% at 1 stream and -36% at 5
  before the retune (23.49/49.57 vs 26.95/78.00). After the retune it is -11% and -34%
  (24.07/51.62). Cached-prefix TTFT is 2.4-3x fp8.
  - Cause (a): continuation chunks over 128 tokens dequantize the whole cached prefix
    to bf16 and then run flash-attn, every layer, every chunk.
  - Cause (b): 1536-token blocks double the recomputed tail.
  - Cold 64K TTFT is +14%.
- **Quality:** needle 3/3 at about 17.5K, 20K and 52.5K. A 175K needle was correct in
  411 s, and 5 concurrent 35K prompts were correct with no OOM. 262K needle: see Open.
  GSM8K and tool calls were not run on UltraQuant.

## What we patch (resources/zz_lds_gate_impl.py, resources/uq_decode_fast.py)

**Upstream on gfx1201.** The FlyDSL fast kernel in vllm#57057 is gfx950-only. gfx1201
falls back to a generic Triton path whose `tl.dot_scaled(e4m3, e2m1)` emulation costs
about 17 ops per element. Stock launch geometry measured 47 GB/s effective (estimate),
and 64K decode was 18.11 tok/s.

**`_patch_uq`, shared continuation buffer.** `ultraquant_attn.py:516-533` caches a
`max_model_len` bf16 K and V buffer per layer: 966 MiB per layer, 15.1 GiB across 16
layers (upstream OOM: "Tried to allocate 484.00 MiB"). The patch shares one holder
across layers. There is no upstream fix; it is worth filing against #57057.

**`uq_decode_fast.py`, a Triton split-KV kernel** for D=256 and GQA 6 with no sinks or
sliding window:

- It decodes FP4 to fp16 with a bit trick, applies UE8M0 group scales per tile
  (SHIFT=7), and computes transposed scores with fp16 WMMA.
- It reuses upstream `reduce_segments`.
- `uq_decode_fast` handles pure decode. `uq_prefill_fast` handles continuation chunks
  of 128 tokens or fewer (BLOCK_M 16 rows = tokens x GQA heads, causal mask), which
  replaces upstream's per-token synthetic decode.
- Hadamard query rotation runs once per call outside the kernel.
- `UQ_FAST=0` keeps the old upstream path with tuned geometry (`_patch_uq_ops`): 64K
  decode about 23.6 instead of 29.6 tok/s.

**Tests** (scratchpad, not in git):

- `test_uq_decode_fast.py`: CPU interpreter 13/13 and GPU pass. Minimum cosine
  0.9999985 against a dequant reference. A nibble-swapped variant fails at 0.30.
- `test_uq_prefill_fast.py`: CPU 7/7, GPU 9/9, cosine above 0.9995 per token.
- `check_hook.py`: routing at 1, 128 and 129 tokens, sinks, and `UQ_FAST=0`.
- All passed on GPU at the retuned defaults (19:24Z).

**Silent-failure risks:**

- SHIFT=7 saturates if a group exponent exceeds 8 (group absmax above about 2300).
  Observed K exponents were -1..1 and V exponents -3..-1.
- Q and P are fp16 where stock uses e4m3 and bf16.
- The hook prints `SKIPPED` and falls through if upstream symbols move, so check it on
  every nightly bump.

**Workload retune (19:24Z).** `bench_uq_workload.py` timed 12 geometries; each figure
is the minimum of 40 replays over at least 256 MiB of distinct caches, two passes with
the order reversed. Each geometry was weighted by the 30d concurrency and
prompt-length mix below.

| Point, ms per 16-layer step | s32/t32/w4 (old) | best |
| --- | ---: | ---: |
| 1 x 16K / 40K / 98K | 2.22 / 2.45 / 5.30 | 1.67 / 1.84 / 3.76 |
| 2 x 16K / 40K / 98K | 3.05 / 5.80 / 12.5 | 2.54 / 4.30 / 7.28 |
| 4 x 16K / 40K / 98K | 4.61 / 9.66 / 21.97 | 3.73 / 7.26 / 15.47 |
| Weighted | 7.332 | 5.366 (s64/t64/w4, -27%) |

- The decode default is now 64 splits, tile 64, 4 warps. Per-concurrency bests are
  within 2% of it.
- Prefill default: tile 64, 4 warps, segments `cdiv(32 * CUs, q_blocks * Hk)`. Weighted
  4.269 vs 4.574 ms per layer (-7%).
- The refactor itself was neutral: new/old median 1.004 (p10 0.976, p90 1.026,
  n=108).
- Per-kernel profile, ms per step at 1 seq x 16K/40K/98K:
  - Kernel: 1.51/2.40/6.47.
  - Hadamard rotation GEMM: about 0.4.
  - `reduce_segments`: 0.15-0.23.
  - Cast: 0.04-0.21.
  - The work around the kernel is about 2% of a 35 ms step, so fusing it is not worth
    doing.
- Expected end-to-end gain (estimate): about +2% per stream at 1 x 40K, about +12% at
  4 x 98K. Attention is 7-10% of the step; weight GEMMs are about 80%.

## Workload (VictoriaMetrics, 30d to 2026-10-07)

`pod=~"qwen38-27b-vllm-[a-z0-9]+-[a-z0-9]+"`, datasource `victoriametrics`.

| Metric | Value |
| --- | --- |
| Concurrency while busy | 1: 46.8%, 2: 24.4%, 3-4: 20.9%, 5-8: 8.0% (mean 2.05; 7d 1.77) |
| Prompt length | p50 40.6K, p90 94.9K; <=20K 22.2%, 20-50K 43.7%, 50-100K 29.1%, >100K 7.1% |
| Cached share of prompt tokens | 91% |
| Computed tokens per request | p50 1,254; <=128 tokens 3.4% (7d 5.1%) |
| Decode share of request time | 79-87%; generated tokens p50 235 |
| ITL | p50 34.1 ms, p90 42.0 ms (24h before the 19:29Z roll) |

## KV offload tier

- **The GPU link is PCIe Gen3 x8.** It was Gen2 x8 until 2026-10-06 18:14Z (host
  `lspci` LnkSta, 90 samples under load). Earlier notes assumed Gen5 x16.
- **The faster link did not speed up the tier.** CPU to GPU load: 3.35 GB/s on Gen2
  (48h before) vs 3.12 GB/s on Gen3 (24h after). Store: 3.31 vs 3.43 GB/s.
  - Load query: `sum(increase(vllm:kv_offload_load_bytes_total[24h])) /
    sum(increase(vllm:kv_offload_load_time_total[24h]))`. Store uses the `store_*`
    pair; Gen2 uses `[48h] offset 26h`.
  - disk tier read speed: 1.7-1.9 GB/s.
- **UltraQuant doubles what the 12 GiB CPU tier holds:** about 712K tokens vs about
  385K on fp8.
- **3d to 19:24Z:**
  - external hit 0.45: 22.1M external of 22.1M + 26.7M computed tokens,
    `prompt_tokens_by_source`.
  - disk share of tier chunk hits: 20.5% (2,205 / (8,544 + 2,205),
    `kv_offload_tiering_chunk_hits_total`).
  - async lookup stall: 6,172 s over 1,447 lookups.
  - 19 pod restarts, each of which wipes the tmpfs tier.
  - control-1 MemAvailable: minimum 15.0 GiB, p5 16.2 GiB.
- **Decision: keep 12 GiB** and re-check the manifest rule (hit < 0.25 or disk share >
  20%) after 7 restart-free days.
- **disk tier mechanics** (vLLM `43b4aaea3` source):
  - A lookup blocks only the request that missed the CPU tier: `fs/manager.py:215-219`
    returns `RETRY`, and the scheduler skips that request and re-polls it every step.
    The cost is that request's TTFT, not other streams' decode.
  - The existence checks run on one thread per tier (`tiering/async_lookup.py:116-121`).
  - Cascade writes are `O_DIRECT` on a 16-thread pool, off the scheduler path.
- **There is no config switch to stop disk lookups after warm-up** (fs options:
  `root_dir`, thread counts, `enable_kv_events`, `locality`, `backpressure`).
  - Smallest option: wrap `FileSystemTierManager.lookup` in the hook to return `MISS`
    after an uptime or CPU-fill gate, while writes continue. `tiering/manager.py:441-478`
    handles `MISS`.
  - Native per-request option: `kv_transfer_params: {"kv_load_tiers": [{"medium":
    "cpu"}]}`, passed through litellm `extra_body`.
  - Neither is built. Every disk hit given up becomes GPU recompute, which slows every
    stream; decide on warm, restart-free data.
- **The manifest's "removing the disk tier halved decode"** (2026-08-19) has no mechanism
  in this build: CPU eviction is in-memory bookkeeping. It is unproven either way.

## Weight GEMMs and MXFP4

- **At M=1 the W4A16 GEMMs run at 85-90% of achievable bandwidth** (559-621 GB/s
  in-pod). That means 15.2 GB per step for a floor of about 25 ms, against 26.8-29.9
  ms measured. Only fewer bytes per weight help there.
- **MXFP4 W4A8 (radiance `radiance_mxfp4_fp8.hip`)** measured 1.12x at M=1 and 1.42x at
  M=4 on summed step GEMMs, plus about 2x prefill
  (`perf-plan-2026-09-13.md`, in git history at `6f1c0b4c7`).
  - The 09-13 `gate_up` loss came from that bench's `DEC_MAX_N=32768`. Radiance HEAD
    `f6727a21` raised it to 36864, which covers N=34816.
  - Blockers now: no licence file and no explicit grant were found for radiance (GitHub
    `magiccodingman/vllm-radiance`, GitHub API `license` null; Codeberg
    `ggz14/radiance-vllm-mxfp4`, `licenses: None`), so it cannot be vendored into this
    MIT repo. The kernel also needs packaging.
  - The Apache-2.0 alternative is vllm#60413 (int8-dot W4A8 GEMV, M <= 8, RDNA3). It
    ports to gfx12 by an arch gate: `v_dot4_i32_iu8` exists on gfx1201.
- **Swift 1.5 MXFP4 checkpoint:** `ethanwtodd/Swift-1.5-Qwen3.8-27b-Quark-RTN-MXFP4`
  (sha `459bc7fe8340`, 19.8 GB vs 19.6 GB for our AWQ).
  - Same format as `amd/Qwen3.8-27B-Quark-AWQ-MXFP4`: fp4 weights in groups of 32 with
    e8m0 scales, `lm_head` excluded, dynamic MXFP4 activations recorded and computed
    as W4A8.
  - It is RTN, not AWQ.
  - Its card reports GSM8K about 95% under radiance, unverified (the card's FP8 row at
    85.75% looks off).
  - The other match, `slybase/...heretic-MXFP4-DFlash2-radiance`, is an abliterated
    variant in radiance's `.rad` format; rejected.
  - Nothing newer than Swift 1.5 exists (HF search 2026-10-07).
- **Integration point in `43b4aaea3`:** `vllm/model_executor/kernels/linear/__init__.py`.
  - `_POSSIBLE_MXFP4_KERNELS[ROCM]` lists `[AiterMxfp4LinearKernel,
    EmulationMxfp4LinearKernel]`. On gfx1201 that falls to emulation: dequant plus
    `F.linear`.
  - `register_linear_kernel(cls, platform, kernel_type="mxfp4")` appends, so our
    kernel class must be inserted at index 0. `--linear-backend` cannot select an
    unregistered custom class (`__init__.py:257, 361`).
  - This checkpoint (fp4 weights, dynamic fp4 input) selects `QuarkOCP_MX`, which calls
    `init_mxfp4_linear_kernel` (`quark.py:653-707, 880`; `quark_ocp_mx.py:238-241`).
    `quark_w4a8_mxfp4_fp8.py` (static fp8 input) never uses the registry.
- **Upstream exit:** none for gfx12 yet.
  - vllm#46676 (native HIP MXFP4, open) targets gfx1100 only.
  - vllm#60413 (W4A8 int8-dot MXFP4 GEMV, open) targets RDNA3/3.5.
  - Both use the same `MxFp4LinearKernel` layer, so a gfx12 kernel can follow that
    pattern upstream.

## Methodology (GPU microbenchmarks on this box)

- **Defeat the 64 MB Infinity Cache** with at least 256 MiB of distinct caches; the
  first sweep was invalid because two 17.8 MB caches fit in it.
- **Production shares the GPU**, so take the minimum of 40 replays, pair the modules
  being compared, and run two passes with the order reversed. Absolute numbers are
  contended; rankings hold.
- **gfx1201 has a per-process 2x slow band** (ROCm#6347): measure the stock kernel in
  every process and use ratios only.
- **Microbenchmarks overstate end-to-end gains:** a predicted 26.8 tok/s was 23.57 real.
  Always confirm with a live roll.
- **Haystack sizing:** the needle haystack is 1.17 tokens per word (286,188 tokens for
  245K words); 218K words gives about 254.6K tokens.

## Open

1. Done: production-context throughput after the retune (Verdict).
2. Done: needle at 262144 passed at 10% depth. The 254,579-token prompt recalled
   `3G55F474` in 603 s (about 422 tokens/s of prefill). The 50% and 90% depths were
   stopped to spare production.
3. Offload round-trip correctness for 1536-token UltraQuant blocks (store verified,
   reload never checked).
4. Free VRAM at peak under UltraQuant.
5. GSM8K and tool calls on UltraQuant.
6. Tail sweep (`tailprobe.py`): only tail 1 = 0.118 s was measured.

## Rollback

- **Full rollback to fp8:** revert this branch's manifest values (`kvCacheDtype:
  fp8_e4m3`, drop `kvCacheCustomDtype`, `--kv-cache-memory 10200547328`,
  `maxModelLen: 246944`), restore the aiter `attn_3d` num_stages patch from the hook's
  git history (dropped with nightly `81198e97`), then reconcile.
- **Partial rollback:** `UQ_FAST=0` returns to upstream kernels with tuned geometry.
  The KV format stays, so the KV tiers stay warm.
- Any KV dtype change re-keys the disk tier, which then starts cold.
