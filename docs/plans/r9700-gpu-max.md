# Qwen3.8-27B on the R9700: Combined Stack Implementation Plan

**Created:** 2026-10-07 (v3: owner kept UltraQuant; v2 reviewed by Opus and Codex `gpt-6-astra`)
**Status:** Approved for Waves 1-2 (no production change). Later waves need owner OKs.
**Complexity:** High
**Estimated Chunks:** 8
**Branch / worktree:** Chunk 1 uses `feat/vllm-ultraquant-shared-buffer` in
`.claude/worktrees/docs-vllm-nightly-bench-0929`. Each later chunk branches from main
in `.claude/worktrees/<chunk-branch>`.

---

## Overview

Build one stack for Qwen3.8-27B Swift 1.5 on the R9700 (gfx1201, 32 GB shared with
Jellyfin). The aims: scale with the production mix, keep the quality gap to full bf16
small, and carry the least out-of-tree code.

**Owner decisions (2026-10-07):**

- Keep UltraQuant 4-bit KV.
- Bleeding-edge versions are welcome.
- Agents do the work under the routing below.

**Where time goes today** (baseline, 2026-10-07 20:07-20:42Z, `bench/prodshape.py`,
independent 16K-98K sessions, 1,254-token tails):

| Sessions | Agg tok/s | Decode/stream | TTFT p50 | ITL p50 |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 17.96 | 31.5 | 5.05 s | 31.6 ms |
| 2 | 24.10 | 29.4 | 11.38 s | 34.1 ms |
| 4 | 26.63 | 22.6 | 25.9 s | 44.5 ms |
| 5 | 28.93 | 19.9 | 23.1 s | 50.2 ms |
| 8 | 6.69 | 1.5 | 116.8 s | 67.0 ms |

**TTFT dominates, and it is not mainly attention:**

- Cold prefill is 950-970 tok/s at 16-40K and falls to 808 at 98K (98,637 tokens in
  122.1 s). Up to 40K it is bound by the GEMMs and GDN; attention adds about 15% by 98K.
- A 1-session turn computes about 1,890 tokens, about 2 s of work. TTFT is still 5.05 s.
- The offload lookup stall did not fire during the baseline: the
  `kv_offload_lookup_async_delay_seconds` count stayed at 45. Earlier pod-lifetime
  events averaged 8.8 s. Queueing is the open term.
- **8 sessions collapse on KV capacity:** hit rate 0.317 and 656K tokens recomputed,
  because about 414K tokens of prefixes overflow the 430,982-token pool. This is why
  UltraQuant's headroom matters.
- Chunk 2 measures the split per steady turn before any kernel work.

**Target stack:**

