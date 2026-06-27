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
- The build infra (Dockerfile, `gpu-builder` runner, workflow) was prototyped in a worktree
  branch and is brought current + onto `main` by Stage 1.
- `docker/sglang-rdna4/Dockerfile` mirrors `sglang-env-rebuild.sh` (same `FORK_REF`,
  `SGLANG_TAG`, the 3 TP=1 patches). The script stays as the emergency PVC-rebuild fallback.

## Stage 1 — build infra (this PR, no production impact)

- `docker/sglang-rdna4/` — Dockerfile (v0.5.14 + 3 TP=1 patches), entrypoint, README.
- `.github/workflows/build-sglang-rdna4.yaml` — builds + pushes `v0.5.14-gfx1201`.
- `kubernetes/apps/actions-runner-system/.../runners/gpu-builder/` — scale-to-zero ARC runner
  on control-1 (root + privileged + GPU passthrough; reuses `cluster-runner-secret`).

The build workflow is **`workflow_dispatch`-only** — merging deploys only the scale-to-zero
`gpu-builder` runner (no pod until a build is dispatched), so **merging is zero production impact**.
Every build is a deliberate dispatch inside the maintenance window below.

## Stage 2 — the cutover (one maintenance window)

The build competes with live serving for the GPU node's host RAM + VRAM; a build OOM can leak
VRAM and wedge the node (the failure mode that cost a TrueNAS cold-cycle this cycle). So free
the GPU first and treat build→switch as a single ~30 min window:

1. **Quiesce** — confirm no agent-pr-review CI in flight, then `kubectl -n ai scale deploy/sglang --replicas=0`. Suspend Flux on the HR so it doesn't fight the manual steps: `flux -n ai suspend hr sglang`.
2. **Build** — `gh workflow run build-sglang-rdna4.yaml` (dispatch). Watch `gh run watch`. ~15-20 min. Capture the pushed digest from the run summary (`…@sha256:…`).
3. **Pin** — set the sglang HelmRelease image to `ghcr.io/tanguille/sglang-rdna4@<digest>`,
   command path `/cache/sglang/repo-v0514/scripts/launch.sh` → `/opt/rdna4-inference/scripts/launch.sh`,
   and `CONDA_BASE: /cache/sglang/conda` → `/opt/conda`. Keep the PVC mount (model `/cache/hf` +
   Triton `/cache/sglang/triton`) and every other flag identical. Open as Stage 2 PR.
4. **Deploy** — merge Stage 2, `flux -n ai resume hr sglang`, watch the pod. First boot recompiles
   the Triton JIT cache onto the PVC (the startup probe's ~16 min budget covers it).
5. **Validate** — `/health` up, a `qwen-3.6` completion with tools+thinking, PPL/needle spot-check, decode tok/s parity with the PVC baseline (≈16 single / ≈99 @conc24).
6. **Retire** — drop the "Runtime-from-PVC" section from the app README; keep `sglang-env-rebuild.sh`
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
