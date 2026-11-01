# radiance engine, 2026-11

qwen-3.8 (Swift 1.5 W4A16-AWQ) is served by [radiance](https://codeberg.org/StillDeadcode/radiance) 1.15.1, a C++/HIP
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
- Quality: GSM8K 146/150 on every arm and on vLLM; tools 12/12, vision 2/2. Greedy agreement against `n009` is 26-27% on
  radiance and 63% on vLLM today, so that gate cannot compare engines.
- Speculation (MTP, DFlash2) roughly doubles decode at 1-4 sessions (DFlash2 63/78/64 agg tok/s at 1/2/4, adaptive window
  85.5 at 4) but stalls at 8 (agg 40-49, itl p90 430-490 ms). `--draft-window adaptive`, `--busy-prefill-chunk 512` and
  `--max-num-seqs 4` did not fix it, so no drafter. Production runs prompt lookup instead (`--spec-ngram 5 --draft-window
  adaptive`): distribution-exact, verified only when the measured step cost says it pays; not benchmarked before it went
  live, so compare `radiance:draft_*` and per-stream decode against the 2026-11-01 snapshot. Untested: `--prefill-interleave`.
- GPU: 249 W (the card's 250 W cap), junction 85 C, fan 4.1K rpm under load, idle free VRAM 2.5 GiB (Jellyfin floor 1.82).

## Cache

- Pool: 12.9 GiB of fp8_v4 KV is 542,688 tokens in VRAM (25 KiB/token). vLLM held 469,489 tokens (4-bit, 18 KiB/token) plus a
  12 GiB CPU tier and a 128 GiB fs tier. Over 7 days 16% of its prompt tokens came from those tiers.
- Radiance has no disk tier (`--weights-disk-tier` is weights only). The host tier holds attention blocks only: `--prefix-cache-mib`
  20480 is 838,848 tokens, about 8 sessions of 100K, pinned in node RAM. It is a mirror, so a tier near the VRAM pool adds nothing.
- Resume needs a recurrent-state checkpoint. The default policy needs ~9 slots per 100K session and capped the cache at ~6
  sessions (`store_full` evictions); `--checkpoint-policy tip-only` keeps one per session. Its 16 auto-sized slots still overflowed (92 `store_full` in 3 h),
  so `--checkpoint-slots 32`.
- `--prefix-resume nearest` resumes at the deepest snapshot (up to a chunk less re-prefill a turn) and
  `--vision-tower-placement ram` gives the tower's 0.86 GiB of VRAM to the KV pool (the pool peaked at 99.9%).
- Sessions beyond that re-prefill cold: 26 s per 55K, ~45 s per 100K. Watch `radiance:prefix_cache_ckpt_evictions_by_reason_total`.

## Operations

- Alerts: `LLMDecodeStepLatched` is the only slow-band guard (no liveness band probe); `LLMPrefixCacheHitRateLow` reads
  `radiance:linear_cached_tokens_total` (attention hits without a state snapshot still re-prefill) and its 0.4 threshold is
  provisional.
- Dashboard: upstream's `deploy/grafana/radiance.json`, pinned to the engine's tag; Renovate bumps it with the image in one
  `radiance` group, 3 days after a release.
- Roll: llmkube's vllm runtime in own-launcher mode (`command: [radiance]`, flags in `extraArgs`) gives idle-gated rollouts
  (`vllm:num_requests_running`, 15 min cap) and keeps the ModelRouter working; `runtime: generic` makes the router refuse
  (`UnsupportedRuntimeInModelRouting`). A restart empties the host tier.
- Engine or checkpoint bump: the Job `qwen38-27b-rad-convert` reruns when its image or the Model `source` changes (a kustomize
  replacement feeds the source in). A fresh install waits 10 min for the operator to stage the weights.
- Memory: raise the host tier only with node RAM; the pod pins ~25.7 GiB.
- Reasoning comes back as `reasoning_content`. Messages, Responses, count_tokens and tokenize are served natively.
- Rollback: revert the migration commit. The vLLM compile-cache and fs-tier PVCs are pruned by Flux on merge, so a revert
  boots with empty caches.

## Gaps against vLLM

No disk tier, no `prompt_logprobs` (so no NLL gate), no `vllm:request_queue_time`, `prefix_cache_*`, `prompt_tokens_cached_by_source`
or `cache_config_info` series, and a single maintainer shipping a release every 1-3 days.