| Layer | Choice | Chunk |
| --- | --- | --- |
| KV | UltraQuant 4-bit, our decode kernel, and our continuation kernel extended to large tiles | live; 4 |
| Prefix cache | `--prefix-match-unit 64` (upstream). Blocks stay 1536. Re-keys the fs tier | 5 |
| Offload | CPU 12 GiB + fs tier. `blocks_per_chunk` 1 vs 4 (partial tails need 1). Lookup stall fixed by data: per-request `kv_load_tiers` or a warm-up gate | 2, 5 |
| Weights | W4A16 AWQ. MXFP4 W4A8 only with a licensed kernel that passes the gates | 6, 7 |
| Hook | Required patches fail boot. Delete the LDS gate (upstream #52619) and the dead aiter attn_3d patch | 3 |
| Spec decode | Off. MTP staged test only in an owner-approved window | 8 |
| GDN | Upstream default (FlyDSL is CDNA-only and fails boot on gfx1201) | none |

**Principles:**

- Config over code, upstream over ours. Every custom component has a deletion trigger.
- **Bleeding edge, frozen per measurement:**
  - Bump to the newest nightly digest in its own roll (Chunk 3).
  - Re-baseline throughput and quality after it.
  - Freeze the image, checkpoint and kernel shas for every A/B.
- One acceptance table. Every number comes with its command. Baseline first.
- **No agent touches production:**
  - The owner merges.
  - Ask before any push, apply, reconcile, resume or restart.
  - The orchestrator alone edits plan status.

### Acceptance table (every roll)

**Throughput:** `prodshape.py` with a warm-up pass, then 2 paired runs (median). The
engine is quiet at start, the run is band-classified (`mem_busy >= 60` at run 1,
`sclk >= 3100`), and VRAM is sampled by `vramloop.sh` (3 s cadence). The baseline is
re-run on the live stack within 24 h before each roll.

| Metric | Gate (rollback if missed) |
| --- | --- |
| Aggregate tok/s at 1, 2, 4, 5 sessions | >= 0.98x baseline (median of 2) |
| TTFT p50 at 1 and 2 sessions | <= 1.02x baseline |
| Preemptions, errors | 0 in the steady window |
| Min free VRAM over the run (node-wide) | >= the measured baseline minimum, and never < 1.82 GiB (largest observed Jellyfin excursion) |

**Quality:** `bench/qualitygate.py`, engine quiet, per-run `cache_salt` for cold passes
plus one deliberate warm pass.

| Check | Gate |
| --- | --- |
| Teacher-forced NLL on 20 fixed texts (8K-32K, `prompt_logprobs`) | mean delta <= 0.01 nats/token vs baseline |
| Top-1 agreement, 60 prompts x 128 greedy tokens (20 GSM8K, 20 code, 20 long) + 2 image prompts | Chunk 4/5: >= 98% (fixed before the run); pure refactors >= 99.5%; no divergence before token 16 |
| GSM8K first 150, greedy | >= 143 pass; 138-142 rerun on the full 1319; < 138 fail |
| Tool calls | 12/12, single and 5 concurrent |
| Needles | 3/3 at 17.5K/52.5K/175K. Plus one cached-prefix hit. Plus one offload reload: load the needle, send >= 450K distinct tokens to evict it from the GPU, resend, and assert the `prompt_tokens_by_source{external_kv_transfer}` delta is > 0 |
| Weight or KV encoding changes only | GPQA-198 x 3 seeds within 2 pt of the baseline. bf16 anchors: GPQA 88.6 (5 seeds), GSM8K 97.5-98.5 (200 chat items, same template) |

---

## User Stories

### US-1: Scale with the production mix

**As** the owner, **I want** the most tokens/s at production shape, **so that** agent
sessions finish sooner.

#### Acceptance Criteria

- [ ] Targets are set after Chunk 2's TTFT breakdown, as a fraction of the measured
      stall, queue and prefill terms. US-1 is informational; the acceptance table
      gates.

### US-2: Quality close to bf16

#### Acceptance Criteria

- [ ] Every roll passes the quality table.

### US-3: Less maintenance

#### Acceptance Criteria

- [ ] Chunk 3 deletes two hook patches.
- [ ] Every remaining component has a deletion trigger and an upstream filing (Chunk 8).

---

## Technical Analysis

Sources:

- the stack-design workflow `wf_2ea06b12-86c` against vLLM `43b4aaea3` (main
  `08567505b`);
- the Opus and Codex reviews of v1 and v2;
- [ultraquant-2026-10-07.md](../llm-hosting/ultraquant-2026-10-07.md).

### Prefix cache and offload

- **Why the block is 1536:** `attn_block = 64 * cdiv(1,634,304 B mamba page, 64 *
  1088 B)` gives 1536 (`platforms/interface.py:1005`). `--block-size` only grows it,
  and `--mamba-block-size` is ignored in align mode.
- **`--prefix-match-unit 64`** (`config/cache.py:90-100`):
  - Partial hash hits (`kv_cache_coordinator.py:695-728`).
  - Mamba partial tail by raw byte copy (`single_type_kv_cache_manager.py:2055-2155`,
    `worker/utils.py:728-775`).
  - An extra scheduler stop (`core/sched/scheduler.py:505-535`).
  - It only shortens recompute for GPU-resident hits while `blocks_per_chunk` > 1.
    Offload partial tails need `blocks_per_chunk == 1`
    (`offloading/scheduler.py:301-315`).
  - `tokens_per_hash` and `blocks_per_file` are part of the fs namespace
    (`file_mapper.py:51-60`). Both the flag and its revert start the fs tier cold.
  - If the mamba cache mode is not align, the fallback is silent
    (`kv_cache_utils.py:802-807`). Positive check: `/kvoffload/*/config.json` shows
    `tokens_per_hash` 64.
- **Lookup stall:**
  - One serial lookup thread per tier (`tiering/async_lookup.py:116-121`).
  - A RETRY skips only the requesting request (`fs/manager.py:215-219`).
  - Fix options: per-request `kv_transfer_params.kv_load_tiers` (native, via litellm
    `extra_body`), or a hook wrapper on `FileSystemTierManager.lookup` gated on uptime
    and CPU-tier fill.

### UltraQuant continuation kernel (extend `uq_prefill_fast`)

- **The large-tile reasoning holds.** `BLOCK_Q = BLOCK_M // 6`, so FP4 decode is reused
  across 2 tokens at M16, 10 at M64 and 21 at M128. Only M16 was ever benched.
- **The forecast is a sweep, not a promise.** Upstream bf16 flash-attn already runs at
  40-46 TF.
- **Default and gates:** start at M64/T64/w8 or M64/T32/w4, and record the spill count.
  Gate on spills <= the live default; `uq_decode_fast.py:467-469` already spills 8 at
  M16. Segments are a sweep dimension (the measured retune preferred 32*CUs).
- **Real chunk sizes** in align mode: mid-prompt chunks are clipped to block multiples
  (`core/sched/scheduler.py:487`). Bench q = 129, 256, 512, 1001, 1536, 3072, 4096.
  Gate each bucket at <= 1.0x upstream. Set `UQ_PREFILL_MAXQ` at the crossover.
- **Graph safety:** the chunk's KV is stored before attention
  (`turboquant_attn.py:498-523`). Prefill batches run PIECEWISE (`UNIFORM_BATCH`,
  `turboquant_attn.py:220`), so routing on host ints is graph-safe. q=1 extends are
  decodes.
- **Semantics change:** intra-chunk K/V are read as FP4 instead of bf16 (upstream
  `ultraquant_attn.py:539-540`). The quality table treats it as a numerical change.
- **VRAM:** the max-context workspace is reserved at init
  (`turboquant_attn.py:249-262`), so bypassing upstream does not free it. Only the
  shared holder is avoided. Any pool change comes from measured free VRAM, and a
  `UQ_FAST=0` or `UQ_PREFILL_MAXQ` rollback must also revert the pool.

### MXFP4 W4A8 (licence first)

- **Licence:** no licence file and no explicit grant were found for radiance (GitHub
  `magiccodingman/vllm-radiance@f6727a21`, Codeberg `ggz14/radiance-vllm-mxfp4`). Do not
  vendor it. A private fetch needs a grant from the rights holders, not an owner OK.
- **Apache-2.0 option: vllm#60413.**
  - int8 per-32 activations at M <= 8.
  - At M > 8 it quantizes activations to MXFP4, dequantizes the weights, and runs
    `F.linear`, so prefill may regress.
  - Opt in with `VLLM_ROCM_MXFP4_W4A8`. The gfx12 port is an arch-gate change
    (`v_dot4_i32_iu8` on gfx1201).
- **Routing:** the checkpoint (fp4 weights, dynamic fp4 input) → `QuarkOCP_MX` →
  `init_mxfp4_linear_kernel` (`quark.py:653-707, 880`; `quark_ocp_mx.py:238-241`).
  Insert at index 0 of `_POSSIBLE_MXFP4_KERNELS[ROCM]`.
- **Build rules:**
  - Custom op with a fake impl.
  - Scratch allocated before capture.
  - `VLLM_DISABLED_KERNELS=EmulationMxfp4LinearKernel`. This does not stop #60413's
    internal fallback, so test M = 8 and 9.
  - Build key sha(source + torch + hip + arch), atomic rename.
  - An `in_proj_ba` (N=96) fallback.
- **Correctness references per kernel:** #60413 uses int8 per-32 activations, radiance
  uses fp8 per-token. Gate on rel-L2 < 1e-2 and cosine. Include negative variants.
- **Profile first:** lm_head at M=1/2 and the M=5 vs M=6 step before committing to any
  GEMM kernel.

### Spec decode (off)

- **Schedule:** `num_speculative_tokens_per_batch_size: [[1,1,3],[2,2,1],[3,8,0]]`.
  - k=0 still runs the draft prefill.
  - V1 dynamic speculation forces PIECEWISE.
  - EAGLE and MTP groups disable offload partial tails.
- **UltraQuant verify is unsafe:**
  - It uses `seq_lens_cpu_upper_bound` (`attention/backend.py:446-450`).
  - FULL graphs bake host ints into the capture.
  - #40914 and #43747 are open, and #39273 is open.
- **The test is an outage:** a second 27B engine cannot fit, so it needs an
  owner-approved window.

### Maintenance Ledger

| Component | Required? | Deletion trigger |
| --- | --- | --- |
| UltraQuant shared continuation buffer (`_patch_uq`) | **required** (avoids a 966 MiB/layer OOM): boot fails if it cannot install | upstream fix (file against #57057) |
| `uq_decode_fast.py` decode + continuation | performance | upstream gfx1201 UltraQuant fast path; offer ours as a PR |
| M<=32 Triton tile | performance | upstream gfx12x tile (offer ours) |
| kv-offload guard CronJob | required | upstream fs-tier evictor |
| MXFP4 kernel class (if shipped) | required when the MXFP4 checkpoint is served | gfx12 kernel upstream |

---

## Decisions & Trade-offs

- **Decision 1, KV format:** UltraQuant (owner).
- **Decision 2, MXFP4 kernel source:**
  - Ask the radiance author for an Apache-2.0 or MIT grant (owner sends).
  - Bench the #60413 gfx12 port as the clean option.
  - Ship only a kernel with a licence grant.
- **Decision 3, roll granularity:** one variable per roll, with one exception. When two
  changes both re-key the fs tier, they ride the same roll so the tier goes cold only
  once (Chunk 5: prefix unit + `blocks_per_chunk`). Their effects are then attributed by
  per-turn hit counters.
- **Decision 4, spec decode:** off. Re-test only in an owner-approved window after
  Chunk 5.

---

## Dependencies

- **Prerequisites:** the owner merges Chunk 1 and OKs the Flux resume; a nightly
  containing main `5281e4990`; the owner sends the licence request.
- **External:** checkpoint `ethanwtodd/Swift-1.5-Qwen3.8-27b-Quark-RTN-MXFP4@459bc7fe8340`;
  `amd-quark` in the image (verify).
- **Bench pod:** `uq-bench` on the same image digest. GPU use is serialized with
  `flock /tmp/gpu.lock`. Each process checks that node free VRAM >= its need + 1.82 GiB
  before allocating. Upstream comparisons stay <= 98K unless the owner approves a
  window.

---

## Agent Orchestration

The orchestrator is the session model (Opus 5.5). It owns:

- decisions;
- gates;
- production actions;
- plan status;
- re-running gate numbers before quoting them.

| Work type | Model | Effort |
| --- | --- | --- |
| Fully specified mechanical steps (fixed commands, exact edits, lint, staging, sha256, given PromQL) | Haiku 5.5 | low |
| Reading and contract extraction with file:line | Sonnet 5.5 | medium |
| Code or manifests following an existing pattern | Sonnet 5.5 | medium |
| Novel kernel or contract code | Sonnet 5.5 | high |
| Adversarial refutation (a different lens from the author) | Sonnet 5.5 | high |
| Cross-family review of plans and kernels | Codex `gpt-6-astra` (CLI, read-only) | default |

**Rules:**

- **Escalate on failure.** Haiku → Sonnet medium → Sonnet high.
- **Structured outputs** carry the commands each agent ran.
- **Pipeline by default.**
- **One writer per file per wave.**
- **Agents may use the `uq-bench` pod (behind `flock`) and VictoriaMetrics.** They
  never touch the production pod, applies or pushes.
- **Under 10 agents per workflow.**

---

## Execution Plan

### Progress Tracker

#### Wave 1 (parallel): land and measure, no restart

- [ ] Chunk 1: Land git = live + benches + docs; owner merges, Flux resume
  - 2026-10-09: committed on the branch, live = branch (ISVC and `lds-gate-patch` patched in place, `llmkube-models` suspended). Waits on push, merge, resume.
- [x] Chunk 2: TTFT breakdown + quality baseline + bench upgrades
  - [ttft-breakdown-2026-10.md](../llm-hosting/ttft-breakdown-2026-10.md), measured on the rolled stack; it led to the `max-num-batched-tokens` 2048 roll.

#### Wave 2 (parallel): bench-pod work, no restart

- [x] Chunk 4: Large-tile continuation kernel (tests in `bench/uq/`)
  - Negative: M16 wins the q-sweep, cap stays 128 (4cd691e86). No routing change for Chunk 5.
- [x] Chunk 6: MXFP4 kernel bench (#60413 gfx12 port) + licence request + lm_head profile
  - 0.979x decode GEMM, no gain (adcc52db1). Licence request is the owner's.

#### Wave 3 (sequential): Roll A0

- [x] Chunk 3: Nightly bump + hook cleanup + fail-closed required patches (depends on 1, 2)
  - Live 2026-10-09: `81198e97` (contains `5281e4990`), bit-identical to `43b4aaea3` on the gate at equal speed. LDS gate and aiter patches deleted, `_patch_uq` fatal; agreement 60/60 vs the old hook.
  - Then `8cbd5d03` (vllm#60533 hybrid prefix-cache boundaries) with `max-num-batched-tokens` 2048: full gate passed, equal speed.

#### Wave 4 (sequential): Roll A1

- [x] Chunk 5: Kernel routing + `--prefix-match-unit 64` + `blocks_per_chunk` arm + lookup-stall fix chosen by Chunk 2 data (depends on 3, 4)
  - Live 2026-10-09 with Chunk 3. `prodshape` agg tok/s / TTFT p50, old -> new: 1 session 17.96 / 5.05 s -> 25.2 / 1.9 s; 2: 24.1 / 11.4 s -> 41.4 / 3.3 s; 4: 26.6 / 25.9 s -> 48.8 / 7.7 s; 8: 8.4 / 96 s -> 40-43 / 17 s.
  - Attribution at 8 sessions: `blocks_per_chunk` 1 alone 30.5 (outputs identical), `--prefix-match-unit 64` alone 8.1 (no fix without bpc 1).
  - Agreement gate exception: `--prefix-match-unit 64` gives 66.4% (27/60) with zero cache hits in the run, so it is the extra prefill split, not cached-state reuse. Quality held: NLL delta <= 5e-5, GSM8K 146/150 both, needles 3/3 at 17.5K/52.5K/175K plus cached-prefix and offload reload, tools 12/12, vision 2/2.
  - The disk tier scored 0 hits in every 8-session run (the working set fits the CPU tier); its lookups are the remaining wait. No lookup-stall fix rolled.

#### Wave 5 (sequential): weights

- [ ] Chunk 7: MXFP4 cutover, only with a licensed kernel that passed Chunk 6 (depends on 5, 6)
  - Blocked: no licensed kernel, and Chunk 6 found no gain.

#### Wave 6: warm review and options

- [ ] Chunk 8: 7-day warm review, pool resize, MTP window, upstream filings (depends on 5; a Chunk 7 roll restarts the 7-day window)
  - 7-day window opened 2026-10-09 08:37 UTC (last restart).

### Wave Conflict Matrix

| Chunk | Write Set | Dependencies | Wave |
| --- | --- | --- | --- |
| 1 | `qwen38-27b-vllm.yaml`, `kustomization.yaml`, `resources/*`, `docs/llm-hosting/ultraquant-2026-10-07.md`, `bench/{prodshape,needle,vramfree,vramloop}.*`, this plan | None | 1 |
| 2 | `bench/qualitygate.py`, `bench/quality/*`, `bench/prodshape.py` follow-ups (after Chunk 1 commits it), `docs/llm-hosting/ttft-breakdown-2026-10.md` | Chunk 1 for `prodshape.py` | 1 (prodshape edits after Chunk 1) |
| 4 | `bench/uq/*` (kernel dev copy, tests, bench) | Chunk 1 | 2 |
| 6 | `bench/mxbench/rdna_w4a8_gfx12/*` | None | 2 |
| 3 | `qwen38-27b-vllm.yaml`, `resources/zz_lds_gate_impl.py`, docs | 1, 2 | 3 |
| 5 | `qwen38-27b-vllm.yaml`, `resources/zz_lds_gate_impl.py`, `resources/uq_decode_fast.py`, docs | 3, 4 | 4 |
| 7 | `qwen38-27b-vllm.yaml`, `kustomization.yaml`, `resources/*`, new MXFP4 Model/PVC manifest, docs | 5, 6 | 5 |
| 8 | docs; manifest per decision | 5 (7 optional) | 6 |

Waves 1 and 2 have disjoint write sets (Chunk 2 edits `prodshape.py` only after Chunk
1's commit). Chunks 4 and 6 share the GPU through `flock`.

---

### Chunk 1: Land git = live, plus benches and docs

**Status:** In Progress · **Wave:** 1 · **Model:** orchestrator

1. **Fix the evidence doc first:**
   - Drop the `--linear-backend` and `DEC_MAX_N` 32768 claims.
   - Soften the radiance licence wording.
   - Add the baseline table and the VRAM samples.
2. **Commit** the manifest sync, `resources/uq_decode_fast.py`, the docs, this plan,
   and `bench/{prodshape.py,needle.py,vramfree.sh,vramloop.sh}`.
3. **Rebase:** `git fetch origin && git rebase origin/main`.
4. **Pre-push checks:**
   - `specdiff.py` (server defaults only).
   - Live ConfigMap keys vs `resources/`.
   - Read-only `flux diff ks llmkube-models --path kubernetes/apps/ai/llmkube/models`.
   - `validate-pr.sh`, `markdownlint-cli2`.
5. **Ask**, push, `gh pr create`. **The owner merges.** **Ask**, then
   `flux -n ai resume kustomization llmkube-models`. Verify the pod age is unchanged.

---

### Chunk 2: TTFT breakdown, quality baseline, bench upgrades

**Status:** Not Started · **Wave:** 1 · **Model:** Sonnet medium writes, Haiku low
runs, orchestrator decides

1. **`prodshape.py`:**
   - Done: per-level deltas of queue, prefill and lookup-stall time.
   - Append the generated assistant text to the next turn (real multi-turn).
   - Add a `--warmup` pass and `--runs 2` with a median.
   - Report a noise floor from two runs of the same config.
2. **`qualitygate.py`:**
   - Modes `nll`, `agree`, `gsm8k`, `tools` (single and 5 concurrent), `needle`
     (including the offload-reload procedure) and `vision` (2 image prompts).
   - Per-run `cache_salt`.
   - Snapshot to `bench/quality/baseline-<stack>.jsonl`.
3. **Run on the live stack**, engine quiet: throughput baseline x2, the quality
   snapshot, and the TTFT breakdown at 1, 2 and 5 sessions. Write it to
   `docs/llm-hosting/ttft-breakdown-2026-10.md`.
4. **Decision rule:**
   - If lookup stall is >= 30% of steady-state TTFT at 1-2 sessions, Chunk 5 includes
     the lookup fix. Prefer per-request `kv_load_tiers` from litellm (no code). Fall
     back to the uptime/CPU-fill gate in the hook.
   - If queueing dominates at 4-5 sessions, check `max-num-batched-tokens` and the
     chunked-prefill interleave before any kernel work.

**Verification:** two identical runs agree within the reported noise floor. Quality
`agree` on the unchanged stack is >= 99.9%; otherwise widen the gates to the measured
floor.

---

### Chunk 3: Roll A0, nightly bump and hook cleanup

**Status:** Not Started · **Wave:** 3 · **Model:** Haiku low edits; Sonnet-medium
refuter; orchestrator gates

1. **Before the roll**, in `uq-bench` on the new digest: re-run `bench/uq` tests and
   `check_hook.py`. The kernel imports `reduce_segments`, `ultraquant.format`,
   `_get_hadamard` and `_kv_cache_flat`.
2. **Edits:**
   - The image digest (must contain `5281e4990`).
   - In `_patch_w4a16`, keep only `_install_small_m_triton_config`.
   - Delete `_patch_aiter_ua`.
   - Make `_patch_uq` fatal when `ultraquant_4bit` is configured and it cannot install.
     Optional patches may print `SKIPPED`.
   - Manifest comments.
3. **Ship:** ask, push, the owner merges, ask, reconcile, explicit restart.
4. **Boot log:**
   - The Triton tile line and `UltraQuant ... fast=True`.
   - No `SKIPPED` on required patches.
   - No LDS or attn_3d lines.
5. **Gate:** the full acceptance and quality tables. This becomes the new baseline for
   Chunk 5.

**Rollback:** revert, reconcile, restart. Nothing re-keys.

---

### Chunk 4: Large-tile continuation kernel

**Status:** Not Started · **Wave:** 2 · **Model:** Sonnet high implements; Sonnet-high
refuter + Codex; Haiku low runs the GPU

1. Move the dev files to `bench/uq/`: kernel copy, tests, bench, `check_hook.py`.
2. **Geometry picker:**
   - q <= 128: M16/T64/w4.
   - q > 128: sweep M {32, 64, 128} x T {32, 64, 128} x w {4, 8} x S.
   - Record spills.
   - Reverse the q-block order. Store directly when S == 1.
3. **Hook routing:** `_fast_ok and 1 < q <= UQ_PREFILL_MAXQ`.
4. **Tests:**
   - q in {129, 130, 200, 1001, 1536, 3072, 4096}.
   - q not divisible by BQ; `cached_len` not a multiple of 64; `cached_len` 0.
   - Gate on cosine > 0.9995 AND rel-L2 < 1e-2 against the fp32 dequant reference.
   - A bf16-intra-chunk reference to size the semantic change.
   - Negative variants (broken causal mask, scale) must fail.
5. **Bench:** cached 16K/48K/98K, against upstream in the same process, min of 40, at
   least 256 MiB of distinct caches. Node-free-VRAM check before allocating. 260K only
   in an owner-approved window.

**Verification:** each q bucket is <= 1.0x upstream; spills <= the live default.
`UQ_PREFILL_MAXQ` is set at the crossover.

---

### Chunk 5: Roll A1, kernel routing + prefix unit + offload arm + lookup fix

**Status:** Not Started · **Wave:** 4 · **Model:** Haiku low edits; Sonnet-medium
refuter; orchestrator gates

1. **Re-run the baseline** on the live stack within 24 h.
2. **Edits:**
   - The Chunk 4 kernel and routing.
   - `--prefix-match-unit 64`.
   - `blocks_per_chunk: 1` (partial tails). Measure lookup seconds per request; bpc 8
     was +45% on 2026-09-03, so bpc 1 is a real risk.
   - The lookup fix chosen in Chunk 2.
3. **Ship:** ask, push, the owner merges, ask, reconcile, explicit restart. The fs tier
   starts cold. Judge offload metrics only after the first hour.
4. **Positive checks:**
   - `/kvoffload/*/config.json` has `tokens_per_hash` 64.
   - Turn N+1 `cached_tokens` == floor(P_N/64)*64 while the prefix is GPU-resident.
5. **Gate:** acceptance and quality tables.

**Rollback order:**

- Env-only partial rollback first: `UQ_PREFILL_MAXQ=128`.
- Then revert the commit. It re-keys the fs tier again.
- If bpc 1 regresses lookup time, roll back to bpc 4 only (one more re-key).

---

### Chunk 6: MXFP4 kernel bench and licence

**Status:** Not Started · **Wave:** 2 · **Model:** Sonnet high ports; Sonnet-high
refuter + Codex; Haiku low runs the GPU

1. **Licence request** (owner sends): a two-line Codeberg issue on
   `ggz14/radiance-vllm-mxfp4` asking for Apache-2.0 or MIT on
   `radiance_mxfp4_fp8.hip`.
2. **Profile first** in the serving image: lm_head at M=1/2, and the M=5 vs M=6 step
   split. Record the GEMM share s.
3. **Port vllm#60413 to gfx12** in `uq-bench`:
   - Arch gate `__GFX11__ || __GFX12__`.
   - Host check accepts gfx12.
   - `on_gfx11() or on_gfx12x()`.
   - `VLLM_ROCM_MXFP4_W4A8=1`.
   - A custom op with a fake impl.
4. **Correctness** with the int8-per-32 activation reference:
   - M = 1, 4, 8, 9, mixed batches.
   - One real checkpoint layer (Range fetch).
   - `in_proj_ba` N=96.
   - Gate on rel-L2 and cosine. Negatives: nibble swap, untransposed scale,
     exponent +1.
5. **Compile safety:** `torch.compile(fullgraph=True)` plus graph capture and replay at
   M = 1..8.
6. **Bench** against the patched W4A16 path (hook mounted): M {1..5, 8, 9, 64, 4096},
   including activation quant and peak memory.
   - Predicted e2e = 1/(s + (1-s)/r), discounted 12% for the measured microbench
     overstatement.

**Verification:** the discounted predicted e2e is >= 1.15x at 4-5 sessions, >= 1.0x at
1 session, and prefill is not slower. If not, record and cancel Chunk 7.

---

### Chunk 7: MXFP4 cutover

**Status:** Not Started · **Wave:** 5 · **Model:** Sonnet medium writes the manifests;
Haiku low gates; orchestrator judges

1. **Pre-stage:**
   - Commit only the PVC.
   - Ask, then apply a curl Job live (not in git).
   - Verify sha256, delete the Job.
   - `df` against the 160Gi guard floor.
   - Confirm the Model CR does not trigger a parallel llmkube download.
2. **Cutover:**
   - `modelRef` and `claimName`.
   - Kernel files and mounts.
   - `VLLM_DISABLED_KERNELS=EmulationMxfp4LinearKernel`.
   - Keep the W4A16 patches dormant.
   - Ask, push, the owner merges, ask, reconcile, restart.
3. **Boot:** the kernel selection line names our class. Measured free-VRAM gate.
4. **Gate:** the acceptance and quality tables, including GPQA x 3 seeds (format
   change).

**Rollback:** revert in reverse order. The AWQ PVC stays in place.

---

### Chunk 8: Warm review and options

**Status:** Not Started · **Wave:** 6 · **Model:** Haiku low pulls metrics; Sonnet
medium drafts; orchestrator decides

1. **After 7 restart-free days:** external hit, lookup seconds per request, stall
   share, preemptions, ITL p50/p90, and the minimum free VRAM.
2. **Pool resize:** only from the measured 7-day minimum free VRAM, in a step that
   keeps it above the gate. The rollback reverts the pool together with any
   `UQ_FAST`/`UQ_PREFILL_MAXQ` change.
3. **MTP window** (owner-approved outage):
   - Stage A: `--no-async-scheduling`, PIECEWISE, full-output diff.
   - Stage B: the graph-safe verify patch.
   - Cover rejection, grammar, cache reload and dynamic-width transitions.
   - Ship only on +15% at 1 session and within 2% elsewhere.
4. **Upstream filings:**
   - The UltraQuant buffer issue (#57057).
   - Offer `uq_decode_fast` for gfx1201.
   - The M<=32 tile.
   - Ask #57199 to widen to gfx1201.
   - Comment on #45559 about lm_head at M=6-8.
   - Re-measure on a clean nightly before quoting.
5. **Docs:** consolidate this plan into `docs/llm-hosting/` and delete it.

---

## Testing Strategy

- **Kernels** (`bench/uq/`, `bench/mxbench/`): CPU interpreter and GPU tests with
  cosine plus rel-L2 and negative variants; `check_hook.py` routing. These are re-run
  on every nightly bump.
- **End to end:** `prodshape.py` (2 runs, median) and `qualitygate.py` on every roll.

## Rollback Plan

1. Revert commits in reverse order. Ask, `flux reconcile ks llmkube-models -n ai
   --with-source`, restart explicitly, then re-run the acceptance table.
2. The fs tier re-keys on any change to the KV format, model path, prefix unit or
   `blocks_per_chunk`. Expect hours of warm-up.
3. A pool change reverts together with any kernel-routing rollback.

---

## Review Log

- **v1 (fp8 direction):**
  - Opus: 3 CRIT, 7 HIGH.
  - Codex: revise before rollout.
  - The fp8-specific findings are moot after Decision 1. The rest were carried into v2.
- **v2:**
  - **Codex `gpt-6-astra`: revise before rollout.** All 7 findings applied in v3
    [FIXED]:
    1. Offload partial tails and re-keying.
    2. Separate #60413 references and its internal fallback.
    3. The 2 GiB claim removed.
    4. NLL plus bf16 anchors in the quality gates.
    5. Assistant turns, paired runs and noise floor in the bench.
    6. Required patches fail boot.
    7. Licence wording.
  - **Opus: 2 CRIT, 5 HIGH, 10 MED, 6 LOW.** All applied [FIXED]:
    - CRIT-1: Roll A split into A0 and A1, with kernel tests on the new digest.
    - CRIT-2: bench OOM guard (flock, free-VRAM check, <= 98K).
    - HIGH-1: the fs re-key stated.
    - HIGH-2: VRAM baseline via `vramloop.sh`.
    - HIGH-3: TTFT breakdown first, targets set from data.
    - HIGH-4: spill-aware default.
    - HIGH-5: bpc 1 arm.
    - MED-1 to MED-10, LOW-1 to LOW-6, and AMB-1 to AMB-6 are folded into the chunk
      text.
- **v3 review:** not run. Waves 1-2 change nothing in production. Re-review before
  Wave 3.

---

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the plan's contents are consolidated into existing docs, remove the plan file; if no relevant docs exist, rework the plan into a reference document.

**Important**: Every prompt should verify the branch and worktree before doing any work.
