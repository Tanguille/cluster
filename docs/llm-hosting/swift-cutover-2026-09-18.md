# 2026-09-18: lmhead4 revert, Swift cutover, RAM/prefill changes

Single R9700 (gfx1201), vLLM ROCm nightly `5550994` (vLLM `dee37d891`,
2026-09-18 build), engine `qwen38-27b-vllm`. Four restarts, ~4-4.5 min each.

## Cutovers

| step | PR | outage | how |
| --- | --- | --- | --- |
| lmhead4 -> philbert bf16 lm_head | #5118 | 18:07:53 -> 18:11:45Z | pre-staged PVC, init container skipped 10/10 |
| philbert -> Swift-Qwen3.8-27b (`TheUnderscore/...-W4A16-AWQ@6ced337b`) | #5121 | 18:56:30 -> ~19:00:40Z | pre-staged, skipped 16/16 |
| drop `--long-prefill-token-threshold 1600` | `8aa874e3f` (direct) | ~4 min | |
| CPU KV tier 22 -> 12 GiB, memory 32 -> 24Gi | #5122 | ~4 min | |
| drop philbert Model CR + 50Gi PVC | #5129 | none | freed 19 GB |

Zero-download cutover: llmkube's cache dir is `/models/<sha256(spec.source)[:16]>`
and the OnChange init container HEAD-revalidates by content-length, so a curl
Job into a pre-created PVC (same name/spec as git; Flux adopts it) makes the
restart boot-only. The fs KV tier is keyed `<root_dir>/<model_name>_<sha>/`
by vLLM's `file_mapper`, so checkpoint swaps need no kv-offload PVC change.

## Swift vs philbert (same geometry, same kernels)

Idle engine, fast band (`mem_busy_percent` 62-79 under load), warmup rep
discarded, `num_requests_running` 0 before and after.

| bench | philbert bf16 | Swift |
| --- | --- | --- |
| `concsweep.py` agg tok/s M=1..5 | 30.0 / 55.0 / 71.5 / 97.1 / 114.3 | 31.1 / 54.9 / 73.4 / 96.6 / 117.1 |
| `longctx.py` 5K (5,129 tok) | 32.0 tok/s, TTFT 3.97 s | 32.0, 3.95 s |
| `longctx.py` 50K (63,894 tok) | 28.8 tok/s, TTFT 64.1 s | 28.85, 63.4 s |
| boot: GPU KV cache | 304,808 tokens / 1.23x | 304,808 / 1.23x; weights 17.64 GiB |

The first M=5 sample on Swift read 66.1 and was a one-off (three re-runs:
116.6 / 117.9 / 116.9). First rep after any restart reads slow (9.5 tok/s,
ITL 105 ms), then recovers.

## Swift quality gate

| gate | result | reference |
| --- | --- | --- |
| `toolcheck.py` (12 tool-call cases, greedy) | 12/12 | 12/12 |
| GSM8K first 150 test items, greedy | 143/150 = 95.3% | philbert 93.3, lmhead4 94.0 (09-13) |
| codrs bench edit tasks (3 reps, fixed harness) | 3/3, 2/3, 2/3 | philbert 0/9 (harness bug, see below) |

codrs 0/9 on philbert was a protocol mismatch, not model quality: the model
emitted Qwen3-Coder native `<tool_call><function=...>` blocks, vLLM's
`qwen3_coder` parser consumed them, and codrs (sending no `tools`) got
`content: null`. Fixed in codrs `fe2b5f8` (sends OpenAI `tools`, renders
streamed `tool_calls` back to its XML). The remaining 13-refactor failure is
the model leaving a non-compiling edit without re-running `cargo test`.

## Prefill cap: no single-stream effect

`--long-prefill-token-threshold 1600` removed. TTFT unchanged: 64K 63.4 s
(capped 64.1), 5K 3.95 s (3.97). Per-step cost scales with tokens, so
~1,000 tok/s at 64K / ~1,300 at 5K is the compute rate (~66 TFLOP/s, ~80% of
the power-capped bf16 ceiling). The cap stays off because its own 09-13 table
showed stall share 19% -> 37% with it. The only faster long-context prefill on
record, SGLang+Qwen3.6 ~3,200 tok/s @66K, can only have been an fp8/int8 WMMA
path; the prefill lever is checkpoint format, not a flag.

## CPU KV tier 22 -> 12 GiB

Measured over the 09-14..09-18 restart-free window (`prod_kvtier.py 4d`):
GPU prefix hit 0.789; external hit 0.297 of GPU misses (4.59M / 15.4M tokens
= ~640 s of prefill avoided in 4 days); cascade cpu->fs 14.9 GB/h at 22Gi
(write-through at any size); fs->cpu 5.5% of loads. Post-change: tier 118
chunks, cgroup 23,964 / 24,576 MiB (shmem 12,272, anon 5,150, rest page
cache), `memory.events max` flat after weight load, control-1 available
12.1 -> 18.1 Gi. Revert criteria in the manifest; 24h soak from 20:09Z.

Raising the limit instead was rejected: control-1 had 12.4 GiB MemAvailable
(`talos memory`), so a 36Gi limit would let vLLM take the node's headroom.

## Ruled out today (details in memory notes)

- MTP: retested 09-12 on this nightly, -27..-45% at the production shape and
  greedy divergence 4/5; nothing changed since.
- Flash-Next on vLLM: ~72 GiB W4A16 backbone vs 29.86 GiB VRAM, PLE offload
  needs 47.7 GiB pinned host RAM, no expert offload in vLLM main.
- Live KV in host RAM: 1.64 GB/step at 50K over PCIe = 0.55x decode M=1,
  ~4x slower M=5; no vLLM/ROCm path for dense GQA.

## Not settled

"Feels slower": per-request p50s are flat (ITL 37 ms vs 38-45, TTFT p50
1.3 s vs 1.8-6.8) but the external cache is cold (hit 0.000 vs 0.297) after
four restarts and a new fs-tier key, so tail TTFT and stall share are up
until it refills. Compare 1h windows vs 09-16/17 at equal traffic on: e2e
p90, TTFT p90, gen tokens/request (831-843 baseline), external hit ratio,
stall share, `workingset_refault_file` and lookup delay in the vllm cgroup.
