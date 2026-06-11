# Plan: Serve Qwen3.6-27B on SGLang-ROCm (custom RDNA4 image, AMD R9700 / gfx1201)

Replace the **vLLM-ROCm** deployment with a **custom-built SGLang image** carrying the RDNA4 patch set,
serving **Qwen3.6-27B (dense, hybrid attention)** at **int4-AWQ** on the **AMD Radeon AI PRO R9700**
(RDNA4 / gfx1201, 32 GB), tuned for an **agentic workload** (Hermes / OpenCode, heavy skill+tool use,
1–2 full-context sessions + several shorter ones) via **RadixAttention prefix sharing** and
**client-side context compaction**.

**Branch:** `feat/sglang-qwen3.6-rocm`
**Worktree:** `.claude/worktrees/sglang-qwen3.6-rocm` (copied `.mcp.json`, `.vscode/`, `.claude/` excl. worktrees; `.env`/`CLAUDE.local.md` absent in repo)
**Namespace:** `ai`  •  **Chart:** bjw-s `app-template` (OCIRepository)  •  **Node:** control-1 (`amd.com/gpu`)
**Supersedes:** `docs/vllm-qwen3.6-rocm-plan.md` (vLLM stays as the running fallback until Step 5 proves SGLang)

---

## Why we are leaving vLLM (verified June 11 2026)

Three multi-agent research passes (engines / quant+fp8 / KV+context compression), each adversarially
verified against primary sources, converged on the same conclusion: **vLLM-ROCm runs Qwen3.6 through
fallback kernel paths on gfx1201**, and the engine that has a *verified-working* hybrid path is SGLang.

| Finding | Evidence | Consequence |
|---|---|---|
| vLLM-ROCm has **no clean gfx1201 path** — FP8 WMMA + AITER fast kernels are out-of-tree, author refuses to upstream | vLLM #28649 | FP8 silently dequants to FP32; AITER off (`VLLM_ROCM_USE_AITER=0`) |
| vLLM hybrid-KV manager **over-allocates ~7×** (pads constant-size DeltaNet state to attention page size) | vLLM #37121 | Shrinks usable concurrency exactly where agentic traffic needs it |
| **SGLang is the only engine with a verified Qwen3.6 GatedDeltaNet path on gfx1201** + RadixAttention prefix sharing | mattbucci fork patches 001/016/047 | Real RDNA4 DeltaNet kernel + best-in-class shared-prefix reuse |

---

## Decision record (why this shape)

### Engine — **SGLang v0.5.12 + the RDNA4 patch fork**, single GPU, **no TP**
- Source: `github.com/mattbucci/2x-R9700-RDNA4-GFX1201-sglang-inference` — **37 patches**, active to 2026-06-10, purpose-built for gfx1201. **Ships conda-only — no Dockerfile; we write our own (Step 1).**
- TP=2 deadlocks on R9700; we have one card → TP=1 is the only path anyway. Drop RCCL / `iommu=pt` / P2P (dual-GPU-only).
- Runner-up / fallback: **llama.cpp (HIP, gfx1201)** — most reliable, best single-stream decode, fused quantized KV — but far weaker continuous batching. Keep as safe-harbor, not the performance play.

### Weights — **int4-AWQ**, `mattbucci/Qwen3.6-27B-AWQ` (gs128, fused Triton GEMM; DeltaNet+vision in BF16)
- The fork's own README **recommends AWQ-int4 for dense Qwen3.6-27B** and calls fp8 a poor fit.
- ~19 GiB weights → **~11–12 GiB KV budget** on 32 GB. The R9700 decode win comes from the **DeltaNet Triton wave-32 path + `fused_moe_gptq_awq` kernel** (Marlin is CUDA-only — irrelevant here).
- Keep the DeltaNet path out of the quant: ignore-list `lm_head`, vision, DeltaNet (`in_proj_*`, `conv1d`), **all gating → BF16** (8 exponent bits for the recurrent state that compounds across 48 layers; BF16 + FP32-accumulate, not FP16).

