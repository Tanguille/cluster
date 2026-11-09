# radiance engine, 2026-11

qwen-3.8 (Swift 1.5 W4A16-AWQ) is served by [radiance](https://codeberg.org/StillDeadcode/radiance), a C++/HIP
engine (Apache-2.0, single maintainer, own `.rad` container), replacing vLLM nightly `9cef623` with UltraQuant KV. Live since
2026-11-01 on the R9700. The service, Deployment and `InferenceService` keep the name `qwen38-27b-vllm` so routes and alerts
did not move.

## Method

- Controlled runs 2026-11-01 on an unrouted pod with production paused: `bench/prodshape.py --levels 1,2,4,8 --warmup --runs 2`
  (prefixes 40K, 16K, 64K, 40K, 98K cycled, 3 turns), `bench/walltime.py` single-stream at 4K/16K/64K/128K, `bench/qualitygate.py`
  gsm8k, tools, vision and agree. vLLM control: nightly `e0d00e4` (2026-11-01) on the same card, same scripts.
- Band: every arm decoded in the fast gfx1201 band (mem_busy 66-82%, [gfx1201-fast-slow-band-2026-09-12.md](gfx1201-fast-slow-band-2026-09-12.md)).

## Results

| Sessions | vLLM nightly agg tok/s | radiance agg tok/s | radiance TTFT p90 | radiance itl p90 |
| --- | --- | --- | --- | --- |
| 1 | 25.8 | 28.8 | 1.5 s | 29.8 ms |
| 2 | 42.6 | 47.4 | 2.9 s | 32.0 ms |
| 4 | 54.4 | 64.9 | 5.9 s | 39.4 ms |
| 8 | 47.5 | 74.0 | 11.7 s | 58.6 ms |

- Cold prefill 2,209 tok/s against 1,250 (vLLM); single-request TTFT at 16K/64K/128K 8.3/43.3/114.5 s against 15.0/81.2/221.8 s on `9cef623`.
- Single-stream decode at 4K 36.7 tok/s against 33.5.
- Today's vLLM nightly is no faster than `9cef623` (25.8/42.6/54.4 against 26.0/41.5/54.3 at 1/2/4 sessions).
- Quality: GSM8K 146/150 on every arm and on vLLM; tools 12/12, vision 2/2. Greedy agreement against `n009` (baseline in git at `db102e988`) is 26-27% on
  radiance and 63% on vLLM today, so that gate cannot compare engines.
- Speculation (MTP, DFlash2) roughly doubles decode at 1-4 sessions (DFlash2 63/78/64 agg tok/s at 1/2/4, adaptive window
  85.5 at 4) but stalled at 8 with 8 slots (agg 40-49, itl p90 430-490 ms); see Speculation.
- GPU in the 11-01 runs: 249 W (the card's 250 W cap), junction 85 C, fan 4.1K rpm under load, idle free VRAM 2.5 GiB
  (observed minimum 1.82 GiB with Jellyfin; not a guaranteed reserve).

## Speculation

`bench/spectest.py` is the correctness check: the output must quote a long prompt back verbatim.

Production since 2026-11-02: the bold row (flags in the manifest). Live idle-engine runs,
`bench/prodshape.py --levels N --warmup --runs 2`, median agg tok/s, 0 preemptions unless noted:

| Config | 1 | 4 | 8 | 8: TTFT p90 / itl p90 | KV pool (tokens) |
| --- | --- | --- | --- | --- | --- |
| spec off, 8 slots | 29.8 | 73.3 | 72.6 | 13.6 s / 59.3 ms | 577,552 |
| MTP, 8 slots (11-02 01:00Z, `--busy-prefill-chunk off`) | 54.4 | 99.0 | 10.6, 36 preemptions | 66.7 s / 631 ms | 453,408 |
| MTP, 6 slots | 51.9 | contaminated | 95.0 | 10.3 s / 64.1 ms | 476,272 |
| MTP, 7 slots | 45.4 | 104.5 | 92.4 | 6.9 s / 67.5 ms | 464,880 |
| **MTP, 7 slots, 1.16.0** | 51.8 | contaminated | 96.1 | 8.4 s / 59.7 ms | 464,880 |
| MTP, 8 slots, k6_v4 | contaminated | 104.4 | 104.2 | 7.5 s / 65.1 ms | 515,408 |

- The 8-slot stall is KV pressure, not drafting: the MTP head takes ~124K tokens of pool and each slot ~0.29 GiB of
  recurrent state, so 8 long sessions preempt and re-prefill. A cap of 6-7 queues the extra session instead. No per-stream
  cut-off is needed: the adaptive window already prices depth by sequence count.
- 1 session swings ±10% run to run (drafting depends on the prompt): 45-52 for the same 6-7 slot config.
- 1.16.0 against 1.15.1 at the same flags: quality gate GSM8K 147/150, tools 12/12, vision 2/2, needles 6/6 to 175K;
  a first 8-session run read 85.6 with cold prefill sagging to 1,864 tok/s (not reproduced, GPU temperatures equal),
  the rerun 96.1 at 2,195 tok/s.
- k6_v4 (int6 K, int4 V, per-32 f16 group scales, no Hadamard) passed the gate against fp8_v4: GSM8K 147 vs 146, tools 12/12,
  vision 2/2, needles 3/3 at 58K and 175K; greedy prefix agreement 62% (fp8_v4 is 100% deterministic warm vs cold), so it
  flips near-ties. With 6 slots it fell to 55-72 at 8. Adopted 11-03 on 1.16.0 with 8 slots after a KL run on clean docs put
  it level with fp8_v4 (mean KL 0.004-0.006 against bf16 KV, top-1 97-98%): GSM8K 147/150, 0 preemptions, pool 514,144 tokens,
  81-90 tok/s at 8 sessions over 3 of 4 runs. The first run after the restart read 42.6 (unexplained, not reproduced).
- Prompt lookup alone (`--spec-ngram`) is refused for a container that declares a draft head, and on 1.15.1
  `rad-convert --max-spec 0` drops the head's weights but still declares `mtp`, so it is untested. Untested: `--prefill-interleave`.
- Draft acceptance in production ~52-60% (`radiance:draft_acceptance_ratio`).
- Ceiling 6 since 2026-11-06, set from production counters, not a bench. At ceiling 4, 91% of verify steps ran at depth 4
  (`radiance:draft_verify_depth_total`) and the last position still accepted 83%, at 3.21 tokens a step. Upstream's cost prior
  is 6% a draft, so 6 should give about 17% more tokens for 10% more step time. 8 sequences x 7 rows stays under the 64-row
  int8 path. Compare with the 4-ceiling baseline from 11-05 09:53Z to 11-06 00:48Z: decode p10/p50/p90 31.8/70.8/94.2 tok/s on
  replies of 200+ tokens, production pool 537,840 tokens.

## Long context (RULER)

`bench/ruler.py` runs [RULER](https://github.com/NVIDIA/RULER) at `db43f92`: its 13 synthetic tasks at 4K-128K, 10 samples
a cell, greedy, thinking off, RULER's answer prefix and string-match metric (no judge model). The prompt wrapper is the sharp
template's no-thinking render, token-identical to the server's (26 tokens, same greedy output). Sequential against the live pod
with production alongside: 780 requests, 6.5 h (median 99 s a request at 128K, 39 s at 64K).

Keep the samples: `~/.local/state/radiance-tune/ruler/data/10` (fingerprint `9c875ea2f4b4f25b`, also
`ruler-data-10-2026-11-08.tar.gz`). RULER fetches the essays live, so a regenerated set does not compare.

Baseline 2026-11-08, 1.17.0 at the manifest's flags (k6_v4, MTP ceiling 6, `--prefix-resume nearest`):

| Task | 4K | 8K | 16K | 32K | 64K | 128K |
| --- | --- | --- | --- | --- | --- | --- |
| 8 NIAH tasks, vt, cwe | 100 | 100 | 100 | 100 | 100 | 100 |
| fwe | 100 | 96.7 | 96.7 | 96.7 | 96.7 | 100 |
| qa_1 | 70 | 70 | 70 | 80 | 80 | 70 |
| qa_2 | 70 | 80 | 70 | 80 | 80 | 80 |
| **Average** | **95.4** | **95.9** | **95.1** | **96.7** | **96.7** | **96.2** |

- No falloff with length up to 128K.
- QA misses are the strict substring match and turns ended right after `Answer:` (qa_2 #1 at 4K: `<|im_end|>` at p 0.42).
- Noise: a 32K rerun matched every cell; 121 of 130 outputs byte-identical, the other 9 only in formatting or list order.
- Gate: any non-QA cell below baseline is a regression. A QA cell moves 10 per changed answer, so read QA from the averages
  and rerun the cell before acting on one flip.

## Cache

- Pool: 514,144 tokens of k6_v4 KV in VRAM with MTP and 8 slots at headroom 2560 (fp8_v4 with 7 slots: 464,880). vLLM held
  469,489 tokens (4-bit, 18 KiB/token) plus a 12 GiB CPU tier and a 128 GiB fs tier. Over 7 days 16% of its prompt tokens came
  from those tiers.
- Radiance has no disk tier (`--weights-disk-tier` is weights only). The host tier holds attention blocks only: `--prefix-cache-mib`
  16384 is about 718K tokens pinned in node RAM (19456 was 852,304). It is a mirror, so a tier near the VRAM pool adds nothing.
- Resume needs a recurrent-state checkpoint. The default policy needs ~9 slots per 100K session and capped the cache at ~6
  sessions (`store_full` evictions); `--checkpoint-policy tip-only` keeps one per session. Its 16 auto-sized slots
  still overflowed (92 `store_full` in 3 h), and 32 did too, so `--checkpoint-slots 48`.
- Sessions beyond that re-prefill cold: 26 s per 55K, ~45 s per 100K. Watch `radiance:prefix_cache_ckpt_evictions_by_reason_total`.

### Memory budget

- Measured on 1.16.0: 30.4 GiB working set, 83 `store_full` evictions in 6 h. A checkpoint is 150.5625 MiB, so 48 pin 7.06 GiB.
- `--gpu-headroom-mib 2048`: about 22K more KV tokens, estimated. The observed whole-card low at 2560 was 1.735 GiB free, so about
  1.24 GiB now if media peaks at the same absolute use (arithmetic, not measured), above `DgpuVramLow`'s 1 GiB and enough for a
  0.85 GB 4K DV transcode. 1536 would sit under the alert for about
  54 minutes per 36 h.
- Tier 16384 with a 30Gi request (48Gi limit): about 27.4 GiB working set, estimated. control-1's requests were at 97% of allocatable,
  leaving 1.6 GiB schedulable, now 3.6. No metric splits host from GPU hits, so the cost shows only as returning-chat TTFT.
- After a roll, check working set, pool size, whole-card free VRAM during media use, returning-chat TTFT and preemptions. Rollback:
  headroom 2560, tier 19456 and request 32Gi together.

## Operations

- Alerts: `LLMDecodeStepLatched` is the only slow-band guard (no liveness band probe); `LLMPrefixCacheHitRateLow` reads
  `radiance:linear_cached_tokens_total` (attention hits without a state snapshot still re-prefill) and its 0.4 threshold is
  provisional.
- Dashboard: upstream's `deploy/grafana/radiance.json`, pinned to the engine's tag. Renovate versions it by the engine image,
  so both move in one `radiance` PR, opened as soon as the release appears; merging stays manual.
- Roll: llmkube's vllm runtime in own-launcher mode (`command: [radiance]`, flags in `extraArgs`) gives idle-gated rollouts
  (`vllm:num_requests_running`, 15 min cap) and keeps the ModelRouter working; `runtime: generic` makes the router refuse
  (`UnsupportedRuntimeInModelRouting`). A restart empties the host tier.
- Engine or checkpoint bump: no manual step. The Job `qwen38-27b-rad-convert` reruns when the image or the Model `source`
  changes. The `.rad` is named by the checkpoint revision and the engine image digest, which replacements also write into
  `--model`, so a pod that starts first fails until the file exists instead of serving an old checkpoint or another
  engine's container. The Job then deletes the other `.rad` files and the previous checkpoint download.
- Memory: raise the host tier only with node RAM; see Memory budget.
- Reasoning comes back as `reasoning_content`. Messages, Responses, count_tokens and tokenize are served natively.
- Rollback: revert the migration commit. The vLLM compile-cache and fs-tier PVCs are pruned by Flux on merge, so a revert
  boots with empty caches.

## Gaps against vLLM

No disk tier, no `prompt_logprobs` (so no NLL gate), no `vllm:request_queue_time`, `vllm:prefix_cache_*`,
`vllm:prompt_tokens_cached_by_source` or `vllm:cache_config_info` series (the `radiance:` cache series above replace them), and a
single maintainer shipping a release every 1-3 days.
