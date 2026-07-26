# vLLM vs SGLang on the R9700 — 2026-07 measurement round

Qwen3.6-27B AWQ on a single Radeon AI PRO R9700 (gfx1201, 32 GB). Records what was
measured, what it overturned, and why the current configs look the way they do.

## The binding constraint: 0.875 VRAM

**The R9700 is shared with Jellyfin media transcoding.** LLM serving must never exceed
`0.875` (`memFractionStatic` for SGLang, `gpuMemoryUtilization` for vLLM). The ~4.6 GB
`rocm-smi` reports free at 0.875 is the transcoding reservation, not slack.

Any benchmark taken above 0.875 is invalid as a production candidate no matter how well
it scores. Two were run during this round before the constraint was known; both are
recorded below and both are rejected.

## What was overturned

### The "decode ceiling" was KV-pool starvation

Earlier rounds concluded aggregate throughput plateaued near 40 tok/s and that this was a
fixed per-step overhead, not bandwidth. Half right: the plateau was real, the cause was
not the GPU.

At concurrency 16 under a small pool, per-stream throughput collapsed to 2.45 tok/s (from
4.85 at concurrency 8) because requests were queuing and preempting. Doubling the pool
held per-stream at ~4.6 and aggregate nearly doubled:

| pool | conc 8 | conc 16 | per-stream @16 |
|---|---|---|---|
| 109,217 tok | 39.04 | 39.32 | 2.45 |
| 213,362 tok | 39.34 | **74.50 / 73.84** | 4.62 |

The 213K row was taken at 0.95 and is **not deployable**. The finding it produced is
still valid and redirects tuning effort: grow the KV pool within 0.875, do not chase
kernel-level decode optimisations.

### Benchmark runs are unreliable immediately after boot

The first sweep after a cold start reads low — one config measured 32.36 tok/s at
concurrency 16 on its first run and 39.04 / 39.09 on two immediate repeats. Historic
"noise" of 32.8–38.5 at concurrency 8 was largely this artifact, and some earlier
single-run comparisons were reading warmup as a config difference.

**Always discard the first run after a pod restart.**

### Image tags are not ordered by version number

The `vllm-openai-rocm` nightly self-reports `0.23.1rc1.dev1474+g0ba2aa35a` — a stale base
tag 1474 commits behind its actual content. The `v0.26.0` release was *built earlier*
(02:47Z) than the nightly (05:17Z) despite the higher version.

Neither publish timestamps nor tag names establish lineage. Only the self-reported
version string plus build metadata do.

## Engine comparison

Same sweep script, short prompts to isolate decode, unique salt per stream so prefix
caching cannot inflate results.

At the production 0.875 budget, SGLang serves a **183,240**-token GPU pool plus 9.01 GB
host-RAM hierarchical KV and 7.14 GB host mamba cache, with 3.71 GB GPU headroom left for
transcoding. It clamps `max_running_requests` 32 → 20 at this size.

The table below is **0.95-only**: neither column is deployable, and nothing in it has
been re-measured at 0.875, so no row should be quoted as a property of the production
configs.

| | vLLM v0.26.0 | SGLang v0.5.15 |
|---|---|---|
| GPU KV pool | 213,362 tok | 261,019 tok |
| host-RAM L2 | none | 12.8 GB KV + 7.1 GB mamba |
| conc 1 | 4.28 / 5.17 | **14.18 / 14.44** |
| conc 8 | **39.34** | 36.28 |
| conc 16 | **74.50** | 69.46 / 70.60 |
| 13.8K prefill | served | **OOM, scheduler died** |

Reading the table: SGLang leads single-stream, vLLM leads at concurrency 16, and the
`13.8K prefill` row is the one that disqualifies the config — SGLang survived short-prompt
decode and died on the prompt shape we actually serve, while vLLM served it on the same
budget. That is evidence the 261K pool is unreachable in practice, not evidence about
0.875 stability; SGLang has run at 0.875 in production for weeks.

Pool size is the only figure comparable across budgets, since it is reported at startup
rather than benchmarked. **A like-for-like engine comparison at 0.875 has not been run**
and is the main gap in this round.

## Speculative decoding (vLLM, n-gram k=3)

Accepted 286 of 303 draft tokens (94.4%). Helps repetition-heavy work and hurts
concurrency:

| workload | baseline | ngram |
|---|---|---|
| quote-back (13.8K prompt) | 4.06 | **7.52** (+85%) |
| novel generation, conc 8 | 32.82 | 37.84 (+15%) |
| novel generation, conc 16 | **64.96** | 39.57 (−39%) |

It also force-disables async scheduling and costs ~12K tokens of KV pool. **Left out of
the committed config** because concurrency is the priority for this workload. Worth
revisiting if single-stream latency becomes the dominant complaint.

## SGLang v0.5.16

