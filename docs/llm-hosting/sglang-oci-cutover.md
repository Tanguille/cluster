# SGLang: runtime-from-PVC → baked OCI image

Move sglang from the **runtime-from-PVC workaround** (engine built out-of-band onto the
`sglang` PVC by `scripts/sglang-env-rebuild.sh`, image = bare ROCm base) to a **git-reproducible
baked image** `ghcr.io/tanguille/sglang-rdna4`. The PVC keeps only the model + Triton cache.

**Why now:** the workaround existed because the cluster couldn't pull never-cached images
(Spegel upstream fall-through was broken). Spegel is healthy again, so the README's documented
follow-up — bake the env into a versioned image — is unblocked. The win is reproducibility
(rebuild-from-Dockerfile, immutable digest, self-healing Deployment) instead of a hand-built PVC.

## State of play

- The previously-pushed image is stale **`:v0.5.12-gfx1201`** — rebuilt at v0.5.14 by Stage 1.
- The build infra (Dockerfile, workflow) was prototyped in a worktree branch and is brought
  current + onto `main` by Stage 1.
- `docker/sglang-rdna4/Dockerfile` mirrors `sglang-env-rebuild.sh` (same `FORK_REF`,
  `SGLANG_TAG`, the 3 TP=1 patches). The script stays as the emergency PVC-rebuild fallback.

## Stage 1 — build infra (this PR, no production impact)

- `docker/sglang-rdna4/` — Dockerfile (v0.5.14 + 3 TP=1 patches), entrypoint, README.
- `.github/workflows/build-sglang-rdna4.yaml` — builds + pushes `v0.5.14-gfx1201` on
  **`ubuntu-latest`** (GPU-free: the HIP kernels cross-compile via `PYTORCH_ROCM_ARCH=gfx1201`;
  setup.sh's GPU touches are verification-only — see `docker/sglang-rdna4/README.md`).

Building off-cluster means **no maintenance window, ever**: builds never touch control-1's
RAM/VRAM or live serving. The trade-off: a broken kernel build surfaces at pod boot instead of
at image-build time — covered by digest-pinned rollback and the Stage 2 validation below.
(The original design used a `gpu-builder` ARC runner on control-1 + a serving maintenance
window; retired once the GPU requirement turned out to be setup.sh verification-only.)

## Stage 2 — the cutover (no maintenance window)

1. **Build** — auto-fires on the Stage 1 merge (`paths: docker/sglang-rdna4/**`), or
   `gh workflow run build-sglang-rdna4.yaml`. Watch `gh run watch`. ~15-30 min. Capture the
   pushed digest from the run summary (`…@sha256:…`). Serving keeps running throughout.
   First-run risks (hosted-runner limits, iterate if hit): disk — the ROCm base + conda env
   vs ~60GB post-cleanup; RAM — the compile vs 16GB.
2. **Pin** — set the sglang HelmRelease image to `ghcr.io/tanguille/sglang-rdna4@<digest>`,
   command path `/cache/sglang/repo-v0514/scripts/launch.sh` → `/opt/rdna4-inference/scripts/launch.sh`,
   and `CONDA_BASE: /cache/sglang/conda` → `/opt/conda`. Keep the PVC mount (model `/cache/hf` +
   Triton `/cache/sglang/triton`) and every other flag identical. Open as Stage 2 PR.
3. **Deploy** — merge Stage 2, watch the pod roll (this restart is the only serving-visible
   moment of the whole cutover). First boot recompiles the Triton JIT cache onto the PVC (the
   startup probe's ~16 min budget covers it). This boot is also the kernel smoke test the
   GPU-free build deferred: a broken build fails the startup probe here. The single GPU forces
   a Recreate rollout (no old pod kept warm), so on failure go straight to **Rollback** below —
   serving is down only for the failed-boot window either way.
4. **Validate** — `/health` up, a `qwen-3.6` completion with tools+thinking, PPL/needle spot-check, decode tok/s parity with the PVC baseline (≈16 single / ≈99 @conc24).
5. **Retire** — drop the "Runtime-from-PVC" section from the app README; keep `sglang-env-rebuild.sh`
   as the documented PVC-rebuild fallback (the Dockerfile is now primary). The PVC's
   `conda/` + `repo-v0514/` are now dead weight but harmless — clean up later.

**Rollback:** revert the Stage 2 HelmRelease commit → Flux redeploys the ROCm-base + PVC-runtime
pod (the env is still on the PVC). No rebuild needed.

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of the plan have been
  consolidated into existing documentation (the app README + `docker/sglang-rdna4/README.md`),
  this plan file can be removed.

**Important:** Every prompt should verify the branch and worktree before doing any work
(`main` was 16 commits behind during this work — always `git pull && git status` first).
