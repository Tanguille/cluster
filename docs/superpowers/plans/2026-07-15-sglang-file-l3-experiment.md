# SGLang File-L3 Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a versioned, PVC-backed SGLang file-L3 experiment that can be evaluated after an idle restart without changing the established GPU-to-host HiCache path.

**Architecture:** The existing `sglang` PVC remains mounted at `/cache`; file-L3 pages are placed below a runtime-identity-specific directory. SGLang keeps `direct` GPU-to-host transfer and ratio-sized L2 pools, while file L3 uses eager write-through and complete prefetch for restart-recovery measurement. Documentation defines the required post-deploy evidence and rollback.

**Tech Stack:** Flux HelmRelease, bjw-s app-template, SGLang v0.5.15 mattbucci RDNA4 fork, Kubernetes PVC, Prometheus metrics.

---

### Task 1: Configure the isolated file-L3 tier

**Files:**
- Modify: `kubernetes/apps/ai/sglang/app/helmrelease.yaml:138-164`

- [ ] **Step 1: Add the versioned persistent cache environment variables**

Add these entries in the SGLang container `env` map next to `TRITON_CACHE_DIR`:

```yaml
SGLANG_HICACHE_FILE_BACKEND_STORAGE_DIR: /cache/sglang/hicache/v0.5.15-gfx1201-466cc2fe-fork-7f058747-qwen36-awq-mamba-bf16-fp8kv-tp1-p1-direct
SGLANG_HICACHE_FILE_BACKEND_MAX_SIZE: 8GB
```

- [ ] **Step 2: Add the file-L3 flags while retaining direct L2 I/O**

Append these tokens to `EXTRA_ARGS` without removing the existing cache-on
flags:

```text
--hicache-storage-backend file
--hicache-storage-prefetch-policy wait_complete
--hicache-write-policy write_through
--hicache-mem-layout page_first_direct
```

- [ ] **Step 3: Document the safety invariants beside the flags**

Update the surrounding comment to state that the directory includes the image
digest and fork ref, file L3 is an experiment for restart recovery, and the
directory must rotate after model, tokenizer, KV-page-layout, TP, or attention
changes. `write_back` remains prohibited, and L3 does not restore the in-memory
radix tree.

- [ ] **Step 4: Render the changed manifest**

Run:

```bash
mise exec -- flate test all
```

Expected: the SGLang HelmRelease renders successfully with the new environment
variables and command flags.

### Task 2: Record the operational measurement procedure

**Files:**
- Modify: `docs/llm-hosting/sglang-blockers.md:194-212`

- [ ] **Step 1: Replace the unconditional L3 rejection with bounded experiment guidance**

Document that the prior local-disk L3 trial had no runtime hits, while the new
PVC-backed experiment is solely for restart recovery. Record the precise
configuration, versioned-cache identity rule, and the requirement to remove or
rotate the directory after every incompatible runtime change.

- [ ] **Step 2: Add exact acceptance criteria**

Document this sequence, including the evidence and safety gates:

```text
1. At idle, perform a 16Gi PVC-free preflight and abort below that threshold.
2. Wait for two idle minutes, then send deterministic request A using
   `sampling_params: {temperature: 0, max_new_tokens: 128}` and fixed stop
   parameters.
3. Record A's `input_ids` and generated token IDs, cold TTFT, and repeat count;
   after idle, require two identical relative-path/size/mtime snapshots below
   the versioned directory, no `*.tmp.*` files, and nonzero KV and Mamba
   component files. Files being merely present is insufficient.
4. Cleanly restart the pod.
5. Use the pinned v0.5.15 native `POST /generate` for warm B with B's
   `input_ids` equal to A's input IDs + A's generated token IDs + pre-tokenized
   extension IDs. Do not reconstruct B from text. Use identical request
   parameters for A, warm B, and cold B: `sampling_params: {temperature: 0,
   max_new_tokens: 128}` and identical stop parameters.
6. Take a pre-B Prometheus scrape, then wait for the 1-minute ServiceMonitor
   interval and take the post-B scrape. Require
   `increase(sglang:cached_tokens_total{cache_source="storage_HiCacheFile"}[5m]) > 0`
   and `increase(sglang:prefetched_tokens_total{storage_backend="file",tp_rank="0"}[5m]) > 0`.
   Record B's TTFT, output token IDs, disk usage, repeat count, aborts, and
   stall-threshold evidence. The uncontaminated cold-B control must use the same
   immutable B `input_ids` and request parameters, with file L3 disabled or a
   separate empty directory and a clean restart; it must not seed the experiment
   directory. Compare deterministic B output IDs to that control and require a
   positive storage hit and lower TTFT than cold prefill, with no
   abort/stall/hybrid-state failure.
7. Treat all stored pages as prompt-derived data; delete them only with approval.
```

- [ ] **Step 3: Keep the rollback explicit**

Document that removing the four file-L3 flags and two environment variables
returns to the previously validated `direct` + ratio-sized L2 configuration.

### Task 3: Validate and review

**Files:**
- Verify: `kubernetes/apps/ai/sglang/app/helmrelease.yaml`
- Verify: `docs/llm-hosting/sglang-blockers.md`

- [ ] **Step 1: Run focused rendering validation**

Run:

```bash
mise exec -- flate test all
```

Expected: exit code 0.

- [ ] **Step 2: Run repository PR validation**

Run:

```bash
bash .agents/skills/pr-review/scripts/validate-pr.sh
```

Expected: exit code 0 with YAML and repository validation passing.

- [ ] **Step 3: Review the final diff**

Run:

```bash
git diff --check && git diff -- kubernetes/apps/ai/sglang/app/helmrelease.yaml docs/llm-hosting/sglang-blockers.md
```

Expected: no whitespace errors; only the isolated L3 experiment and its
operational guidance are present.

- [ ] **Step 4: Commit**

```bash
git add kubernetes/apps/ai/sglang/app/helmrelease.yaml docs/llm-hosting/sglang-blockers.md docs/superpowers
git commit -m "feat(sglang): trial persistent hicache l3"
```