Skip the release, cherry-pick one commit. The release relocated the kernel tree
(`srt/layers/attention/**` → `kernels/ops/**`), which breaks the fork's ~90 patches.

`#31648` (mamba LRU) is the one worth taking: prefix hit rate 0.61 → 0.83. Not yet
applied — it needs a fork rebuild.

## Operational hazard: patching during a model download corrupts the cache

Hit during this round and worth knowing before it costs someone else 40 minutes.

Patching an `InferenceService` while its `model-downloader` init container is mid-transfer
replaces the pod and truncates the file being written. The corruption is then **invisible
to every subsequent boot** until the engine tries to load the weights and fails with
`SafetensorError: incomplete metadata, file not fully covered`.

The downloader runs:

```sh
curl -fsSL --etag-compare "$etag" --etag-save "$etag" -o "$dest" "$url"
```

Three things combine:

1. **No atomic write.** curl streams to the final path, so an interrupted transfer leaves
   a truncated file where a valid one belongs. A `$dest.tmp` + `mv` would make this
   impossible.
2. **ETag is not integrity.** The etag file is written from response headers, which
   arrive before the body, so it can persist for a transfer that later dies. The next
   boot sends `If-None-Match`, gets `304`, and prints `Model artifact ... revalidated`
   over a corrupt file. Nothing checks size against `Content-Length`.
3. **The fallback keeps bad data.** `elif [ -f "$dest" ]` treats presence as validity.

There is currently no content-integrity mechanism at all: sha256 pinning was rejected on
the `Model` CRD, leaving only revision pinning.

A `Content-Length` comparison on the finished file is the cheapest mitigation and needs
no schema change, but it is only a **truncation guard**, not integrity. It would have
caught this incident and would not catch a corrupted-but-complete transfer. It also does
nothing about atomicity: the artifact and its `.etag` are still published as two separate
non-atomic steps, so a crash between them leaves the same inconsistent state. A real fix
is download-to-temp plus a single rename, with digest or format-level validation before
publishing.

**Recovery:** scale the InferenceService to 0, mount the model-cache PVC in a throwaway
root pod, delete the bad artifact *and* its `.<name>.etag`, scale back up. Deleting the
weight file alone is not enough; the stale etag will short-circuit the re-download.

## Open items

- **Prefix cache reports 0% on vLLM** across 889,464 queries with 0 hits and 0
  preemptions. Confirmed upstream code bug (`vllm#45238`), not a capacity problem. On a
  workload of agents resending near-identical 35K contexts this is worth more than any
  decode tuning.
- **Quant comparison is unfinished.** vLLM runs QuantTrio AWQ, SGLang runs mattbucci AWQ,
  so the engine comparison above is engine+quant. `mattbucci/Qwen3.6-27B-AWQ-CT`
  (compressed-tensors W4A16) is the untested candidate on **vLLM only**: it should bind
  `RDNAHybridW4A16LinearKernel`, a gfx1201-tuned int4 GEMM for M≤5, which the `auto_awq`
  path does not. It is not an SGLang candidate — its card describes it as the raw GPTQ
  output *before* AWQ repack, so it is upstream of the model SGLang already serves, and
  SGLang's compressed-tensors support is applied mainly to `w8a8_fp8`.
- **How much VRAM does transcoding actually need?** 0.875 is treated as fixed. Whether it
  has margin, or is itself too high under concurrent transcode load, has not been
  measured.
- **Host-RAM L2 for vLLM** (`kv_offloading_size`, `kv_offloading_backend`, present in
  v0.26.0) costs no VRAM, which makes it attractive under this constraint. Blocked on
  node memory: control-1 sits at 97% memory requests and SGLang's equivalent wanted
  ~20 GB.

## Method notes

Scripts live in `bench/` next to this doc, so the numbers above stay auditable:

- `concsweep.py` — aggregate decode at concurrency 1/8/16. Short prompts to isolate
  decode from prefill, unique salt per stream so prefix caching cannot inflate results.
- `spectest.py` — verbatim-reproduction throughput on a 13.8K-token prompt. This is the
  coding-agent shape where prompt-lookup n-gram actually hits; a short-prompt test shows
  near-zero acceptance.

Both drive the OpenAI `/v1/completions` endpoint, so they run unmodified against either
engine. Port-forward the service and pass the port:

```sh
kubectl -n ai port-forward svc/qwen36-27b 30000:30000 &
python3 concsweep.py 30000
python3 spectest.py 30000
```

**Discard the first run after a pod restart** (see above), and note `concsweep.py` reports
aggregate tok/s, not per-stream. Every figure in this doc comes from these two scripts
against a single-replica service with nothing else on the GPU.

Workload profile driving these choices, from litellm `LiteLLM_SpendLogs` over 21 days
(14,678 requests): prompt p50 35,346 / p90 88,583 / p99 112,050; generation p50 196 /
p90 2,324; concurrency 4–6. **54.4% of real requests exceed a 32K context.**
