# SGLang File-L3 Experiment Design

## Goal

Test whether a PVC-backed SGLang file HiCache tier reduces the cold-prefill cost
of an identical long Qwen3.6 request after a clean SGLang restart.

## Scope

Keep the existing cache-on, TP=1 RDNA4 configuration and its validated
GPU-to-host `direct` I/O path. Add an isolated file-backed L3 tier under a
versioned directory on the existing `sglang` PVC. The cache directory is tied
to the deployed image digest and Qwen3.6 runtime layout, so an image, model,
tokenizer, KV-dtype, page-size, TP, or attention-backend change gets a new
directory instead of reusing potentially incompatible pages.

The experiment uses eager `write_through`, rather than the current selective
RAM-only policy or `write_back`, to give pages a chance to reach persistent
storage before a planned restart. `wait_complete` favors deterministic
cache-recovery measurement over first-request latency.

## Non-goals

- Increasing host-RAM HiCache capacity; Prometheus shows insufficient sustained
  control-1 headroom for that change.
- Improving normal cache-hit latency or enabling speculative decoding.
- Treating file L3 as a restored in-memory radix tree. The first post-restart
  request must reproduce the original token prefix before storage pages can be
  found.
- Using `write_back`, `kernel` I/O, or `--hicache-size` on this hybrid ROCm
  deployment.

## Configuration

Add these settings while preserving all existing SGLang performance flags:

```text
--hicache-storage-backend file
--hicache-storage-prefetch-policy wait_complete
--hicache-write-policy write_through
--hicache-mem-layout page_first_direct
SGLANG_HICACHE_FILE_BACKEND_STORAGE_DIR=/cache/sglang/hicache/v0.5.15-gfx1201-466cc2fe-fork-7f058747-qwen36-awq-mamba-bf16-fp8kv-tp1-p1-direct
SGLANG_HICACHE_FILE_BACKEND_MAX_SIZE=8GB
```

The directory includes the image digest and fork ref. Rotate it after any model,
tokenizer, KV-page-layout, TP, or attention change. File pages may contain
prompt-derived data, so use only approved non-sensitive prompts and delete the
directory only with explicit operator approval.

## Evidence path and rollback

After Flux deploys at an idle period, perform a 16Gi PVC-free preflight, then
wait two minutes and use the pinned v0.5.15 native `POST /generate` API. Request
A, warm B, and cold B must use identical sampling and stop parameters:
`sampling_params: {temperature: 0, max_new_tokens: 128}`. Record A's `input_ids`,
generated token IDs, cold TTFT, and repeat count. After idle, verify file
stability with two identical relative-path/size/mtime snapshots, no `*.tmp.*`
files, and nonzero KV and Mamba component files before one clean pod replacement;
files being merely present is insufficient. Request B must use
`input_ids = A.input_ids + A.generated_token_ids + pre-tokenized_extension_ids`.
Do not reconstruct B from text. Measure the counters immediately before and
after B: take a pre-B scrape, wait for the 1-minute ServiceMonitor interval,
then take the post-B scrape. Require
`increase(sglang:cached_tokens_total{cache_source="storage_HiCacheFile"}[5m]) > 0`
and corroborate
`increase(sglang:prefetched_tokens_total{storage_backend="file",tp_rank="0"}[5m]) > 0`.
Record B's TTFT, output token IDs, and compare them with a recorded
deterministic cold-B control. The control must use the same immutable B
`input_ids` and request parameters, with file L3 disabled or pointed at a
separate empty directory, followed by a clean restart; it must not seed the
experiment directory. Record repeat count, disk usage, aborts, and stall
evidence. Abort on less than 16Gi free space; reject the experiment for no
positive storage hit, no TTFT improvement, mismatched output IDs, any
hybrid-state crash, any scheduler stall over 60 seconds, or sustained abort
increase above the preflight baseline.

Rollback is one manifest revert: remove the file-backend flags and environment
variables. The versioned L3 directory is intentionally retained for evidence
until the experiment is concluded; it must not be reused after an incompatible
runtime change or deleted without explicit operator approval.
