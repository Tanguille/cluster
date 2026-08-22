# qwen38-27b-vllm: DFlash2 spec-decode tuning (2026-08-22)

Config lives in `kubernetes/apps/ai/llmkube/models/qwen38-27b-vllm.yaml`. This
doc is the tuning narrative behind it; the YAML only carries one-line pointers
back here.

## Why DFlash2

The running vLLM nightly (`ge9d1398d9`, confirmed 54 commits past DFlash2's
upstream merge `b389ac29`) shipped DFlash2 (upstream vllm-project/vllm#52816,
"[Spec Decode] DFlash2: local convolution + candidate selector"). Draft model
`z-lab/Qwen3.8-27B-DFlash2` is public, apache-2.0, and fine-tuned specifically
for our base model (`Qwen/Qwen3.8-27B`).

MTP speculative decoding stays deliberately omitted: measured on this
hardware, MTP + tool-calling grammar wedges to ~0.2 tok/s (100x regression)
under concurrent tool traffic. DFlash2 re-tests that wedge with a different
mechanism -- an external draft model (not an MTP head), verifying
`1 + num_speculative_tokens` positions per step. Confirmed clean: the same
tool-calling request that would have hit the MTP wedge resolves correctly
under load, no wedge.

## Real-workload result

Matched comparison, same 4 tool-calls + 4 reasoning completions, concurrent,
baseline (no DFlash2, parallelSlots=6) vs tuned (DFlash2, parallelSlots=8):

| | baseline | tuned |
|---|---|---|
| Mean TPOT | 74.6ms/token | 43.5ms/token (-42%) |
| Wall time (8 req) | 22.24s | 19.92s (-10%) |
| Draft acceptance | n/a | 34.7% |

Acceptance rate on synthetic random-token benchmarks was only 15-18% --
real tool-call/reasoning text is far more predictable for the draft model
than random tokens, so synthetic benchmarks understate the real win.

`num_speculative_tokens` swept 3/5/7 (single-sample, noisy) at M=1 and M=6:

| num_spec | M=1 TPOT | M=6 TPOT |
|---|---|---|
| 3 | 17.2ms (58.2 tok/s) | 47.4ms (21.1 tok/s) |
| 5 | 32.5ms (30.8 tok/s) | 33.8ms (29.6 tok/s) |
| 7 (shipped, draft's own block_size=8 recommends it) | 20.0ms (49.9 tok/s) | 24.3ms (41.2 tok/s) |

7 won at both ends of the sweep.

## parallelSlots: 6 -> 8

The old cap came from a TPOT probe (08-21) finding a kernel-dispatch cliff at
M=6 (`MAX_SKINNY_BATCH_SIZE=5` in `rdna_hybrid_w4a16.py`). DFlash2 verifies
`1 + num_speculative_tokens = 8` positions per step, so decode already runs
batches past that threshold every step regardless of `parallelSlots` --
confirmed via a round-1 OOM trace shaped `(48, 128)` = 6 (old parallelSlots)
`* 8`. Raising `parallelSlots` doesn't newly cross that cliff; it was already
crossed.

Swept 6/8/10 via `vllm bench serve` (`--max-concurrency` matched,
`--dataset-name random --random-input-len 50 --random-output-len 100`):

| parallelSlots | output tok/s | mean TPOT | mean TTFT |
|---|---|---|---|
| 6 | 65.7-73.5 | 67-69ms | ~550ms |
| **8** | **113.3** | **60.0ms** | **465ms** |
| 10 | 104.4 | 76.8ms | 1277ms |

8 is the peak; 10 regresses (queueing/scheduling overhead dominates).

## kv-cache-memory: 9Gi -> 7Gi (not 8Gi)

`--kv-cache-memory` skips profiling and sizes KV directly, bypassing
`gpuMemoryUtilization`. DFlash2's dynamic scratch buffers (candidate-selector,
grouped-conv scratch) aren't accounted for in that sizing at all -- they eat
into whatever VRAM is left over.

Round-1 test: 9Gi with `max-model-len` maxed to its own zero-slack ceiling
(212,960 tokens) crashed the engine with a CUDA OOM (31.86GiB card, 0 bytes
free) under a single tool-call request.

Tested 7Gi vs 8Gi at matched ~92% pool utilization margin: 8Gi measured
**worse** (98 tok/s vs 113, P99 ITL spiked to 1.6s) -- more KV reservation
means less free VRAM for the dynamic buffers, not more headroom. 7Gi shipped.

## max-model-len: 262144 -> 147456

The old value (246,944, via `vllmConfig.maxModelLen`) came from a pre-DFlash2
bisection of the pool/cap concurrency-decode ratio -- not re-validated under
DFlash2's different decode-batch shape, so it no longer applies as-is (see
git history on this file for that table if reviving the non-spec-decode
config).

Ceiling at `kv-cache-memory=7Gi` (7,516,192,768 bytes) is **160,160 tokens**,
read directly from vLLM's own KV-budget `ValueError` at zero slack (found by
deliberately overshooting `max-model-len` and reading the computed ceiling
from the crash, same trick used for the 9Gi and 8Gi ceilings: 212,960 and
186,560 respectively).

147,456 (92% of 160,160) leaves ~8% pool slack for DFlash2's dynamic scratch
buffers -- same category of headroom the kv-cache-memory choice above needs.

Tested stable under load:
- Tool-calling under concurrent load (the tool-calling-wedge risk case)
- 2000-token-prompt / 500-token-gen stress test
- A real 31K-token prompt
- **Worst case**: 8 concurrent ~17.7K-token prompts (aggregate ~141K tokens,
  near the 160,160 pool ceiling) -- all completed, pod stayed healthy
  throughout, no OOM/crash. Slow under that compound load (429ms/token
  mean), but stable.

Not tested: a synthetic ~140K-token single-prompt prefill did not finish in
400s (pod stayed healthy the whole time -- not a safety issue, just
impractically slow for interactive use at the very top of the range).

## turboquant KV compression: ruled out

`turboquant_4bit_nc` (4-bit MSE keys + 4-bit values, 3.8x compression vs
uncompressed, +2.71% perplexity) looked promising -- nearly double our
current `fp8_e4m3`'s ~2x compression. Confirmed real in vLLM's `CacheDType`
literal, but architecturally incompatible with this deployment:

```
ValueError: No valid attention backend found for rocm ... Reasons:
{ROCM_AITER_UNIFIED_ATTN: [kv_cache_dtype not supported, non-causal attention
not supported, KV connector not supported], TRITON_ATTN: [kv_cache_dtype not
supported], TURBOQUANT: [sliding window not supported, non-causal attention
not supported]}
```

DFlash2's draft model uses sliding-window and non-causal attention (its own
`config.json`); no ROCm attention backend supports that combination with a
quantized KV cache. Not a hardware gap -- would fail on any GPU with this
exact model architecture.

## Method note

All of the above was measured via live `suspend kustomization -> patch CR ->
test -> revert` cycles against the running production `InferenceService`,
never a separate environment. Every round confirmed a clean diff against git
before moving on. See `.claude/skills/live-pr-test` for the runbook pattern.