### KV cache — **fp8_e4m3** (the fork's validated default — REVERSED from the original bf16 call)
- **Reversal (Step 3, after reading the fork's `launch.sh`):** the `qwen36-27b` preset serves this exact
  model with `--kv-cache-dtype fp8_e4m3` on the real R9700, on top of dedicated RDNA4 fp8 patches
  (039 pertoken-padding-fix, 042 reclaim-fp8-load-transients, 044 modelopt-fp8-rocm-allowlist). The earlier
  "fp8 KV is unfused / net loss" conclusion was about **unpatched upstream SGLang/vLLM**, not this fork —
  it does not hold here. This also matches the user's original instinct (fp8 quant + fp8 KV).
- The fork picks fp8 to make **262144** context fit on the dual-card rig; on a **single 32GB card** we start at
  **131072** (262K fp8 KV + 19 GiB weights won't fit one card at `mem-fraction 0.80`). KV math (16 full-attn
  layers, GQA kv_heads=4, head_dim=256): fp8 ≈ **32 KB/token** → 131K ≈ 4.2 GiB.
- **bf16 KV is now the Step 5 A/B** (higher decode quality + speed, lower max context) — one-flag swap.
- **The 48/64 GatedDeltaNet layers hold constant-size recurrent state → KV is already ~4× smaller than a dense 27B. Free, structural, banked.** Sized via `--max-mamba-cache-size 8`.

### Context strategy — **RadixAttention prefix sharing + client-side compaction** (the real levers)
- **RadixAttention** (on by default): the shared system+tool/skill prefix is stored once across sessions, not N×. Up to ~6.4× on prefix-heavy workloads (arXiv 2312.07104). **Gating hazard ⇒ Step 2.**
- **OpenCode client-side compaction** (prune 40k-protect / 20k-min, then summarize): shrinks each session's *unique tail* — the term that decides fit — at zero kernel risk; **never prunes `skill` outputs**. Engine-agnostic.
- **Off the table on gfx1201 today** (do not architect around): int4/KIVI KV (absent in SGLang), token eviction (SnapKV/H2O/etc — unmerged *and* unsafe for tool chains), sparse attention (gated to DeepSeek/GLM), host-RAM offload (HiCache doesn't support hybrid models; LMCache CUDA-only), TurboQuant (a *KV* vector-quant, CUDA-only, ROCm port `[PLANNED]`).

### Decode lever for later — **MTP / speculative decoding**
- Real ~1.5–2× lever, but **blocked today**: DFlash OOMs the DeltaNet draft path even at 16K on this patch set; SGLang Spec-V2 asserts on ROCm. Revisit-quarterly item; documented route is an AWQ-int4 + BF16-MTP recast. **Not available now.**

### Verified version pins (as of 2026-06-11)
| Component | Pin | Note |
|---|---|---|
| ROCm | **7.2.4** (production; 7.13 is preview — do not mix) | fork targets 7.2.1; 7.2.4 base image is current |
| PyTorch | **2.12.0+rocm7.2** (cp312, download.pytorch.org/whl/rocm7.2; tv 0.27.0) — torchaudio stays 2.11.0 (no 2.12 wheel; unused) | corrected from 2.9.1 after reading `setup.sh`; default bumped to latest-stable 2.12 per user (audio not needed). Fork validates on 2.11 → that triple is the one-flag fallback if a kernel compile fails |
| Triton | **3.6.0** (the fork's `sglang-triton36` pin, pip wheel from the rocm7.2 channel) | 3.6/3.7 release notes target **gfx1250, NOT gfx1201**; gfx1201 rides generic RDNA4 wave-32 + fork patches |
| transformers | **>=5.0** (installed `--no-deps` + `gguf`) | fork's `setup.sh` upgrades past the SGLang-pinned 4.x |
| SGLang | **v0.5.12** (fork base; upstream stable is v0.5.12.post1) | upstream has no gfx1201 support |
| Base image | **rocm/dev-ubuntu-24.04:7.2.4-complete** (pin by digest) | |
| Model | **`mattbucci/Qwen3.6-27B-AWQ`** | tuned + calibrated on R9700 |

### Realistic numbers (single R9700, int4-AWQ, agentic)
- **Decode:** c=1 ~11–25 tok/s (kernel-bound); **c≈10 ~40–90 tok/s aggregate, ~5–10 tok/s/session**.
- **Prefill:** ~100–200 tok/s/stream chunked → a *fresh* 100K context costs minutes → **RadixAttention is non-negotiable** (only the per-turn delta prefills).
- Headline 300–800 tok/s figures are **dual-GPU TP=2 on MoE models** — halve for one card; dense-27B scales worse. Treat all as upper bounds; **validate on the card.**

**Honest risks:** (1) **single-author 37-patch fork** with no upstream/AMD support — every ROCm/SGLang bump risks a rebase; (2) fork validated on **2× R9700 (TP2)**, not single-card — VRAM fit + behavior need re-checking solo; (3) **int4 may degrade high-entropy tool-call tokens** (a sibling model scored 0/6 vs FP8 4/6 on SWE-bench) — validate on real agent traces, FP8 W8A8 is the quality fallback at VRAM cost; (4) **RadixAttention cache-hit tool-call hazard** on Qwen3.6 hybrid (omlx #825) — Step 2 gate.

---

## Process Instructions

- After completing each step, update this plan with the current status.
- Pause for user confirmation before proceeding to the next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of this plan have been
  consolidated into existing documentation, this plan file can be removed. If there is no relevant
  existing documentation, this plan should be reworked into a reference document.

**Important:** Every prompt should verify the branch and worktree before doing any work
(`git branch --show-current` ⇒ `feat/sglang-qwen3.6-rocm`; `pwd` ⇒ the worktree).

---

## Step 0 — Branch & worktree  ·  Status: ✅ done

> Worktree `.claude/worktrees/sglang-qwen3.6-rocm` on branch `feat/sglang-qwen3.6-rocm`.
> Copied `.mcp.json`, `.vscode/`, `.claude/` (excl. worktrees). `.env`/`CLAUDE.local.md` absent (skipped).

**Continue prompt:** "Verify branch/worktree, then do Step 1: build the custom SGLang RDNA4 image."

---

## Step 1 — Build the custom SGLang-fork image  ·  Status: ◑ authored (build pending on the R9700 node)

The fork ships **conda instructions, no Dockerfile** — we author one and mirror the result to GHCR
(pin by digest; per `project_ik_llama_image_tags`, community tags get rebuilt in place — digest only).

**Built (local, in `docker/sglang-rdna4/`):**
- `Dockerfile` — base `rocm/dev-ubuntu-24.04:7.2.4-complete@sha256:92f309c5…` (digest-pinned),
  `PYTORCH_ROCM_ARCH=gfx1201`, **no** `HSA_OVERRIDE`. Installs miniforge, vendors the fork at
  **`FORK_REF=1592f671…`** (today's HEAD), then runs the fork's own `scripts/setup.sh` rather than
  re-implementing the build. That script clones stock SGLang v0.5.12, applies `patches/[0-9]*.patch`
  (37 patches incl. 001 upstream-sync, 016 qwen3next-conv1d-tp, 047 hybrid-mamba-vhead-dim; the
  `050…CANDIDATE` is excluded by the glob), creates the `sglang-triton36` env, installs the pinned
  torch/triton/transformers stack, and builds the three native gfx1201 HIP kernels
  (`sgl_kernel`, `awq_gemv`, `skinny_gemms_int4`) into the env's site-packages.
- `entrypoint.sh` — activates the conda env and `exec`s the CMD (server args come from the HelmRelease).
- `README.md` — pins table, the GPU-at-build requirement, and the GHCR push-by-digest recipe.
- Correctness-critical gfx1201 env (from the fork's `common.sh::setup_rdna4_env` — `SGLANG_USE_AITER=0`,
  Triton AWQ/flash-attn enables, `PYTORCH_HIP_ALLOC_CONF`, …) is **baked into the image `ENV`**;
  single-GPU device/RCCL vars are deliberately left for the HelmRelease.

**Corrections found while reading the fork (vs. the original plan):** PyTorch — the fork pins
**2.11.0+rocm7.2** (not 2.9.1) from `download.pytorch.org/whl/rocm7.2`; we default the image to the
latest-stable **2.12.0+rocm7.2** + torchvision 0.27.0 (torchaudio has no 2.12 wheel, stays 2.11.0,
unused — audio not needed), with the 2.11 triple as a one-`--build-arg` fallback if a HIP-kernel
compile fails. Triton 3.6.0 is the **pip wheel** (source build
only behind `TRITON_FROM_SOURCE=1`); transformers is upgraded to **>=5.0**; **Rust/cargo** is required
(SGLang v0.5.12 grpc) — installed via **rustup `stable`** (latest), not apt's old cargo; `protoc` comes
from conda-forge `libprotobuf` inside the env. There is no
`fused_moe_gptq_awq` Marlin path — the decode win is the native `awq_gemv` HIP GEMV + `sgl_kernel`
native rotary (its Python fallback garbles dense AWQ output).

**Remaining (needs the card — cannot finish from this workstation):**
1. **Build** on the R9700 node (`setup.sh` asserts `torch.cuda.is_available()` and the AWQ-GEMV/skinny
   build scripts import the freshly-built `.so` → a gfx1201 GPU must be in the build sandbox):
   `podman build --device /dev/kfd --device /dev/dri --group-add keep-groups -t sglang-rdna4:local docker/sglang-rdna4`.
2. **Mirror to GHCR** (`ghcr.io/tanguille/sglang-rdna4`), record the pushed **digest** for Step 3.

**Watch (carried into the build):** patch apply order (setup.sh aborts FATAL on any failed patch);
whether any patch hard-codes TP2 assumptions needing a single-card tweak; build wall-time (large ROCm
base + 3 HIP kernel compiles).

### Image build automation (authored — was "out of scope", now wired)

The image rebuilds itself on any Dockerfile change, via the existing Renovate→Flux machinery:

1. **`gpu-builder` runner** (`kubernetes/apps/actions-runner-system/.../runners/gpu-builder/`) — a
   second `gha-runner-scale-set`, **scale-to-zero** (`minRunners: 0`), pinned to **control-1**, claiming
   `squat.ai/dri` (shared, `count:4` → co-exists with the serving pod) with the ROCm securityContext and
   an 80Gi scratch volume. Reuses the cluster runner's GitHub App secret + the shared OCIRepository.
2. **`.github/workflows/build-sglang-rdna4.yaml`** — `runs-on: gpu-builder`; triggers on `push` to
   `docker/sglang-rdna4/**`, `workflow_dispatch`, and a weekly cron. `podman build --device /dev/kfd …`
   → push `ghcr.io/tanguille/sglang-rdna4:v0.5.12-gfx1201` (stable tag, rebuilt in place).
3. **Renovate** — `# renovate:` annotations on the Dockerfile track the base-image digest, the fork
   `FORK_REF` (git-refs `main`), and miniforge; grouped into one **`sglang-rdna4`** review PR (no
   automerge). Merging it changes the Dockerfile → fires the build. After push, Renovate digest-pins the
   **HelmRelease** image (also no automerge) → Flux deploys. Closed loop.

**First-run validation items (flagged, not blockers):** runner image running as root+privileged;
`podman` overlay vs `vfs` storage driver in-pod; real disk headroom on control-1 (base image is large);
and `GITHUB_TOKEN` write to the `ghcr.io/tanguille/*` user-namespace package (may need the package
pre-linked or a PAT).

**Continue prompt:** "Verify branch/worktree, then build the image (push to main to fire `gpu-builder`, or build by hand on the node) and confirm the GHCR push (finish Step 1)."

---

## Step 2 — On-card validation gates (before any manifests)  ·  Status: ☐ not started

Run the image by hand on the R9700 (`docker`/`podman` or a throwaway pod) and settle the two
pass/fail gates the research flagged, **before** writing GitOps manifests.

1. **Boot:** image loads `mattbucci/Qwen3.6-27B-AWQ`, detects gfx1201, **no FP32-fallback warning**
   on the AWQ/DeltaNet path. `rocm-smi` shows the card; `/dev/kfd` + `/dev/dri/renderD*` present.
2. **GATE A — int4-AWQ tool-call quality.** Run real Hermes/OpenCode tool-call traces; confirm
   structured/JSON output is correct (not garbled at decision tokens). **If it fails → escalate to
   FP8 W8A8** (quality ceiling, ~28.7 GiB, eats KV — re-evaluate the whole VRAM budget) and record it.
3. **GATE B — RadixAttention cache-hit tool-call fidelity (omlx #825).** Send two requests sharing a
   long prefix; on the *second* (cache hit), confirm the model still **emits tool calls, not plain
   text**. The 48 DeltaNet layers' recurrent state is not prefix-shareable and has a documented
   regression here. **If it fails, prefix caching is the blocker — not memory** (decide: disable APC
   and eat the prefill cost, or pin a patch/workaround).
4. Quick **resident-context** probe: load to ~100K and read actual GPU KV size + max concurrency line.

**Continue prompt:** "Verify branch/worktree, then do Step 3: write the sglang app manifests."

---

## Step 3 — Create the `sglang` app  ·  Status: ◑ authored (manifests written, `kustomize build` passes; deploy is Step 4)

> **Authored ahead of the Step 2 on-card gates** (those need the card; these are just files). The
> Step 2 gates still gate the *deploy* in Step 4 — they are not skipped.

New app dir `kubernetes/apps/ai/sglang/` mirroring `vllm/` (GPU plumbing reused verbatim — node
affinity `amd.com/gpu`, `squat.ai/dri`, video/render groups 44/226, seccomp Unconfined, `/dev/shm`
emptyDir; `/dev/kfd` already exposed by the generic-device-plugin from the vLLM work). Files:
`ks.yaml`, `app/{kustomization,pvc,servicemonitor,helmrelease}.yaml`, and `sglang` added to the `ai`
namespace `kustomization.yaml`. `kustomize build kubernetes/apps/ai/sglang/app` → 4 objects, **passes**.

**The args were rewritten from the fork's hardware-validated `launch.sh qwen36-27b` preset** — the
original plan's guessed args were wrong and would have **crashed**. Concretely, vs. the old guess:
- `--dtype bfloat16` **was missing** → default fp16 hits a bf16/fp16 type mismatch in triton
  `decode_attention` and kills the scheduler ~3s after startup. **Mandatory.**
- `--num-continuous-decode-steps 8` **was missing** → `=32`/default kills the thinking-mode scheduler
  on this hybrid. **Mandatory.**
- `--kv-cache-dtype` → **`fp8_e4m3`** (the validated default), not `bfloat16` — see the KV reversal above.
- `--max-mamba-cache-size 8` added (sizes the GatedDeltaNet recurrent-state cache); `--disable-cuda-graph`,
  `--disable-custom-all-reduce`, `--trust-remote-code`, `--watchdog-timeout 600`, `--enable-metrics` added.
- `--mem-fraction-static 0.80` (was 0.90), `--max-running-requests 8` (was 14), `--chunked-prefill-size 8192`
  (was 2048) — preset values.
- `--context-length 131072` (single-card; preset's 262144 assumes TP2/64GB), `--tensor-parallel-size 1`.
- `torch_native` attention is the documented escape hatch (in a comment) if Triton-bf16 attention garbles
  tool-call logits at long KV. RadixAttention stays default-on (no `--disable-radix-cache`) pending Gate B.

**Image is referenced by tag `v0.5.12-gfx1201` with a loud `TODO(Step 1→4)` to swap to the `@sha256`
digest** once built+pushed (memo `project_ik_llama_image_tags`). No ConfigMap `${VAR}` data, so no
envsubst-escaping concern (`project_flux_envsubst_escaping`).

**Continue prompt:** "Verify branch/worktree, then do Step 4: deploy & verify GPU bring-up (needs the built image)."

---

## Step 4 — Deploy & verify GPU bring-up  ·  Status: ☐ not started

1. Commit; `flux reconcile ks sglang` (vLLM stays running in parallel — different app/route).
2. Pod schedules on control-1, claims `squat.ai/dri`; `rocm-smi` shows the R9700.
3. Logs: SGLang loads AWQ, RadixAttention initialised, no FP32 fallback.
4. Smoke: `curl .../v1/chat/completions` → coherent completion + a correct tool call.

**Continue prompt:** "Verify branch/worktree, then do Step 5: benchmark vs vLLM baseline."

---

## Step 5 — Benchmark & decide  ·  Status: ☐ not started

Reuse the existing bench harness (`/tmp/benchctx2.py`). **Always report both PP and TG**
(per `feedback_pp_tg_always`).

1. **Single-stream:** PP@{2K,8K,32K,64K,100K} + TG. Compare to the vLLM baseline table in
   `docs/vllm-qwen3.6-rocm-benchmarks.md`.
2. **Concurrency:** fire N=1..14 shared-prefix sessions; record aggregate + per-session TG, RadixAttention
   hit-rate, max stable concurrency, VRAM at idle/load.
3. **Resident context:** max stable `--context-length` at bf16 KV; confirm ~170K ceiling.
4. **Decision:** if SGLang beats vLLM on agentic aggregate + holds tool-call quality → promote SGLang,
   schedule vLLM retirement (Step 6). If not → keep vLLM, document why, park this branch.

**Continue prompt:** "Verify branch/worktree, then do Step 6: client compaction, integrate, retire vLLM."

---

## Step 6 — Client compaction, integrate & decommission  ·  Status: ☐ not started

1. **Client-side compaction:** configure OpenCode/Hermes compaction (40k protect / 20k min) so each
   session's tail is summarised before it reaches the server (`skill` outputs preserved).
2. **LiteLLM:** point the `qwen3.6-27b` backend at `http://sglang.ai.svc.cluster.local`; verify
   open-webui / agent clients see it.
3. **Retire vLLM** (and the dead NVIDIA `llama-server`): only after embeddings are re-homed
   (`project_vmcp_tei_batchsize_restart`). Remove from the namespace kustomization once traffic is moved.
4. ServiceMonitor + Grafana dashboard for SGLang `/metrics`.

**Continue prompt:** "Verify branch/worktree, then do the final documentation pass."

---

## Step 7 — Final docs pass & PR  ·  Status: ☐ not started

1. Fold durable bits (Dockerfile + image digest, the int4-AWQ/bf16-KV/no-fp8 rationale, gfx1201 env,
   measured limits, the two validation gates) into repo docs / app README.
2. Reconcile/retire `docs/vllm-qwen3.6-rocm-plan.md` and the benchmark doc.
3. Remove this plan file (or rework into a reference doc).
4. Open PR with `gh` (no Claude attribution, per project convention). **Do not push until asked.**
