# NVIDIA GPU support on control-1 Implementation Plan

**Created:** 2026-11-14
**Status:** In progress. Chunks 1-5 done on the branch; live rollout (CI dispatch, upgrade, apply) is pending the owner, see `docs/nvidia-gpu-control-1.md`. Keep this file until the rollout is done.
**Complexity:** Very High
**Estimated Chunks:** 5
**Branch:** `feat/nvidia-gpu-operator`
**Worktree:** `.claude/worktrees/nvidia-gpu-operator` (copy `.mcp.json .env CLAUDE.local.md .vscode/ .claude/` in; remove worktree and branch when done)

---

## Overview

Re-introduce the NVIDIA stack for the RTX 2070 (TU106) that is now passed through to the control-1
TALOS VM next to the R9700, and modernise what #3167 removed on 2026-06-10: NVIDIA GPU Operator
(driver and toolkit disabled) with time-sliced device plugin, plus the Talos side. The Talos side is
the hard part: all three nodes run a self-built installer (`v1.14.6-k7.3.4`, kernel 7.3.4), so the
stock `nvidia-open-gpu-kernel-modules-production` extension cannot load (`module.sig_enforce=1`,
fresh kernel signing key per build). The modules must be built against our kernel in our pipeline.

The 2070 is additive: media and LLM on the R9700 must keep working unchanged. Wiring jellyfin,
fileflows or any inference workload onto `nvidia.com/gpu` is **out of scope** (follow-up PRs).

### Owner decisions (2026-11-14, do not re-litigate)

1. Kernel: build the NVIDIA open modules against our custom kernel by extending `docker/talos-kernel`
   and `scripts/talos-kernel-build.sh`. Not the stock kernel, not manifests-only.
2. Workloads: media transcoding and small inference share the card by time-slicing.
3. Allocation: GPU Operator + device plugin, not the NVIDIA DRA driver (DRA claims are
   namespace-scoped and exclusive; see memory `project-dra-amdgpu-timeslicing`).
4. Rollout: manual `talosctl upgrade` on control-1 only, owner-run. Never push or open the PR
   without explicit OK. No Claude attribution in commits.

### Verified facts the plan relies on

| Fact | Evidence |
|------|----------|
| Pinned installer is `v1.14.6-k7.3.4` | `grep -rh "k7" kubernetes talos` shows `version: v1.14.6-k7.3.4` |
| NVIDIA modules are compiled in **siderolabs/pkgs**, not in the extensions repo | `gh api .../extensions/contents/nvidia-gpu/nvidia-modules/production/pkg.yaml?ref=v1.14.6`: extension only copies `/usr/lib/modules/*` out of image `${PKGS_PREFIX}/nvidia-open-gpu-kernel-modules-production-pkg:${PKGS}` |
| Extensions Makefile has targets `nvidia-open-gpu-kernel-modules-production` and `nvidia-container-toolkit-production` | `gh api .../extensions/contents/Makefile?ref=v1.14.6` |
| Our pipeline today rebuilds only `amdgpu` through `make -C extensions amdgpu PKGS_PREFIX=... PKGS=<tag>` | `scripts/talos-kernel-build.sh:115-122` |
| Our kernel image (`docker/talos-kernel/Dockerfile`) exports only `/boot`, `/usr/lib/modules`, spdx; its builder stage holds the full built tree at `/src` and the per-build signing key | Dockerfile lines 50-98 (builder stage) and 100-101 (export) |
| Installer tags are never reused: the build no-ops if the tag exists; the node lives in the repo path (`installer/control-1`), not the tag | README "Version tagging", build script lines 54-56 |
| Old setup (before #3167): chart `oci://ghcr.io/home-operations/charts-mirror/nvidia-gpu-operator` tag 26.3.2, `driver.enabled=false`, `toolkit.enabled=false`, `hostPaths.driverInstallDir=/usr/local`, `devicePlugin` time-slicing `replicas: 8`, RuntimeClass `nvidia`, namespace with `pod-security.kubernetes.io/enforce: privileged` | `git show 5e797a51b^:kubernetes/apps/gpu-operator/...` |
| Latest upstream operator release is v26.7.2 (2026-10-14) | `gh api repos/NVIDIA/gpu-operator/releases` |
| The repo has no Node Feature Discovery deployment, so the operator's bundled NFD is what labels the node | `grep -rli node-feature-discovery kubernetes` found nothing |
| The old repo flip-flopped `runtimeClassName: nvidia` vs `nvidia-cdi` (commits `cee8955e8`, `e4aeac891`) | `git log --grep nvidia` |
| At Talos v1.14.6 the extensions repo pins production driver 595.112.07, LTS 580.196.07, toolkit v1.19.1 (`extensions/nvidia-gpu/vars.yaml`) | review of the plan, upstream read |
| The extension's `nvidia.conf` blacklists `nvidia`, `nvidia_uvm`, `nvidia_drm`, `nvidia_modeset`, so they must be loaded explicitly by machine config | extensions repo, nvidia-modules `nvidia.conf` |
| The toolkit extension ships the GSP firmware at `/usr/lib/firmware/nvidia/<driver-version>` and needs sysctl `net.core.bpf_jit_harden: 1` | toolkit README at v1.14.6 |
| `machine.kernel.modules` is deprecated in 1.14; use one `KernelModuleConfig` document per module | `v1alpha1_types.go:2521` |
| tuppr compares the node's `Version()` to the CR as a plain string; `pinned` is the CR value for every node | tuppr README "Version tagging", `.justfile:46-50` |
| The old app also had `prometheusrule.yaml` (HighGpuUsage) | `git show 5e797a51b^` |
| `cluster-apps` points at `./kubernetes/apps` with no root kustomization; there is no control-plane taint (`allowSchedulingOnControlPlanes: true`) | repo |

### Known risks (read before executing)

- **R1 (CLEARED by Chunk 1, see Spike results; 595.112.07 compiles on 7.3.4):** NVIDIA open modules may not compile against kernel **7.3.4**.
  UNVERIFIED for production 595.112.07 (upstream issues #1507 and #1512 describe 7.3 breakage on
  615.x). If it does not build, stop and return to the owner. LTS 580 is **not** a fallback (older, so
  less likely to build) and a newer branch would mismatch the toolkit's 595 libraries and GSP
  firmware (R4). The options are a backported patch to 595.112.07 or a stock-kernel control-1.
- **R2:** pkgs' `kernel-build` is an internal bldr stage that `PKGS_PREFIX` cannot replace, and a
  compatible exported stage would put `certs/signing_key.pem` into a registry image (anyone with pull
  access could then sign modules that `sig_enforce` accepts). Route A is struck. Route C (Decision 1)
  compiles in our own builder stage.
- **R3 (DECIDED 2026-11-14, owner: option c, overwrite the tag):** control-1's installer is rebuilt
  and re-pushed under the existing `v1.14.6-k7.3.4` tag; no tuppr or `.justfile` change (tuppr sees an
  identical version string, so it does not revert it). Consequences the executor must handle:
  - The old control-1 image is deleted, so the rollback target is Talos A/B (`talosctl rollback`),
    not "upgrade back". The runbook says so (Chunk 5).
  - The build script no-ops when the tag exists, so only the **control-1** ref is force-rebuilt
    (explicit flag or deleting that one tag first, ask first); `installer/shared` and control-2/3
    refs must be skipped, never re-pushed with a fresh kernel and key (CRIT-2).
  - Same tag, new digest: the nodes and spegel may serve the cached old digest. The upgrade must
    pin by digest (`installer/control-1:v1.14.6-k7.3.4@sha256:...`) and the runbook verifies the
    digest the node pulled.
  - control-2/3 keep the kernel and key of the original build; control-1 gets a new build with a new
    key. Fine for sig_enforce (modules and kernel match per node), but record the divergence in the README.
- **R4:** the toolkit extension ships userspace libraries and GSP firmware whose version must equal
  the kernel modules' driver version (hard requirement for the TU106 open modules). Derive
  `NVIDIA_DRIVER_PRODUCTION_VERSION` and `NVIDIA_PKGS_PRODUCTION_AMD64_SHA256` from
  `extensions/nvidia-gpu/vars.yaml` at `${TALOS_VERSION}` (the script already clones extensions), pass
  them as build-args and `sha256sum -c` the tarball. No hardcoded version, no Renovate pin.
- **R5:** the Talos image build takes about 1-2 h (CI cache is `mode=min`, so a retry repeats the full
  compile); dispatch it on the branch after Chunk 4 only, because the installer is immutable.
- **R6:** `nvidia_drm` adds a second DRM card and renderD node under `/dev/dri` next to the R9700;
  the squat plugin hands the whole `/dev/dri` to jellyfin, fileflows and vllm, so a UI-configured
  `/dev/dri/renderD128` could flip. Chunk 5 verifies the device paths; load `nvidia_drm` with
  `modeset=0` or skip it if the toolkit does not need it (UNVERIFIED).

---

## User Stories

### US-1: The 2070 is schedulable
**As the** cluster owner **I want** `nvidia.com/gpu` allocatable on control-1 **so that** media and
small inference can share the 2070 by time-slicing.

#### Acceptance Criteria
- [ ] After the owner-run upgrade, `kubectl get node control-1 -o jsonpath='{.status.allocatable}'` shows `nvidia.com/gpu` equal to the configured replicas
- [ ] A throwaway pod requesting `nvidia.com/gpu: 1` with `runtimeClassName: nvidia` runs `nvidia-smi` and sees the TU106
- [ ] `amd.com/gpu` allocation and R9700 workloads are unaffected
- [ ] control-2 and control-3 render `No changes.` in `just talos diff-node`

### US-2: The modules survive kernel bumps
**As the** owner **I want** the NVIDIA modules rebuilt by the same pipeline as amdgpu **so that** a
Renovate kernel bump does not strand control-1.

#### Acceptance Criteria
- [ ] `scripts/talos-kernel-build.sh` publishes the NVIDIA extension signed by the same kernel build
- [ ] README documents the new artifact and per-bump check

---

## Technical Analysis

### New Files
| File | Purpose |
|------|---------|
| `kubernetes/apps/gpu-operator/kustomization.yaml` | Namespace-level Kustomization (components/common), as before #3167 |
| `kubernetes/apps/gpu-operator/gpu-operator/ks.yaml` | Flux Kustomization |
| `kubernetes/apps/gpu-operator/gpu-operator/app/{kustomization,namespace,ocirepository,helmrelease}.yaml` | Operator and privileged PSA namespace; the `nvidia` RuntimeClass is created by the operator (`operator.runtimeClass: nvidia`), not by a manifest |
| `docs/llm-hosting/` or `docs/` NVIDIA runbook (path decided in Chunk 5) | Rollout and rollback |

### Modified Files
| File | Changes |
|------|---------|
| `docker/talos-kernel/Dockerfile` | Route C: compile the NVIDIA modules in the existing `builder` stage into `/rootfs/usr/lib/modules/<rel>/extra/` |
| `scripts/talos-kernel-build.sh` | Derive driver version/sha, derive the `-pkg` image, build the extension, map the schematic extension in the `while read ext` loop, tag rule from R3 |
| `docker/talos-kernel/README.md` | Pipeline table, "will bite" notes |
| `.github/workflows/talos-kernel.yaml` | Only if trigger paths or inputs need the new files |
| `talos/nodes/controlplane/control-1.schematic.yaml` | Add `siderolabs/nvidia-container-toolkit-production` and `siderolabs/nvidia-open-gpu-kernel-modules-production` (the script swaps the latter for our build, like amdgpu) |
| `talos/nodes/controlplane/control-1.yaml.j2` | One `KernelModuleConfig` per module, `SysctlConfig` `net.core.bpf_jit_harden: "1"`, node label `nvidia.com/gpu: "true"` |
| `talos/README.md:75`, `talos/AGENTS.md:16`, root `README.md:81,94,98` | Stale "only dGPU" and hardware-table statements (now two GPUs) |

### Conventions Checklist
- [ ] `.yaml` extension only, block-style YAML, terse comments, no em dashes
- [ ] Round resource values; requests just below measured average, no guesses
- [ ] Versions, digests and field names from upstream (chart values, extensions repo), never memory
- [ ] Renovate covers the OCIRepository tag and any new pinned versions (check `.renovaterc*`)
- [ ] `docs/` updated for every renamed or changed behavior; grep for stale references (`amd.com/gpu`-only comments in `talos/` that say "only dGPU")
- [ ] Shell changes: `set -euo pipefail`, shellcheck clean
- [ ] No SOPS secrets touched; no push without OK

---

## Decisions & Trade-offs

### Decision 1: Module build route (Route C chosen by the plan review)
1. Route A (struck): build pkgs' `-pkg` image against a `kernel-build` derived from our kernel. Needs pkgs patched and would publish the signing key.
2. Route C: compile in our Dockerfile `builder` stage (`make LD=ld.lld OBJDUMP=llvm-objdump SYSSRC=/src`, the ThinLTO flags pkgs uses), install to `/rootfs/usr/lib/modules/<rel>/extra/` (not in `modules-amd64.txt`, so installer-base ignores it), derive `${PREFIX}/nvidia-open-gpu-kernel-modules-production-pkg:${VERSION}` from the kernel image holding only `extra/*.ko`, then `make -C extensions nvidia-open-gpu-kernel-modules-production PKGS_PREFIX=... PKGS=${VERSION}` exactly like amdgpu. Same build graph and key, upstream validator and manifest for free. Never a second `docker buildx build --target`: a builder cache miss regenerates the key and the modules silently fail to load.

**Chosen:** Route C. Chunk 1 confirms it compiles.

### Decision 2: Operator vs DRA
**Chosen:** GPU Operator + device plugin (owner). Rationale: cross-namespace sharing by time-slicing.

### Decision 3: Driver version
**Chosen:** production, version read from `extensions/nvidia-gpu/vars.yaml` at the Talos tag. If it does not compile on 7.3.4, stop (R1); LTS is not a fallback.

---

## Dependencies

### Prerequisites
- [x] 2070 passed to the TALOS VM (PCI functions 2e:00.0-.3), done 2026-11-14 on the TrueNAS host
- [ ] Host reboot test on `.27` finished and Ceph `HEALTH_OK` (control-1 is rebooted by the final rollout)
- [ ] `ghcr.io/tanguille/*` packages remain public where nodes pull them

### External Dependencies
- NVIDIA open-gpu-kernel-modules release, siderolabs/pkgs and siderolabs/extensions at the pinned Talos tag, NVIDIA gpu-operator chart (mirror: `ghcr.io/home-operations/charts-mirror/nvidia-gpu-operator`, verify the mirror carries the chosen tag)

---

## Execution Plan

### Progress Tracker

#### Wave 1 (sequential) - Spike and decision gate
- [x] Chunk 1: Upstream spike, route and tag decision, compile check on 7.3.4

#### Wave 2 (parallel) - Build pipeline, manifests, node config
- [x] Chunk 2: Kernel pipeline builds and publishes the NVIDIA extension (depends on Chunk 1)
- [x] Chunk 3: Flux manifests for the GPU Operator (depends on Chunk 1 only for driver/toolkit facts)
- [x] Chunk 4: control-1 schematic and machine config (depends on Chunk 1 for names; the script maps the schematic entry by name, no file overlap with Chunk 2)

#### Wave 3 (sequential) - Build dispatch, runbook and validation
- [x] Chunk 5: Runbook, docs pass, full validation (depends on Chunks 2, 3, 4); the CI dispatch (step 1) is held for the owner and lives in the runbook

### Wave Conflict Matrix
| Chunk | Write Set | Dependencies | Wave |
|-------|-----------|--------------|------|
| 1 | `docs/plans/nvidia-gpu-operator-control-1.md` (decision records only) | None | 1 |
| 2 | `docker/talos-kernel/**`, `scripts/talos-kernel-build.sh`, `.github/workflows/talos-kernel.yaml` | Chunk 1 | 2 |
| 3 | `kubernetes/apps/gpu-operator/**` | Chunk 1 | 2 |
| 4 | `talos/nodes/controlplane/control-1.schematic.yaml`, `talos/nodes/controlplane/control-1.yaml.j2`, `talos/README.md`, `talos/AGENTS.md`, root `README.md` (no `.justfile` or tuppr CR change under R3 option c) | Chunk 1 | 2 |
| 5 | `docs/**` (runbook), `.agents/learned-workspace.md` (only if owner OKs), plan file | Chunks 2, 3, 4 | 3 |

Wave 2 chunks have disjoint write sets (`docker`+`scripts`+workflow, `kubernetes/apps/gpu-operator`, `talos`+READMEs). Under R3 option c no `.justfile` or tuppr CR change is needed.

---

### Chunk 1: Upstream spike and decision gate
**Status:** Complete
**Wave:** 1
**Estimated Context:** ~60k tokens
**Complexity:** High

#### Scope
Read-only research plus a local compile test. Writes only decision records into this plan.

#### Prerequisites
None (Wave 1). Create the worktree and run `git pull --ff-only && git status` first.

#### Detailed Steps

1. **Pin the facts from upstream**
   - Read `siderolabs/pkgs` at the `PKGS` pin from the Talos v1.14.6 Makefile: the `nvidia-open-gpu-kernel-modules-production` package (`pkg.yaml`, its `kernel-build` dependency, the driver version and download URL).
   - Read the extensions manifests for `nvidia-container-toolkit-production` and `nvidia-open-gpu-kernel-modules-production` at v1.14.6: required driver version, required `machine.kernel.modules`, any sysctls, and how the toolkit expects the driver at `/usr/local`.
   - Confirm TU106 (Turing) is supported by the open modules for the chosen driver version (NVIDIA release notes).
2. **Compile check on 7.3.4 (R1)**
   - Using the kernel build tree from a local or CI build of `docker/talos-kernel` (or its builder stage), compile the chosen driver version's open modules with `SYSSRC` pointing at the tree.
   - Build with `docker buildx build --target builder --load -t kbuild ...` (the script's build-args), then `docker run kbuild make -C /nvidia/kernel-open LD=ld.lld OBJDUMP=llvm-objdump SYSSRC=/src`; keep the log in the plan.
   - If it fails, record the error and stop; report to the owner (R1: backported patch or stock-kernel control-1; LTS is not a fallback). Do not continue to Chunk 2.
3. **Confirm Route C (Decision 1)** and write down the exact build commands and the extension rootfs layout (the validator contract in the extensions repo `pkg.yaml`: `/usr/lib/modules`, `/usr/local/lib/modprobe.d/nvidia.conf`, `manifest.yaml`).
4. **Apply R3 (option c)**: confirm how `talosctl upgrade` and containerd/spegel resolve a re-pushed tag (digest pinning), and design the control-1-only forced rebuild. Record the exact mechanism.
5. **Record** everything above in a "Spike results" section of this plan.

#### Verification
- [x] The modules compiled against the 7.3.4 tree (log kept), or the plan is halted with the error recorded
- [x] Driver version in the module build equals the version the toolkit extension carries (R4)
- [x] Spike results section lists route, driver version, extension layout, tag rule, required modules and sysctls

#### Files Changed Summary
```
docs/plans/nvidia-gpu-operator-control-1.md  (spike results only)
```

#### Continuation Prompt
> Continue the NVIDIA GPU plan from `docs/plans/nvidia-gpu-operator-control-1.md`.
> 1. Verify branch `feat/nvidia-gpu-operator` and worktree `.claude/worktrees/nvidia-gpu-operator`, then `git pull --ff-only && git status`
> 2. Read "Spike results" and confirm the compile check passed
> 3. Mark Chunk 1 complete and proceed with Chunks 2 and 3 (parallel)

---

### Chunk 2: Kernel pipeline builds the NVIDIA extension
**Status:** Complete
**Wave:** 2
**Estimated Context:** ~80k tokens
**Complexity:** High

#### Scope
- `docker/talos-kernel/Dockerfile`, `scripts/talos-kernel-build.sh`, `docker/talos-kernel/README.md`, `.github/workflows/talos-kernel.yaml` (only if needed)

#### Prerequisites
Chunk 1 complete and its compile check passed.

#### Detailed Steps

1. **Route C in the Dockerfile**: derive driver version and tarball sha from `extensions/nvidia-gpu/vars.yaml` at `${TALOS_VERSION}` (build-args like `TOOLS_REV`), `sha256sum -c` the tarball, compile in the existing `builder` stage with the same key, install to `/rootfs/usr/lib/modules/<rel>/extra/`.
2. **Publish** `${PREFIX}/nvidia-open-gpu-kernel-modules-production-pkg:${VERSION}` (only `extra/*.ko`) next to the kernel image, then build the extension with `make -C extensions nvidia-open-gpu-kernel-modules-production PKGS_PREFIX=... PKGS=${VERSION}` and discover its published tag as the amdgpu step does (never assumed).
3. **Imager input**: map `siderolabs/nvidia-open-gpu-kernel-modules-production` from the schematic to our extension in the script's `while read ext` loop, exactly like `siderolabs/amdgpu`. control-1-only scoping falls out of the schematic; no node conditionals.
4. **Tag rule (R3, option c)**: add an explicit control-1-only force flag. The current pre-check is all-or-nothing and a partial rebuild would re-push `installer/shared` and control-2/3 with a fresh kernel and key, so the publish loop must skip every ref that `crane manifest` already resolves except the forced one. Mind that control-1's kernel and signing key then differ from the other nodes' (README note).
5. **README**: pipeline diagram, why the NVIDIA extension must be rebuilt per kernel build, per-bump check. The module extension and `-pkg` images are imager-only intermediates: keep them private like amdgpu; only `installer/control-1` must be public and already is.
6. **Shell hygiene**: `set -euo pipefail`; statuses in command substitutions checked explicitly (see `.agents/common-operations.md` section on statuses `set -e` does not see).

#### Verification
- [ ] `mise exec -- shellcheck scripts/talos-kernel-build.sh` clean
- [ ] `bash .agents/skills/pr-review/scripts/validate-pr.sh` passes
- [ ] Local `docker buildx build` of the kernel stage including the module compile succeeds and `extra/*.ko` carry the module signature trailer (CI dispatch is in Chunk 5, after Chunk 4)
- [ ] A simulated forced rebuild of control-1 does not re-push `installer/shared` or the control-2/3 refs

#### Files Changed Summary
```
docker/talos-kernel/Dockerfile      NVIDIA module build/stage
docker/talos-kernel/README.md       pipeline + gotchas
scripts/talos-kernel-build.sh       build, publish, control-1 imager input, tag rule
.github/workflows/talos-kernel.yaml (only if required)
```

#### Continuation Prompt
> Continue the NVIDIA GPU plan from `docs/plans/nvidia-gpu-operator-control-1.md`. Verify branch and worktree, `git pull --ff-only && git status`, mark Chunk 2 complete, proceed with Chunk 4 once Chunk 3 is also complete.

---

### Chunk 3: Flux manifests for the GPU Operator
**Status:** Complete
**Wave:** 2
**Estimated Context:** ~50k tokens
**Complexity:** Medium

#### Scope
- `kubernetes/apps/gpu-operator/**` (adding `kubernetes/apps/gpu-operator/kustomization.yaml` is enough; there is no root apps kustomization)

#### Prerequisites
Chunk 1 complete (driver/toolkit facts). Pattern to follow: `kubernetes/apps/kube-system/generic-device-plugin/` and the pre-#3167 files (`git show 5e797a51b^:kubernetes/apps/gpu-operator/...`).

#### Detailed Steps

1. **Namespace**: `gpu-operator`, `pod-security.kubernetes.io/enforce: privileged`, same prune/volsync annotations as the old file.
2. **OCIRepository**: chart `nvidia-gpu-operator` from `ghcr.io/home-operations/charts-mirror`; pick the newest tag the mirror carries (`crane ls ... | grep -E '^[0-9]+\.'`), compare with upstream v26.7.2, and keep Renovate coverage.
3. **HelmRelease**: values taken from the chart's own `values.yaml` at the chosen tag, never from the old file. Keep `driver.enabled: false`, `toolkit.enabled: false`, `hostPaths.driverInstallDir: /usr/local`. Time-slicing: `nvidia.com/gpu` replicas 8, `renameByDefault: false`. Set `cdi.enabled` explicitly and record why (the old repo flip-flopped between `nvidia` and `nvidia-cdi`). With CDI enabled the operator also creates `nvidia-cdi` and `nvidia-legacy` RuntimeClasses whose handlers do not exist on Talos (`10-nvidia-container-runtime.part` registers only `nvidia`); workloads and operands use runtimeClass `nvidia` only. Verify the device-plugin `DEVICE_LIST_STRATEGY` matches `/run/cdi/nvidia.yaml` from `nvidia-cdi-gen`. Keep dcgm-exporter with the `prometheus: victoria-metrics` ServiceMonitor label; round resource values.
4. **NFD**: the repo has no Node Feature Discovery, so keep the bundled NFD enabled and confirm it does not need PSA exceptions beyond the namespace's privileged label.
5. **Scheduling**: the device-plugin, dcgm, gfd and validator operands must select `nvidia.com/gpu.present`. The bundled `nfd-worker` DaemonSet runs on every node by design; accept that. No tolerations (there is no control-plane taint).
6. **RuntimeClass**: do not ship `runtimeclass.yaml`; the operator owns `nvidia` (`operator.runtimeClass: nvidia`) with a controller ownerRef and updates it every pass, so Flux would fight it.
7. **Netpol/Cilium**: check whether namespaces need CiliumNetworkPolicy to match repo defaults (see `common` component) and whether dcgm scraping needs a grant.
8. **Grafana dashboard**: reuse the removed `grafanadashboard.yaml` shape only if the dashboard source still exists upstream.
9. **PrometheusRule**: the old app had `prometheusrule.yaml` (HighGpuUsage); restore it on the DCGM metrics or skip it explicitly and say so in the PR body.

#### Verification
- [ ] `mise exec -- flate`/`kustomize build` of the new path renders; `bash .agents/skills/pr-review/scripts/validate-pr.sh` passes (it renders the Helm chart)
- [ ] The operands (device-plugin, dcgm, gfd, validator) select `nvidia.com/gpu.present`; nfd-worker runs everywhere by design
- [ ] Renovate would pick up the new OCIRepository tag (dry check against `.renovaterc*` rules)
- [ ] Flux dry run: nothing is applied to the live cluster by this chunk

#### Files Changed Summary
```
kubernetes/apps/gpu-operator/**   operator, namespace, ks (no runtimeclass)
```

#### Continuation Prompt
> Continue the NVIDIA GPU plan from `docs/plans/nvidia-gpu-operator-control-1.md`. Verify branch and worktree, `git pull --ff-only && git status`, mark Chunk 3 complete, proceed with Chunk 4 (needs Chunks 1, 2).

---

### Chunk 4: control-1 schematic and machine config
**Status:** Complete
**Wave:** 2
**Estimated Context:** ~40k tokens
**Complexity:** Medium

#### Scope
- `talos/nodes/controlplane/control-1.schematic.yaml`, `talos/nodes/controlplane/control-1.yaml.j2`, `talos/README.md`, `talos/AGENTS.md`, root `README.md`

#### Prerequisites
Chunk 1 complete (extension names, module list). Does not need Chunk 2: the script maps the schematic entry by name.

#### Detailed Steps

1. **Schematic**: add `siderolabs/nvidia-container-toolkit-production` (userspace, reused by digest) and `siderolabs/nvidia-open-gpu-kernel-modules-production`, the latter with a comment that the build script swaps in our own build like `siderolabs/amdgpu`. Schematic files are complete overrides, not deltas.
2. **Machine config**: one `KernelModuleConfig` document per module (`nvidia`, `nvidia_uvm`, `nvidia_drm`, `nvidia_modeset`; `machine.kernel.modules` is deprecated; the extension blacklists them so they must be loaded explicitly; consider `nvidia_drm` with `modeset=0`, R6), a `SysctlConfig` document with `net.core.bpf_jit_harden: "1"` (toolkit README at v1.14.6; confirm in `render-config` that the node layer merges with the cluster one, see `cluster.yaml.j2:162`), and `nodeLabels: nvidia.com/gpu: "true"` beside `amd.com/gpu: "true"`. Update the file header comment, which says "Only node with the dGPU (R9700)".
3. **Grep for stale statements**: `grep -rn "only dGPU\|only node with the dGPU" talos docs .agents README.md` and fix every comment that is now wrong, at least `talos/README.md:75`, `talos/AGENTS.md:16` and root `README.md:81,94,98` (two GPUs now).
4. **Render**: `just talos render-config` for all nodes; `just talos diff-node` only as a read-only dry run, and only after the owner OKs touching live nodes. Ask first.

#### Verification
- [ ] Rendered control-2/control-3 configs are byte-identical to `main`
- [ ] Rendered control-1 diff is limited to the NVIDIA additions
- [ ] `bash .agents/skills/pr-review/scripts/validate-pr.sh` passes

#### Files Changed Summary
```
talos/nodes/controlplane/control-1.schematic.yaml  nvidia toolkit extension
talos/nodes/controlplane/control-1.yaml.j2         modules, label, comments
```

#### Continuation Prompt
> Continue the NVIDIA GPU plan from `docs/plans/nvidia-gpu-operator-control-1.md`. Verify branch and worktree, `git pull --ff-only && git status`, mark Chunk 4 complete, proceed with Chunk 5.

---

### Chunk 5: Runbook, docs and full validation
**Status:** Complete (live steps held: CI dispatch, diff/upgrade/apply are owner-run per `docs/nvidia-gpu-control-1.md`)
**Wave:** 3
**Estimated Context:** ~40k tokens
**Complexity:** Medium

#### Scope
- A runbook under `docs/`, plus consolidating this plan into existing docs; `.agents/learned-workspace.md` only with the owner's OK

#### Prerequisites
Chunks 2, 3, 4 complete.

#### Detailed Steps

1. **CI dispatch** (after Chunk 4, ask first): `gh workflow run talos-kernel.yaml --ref feat/nvidia-gpu-operator`, `gh run watch --exit-status`; verify the control-1 installer is pullable with the token-based `curl` check in the README (200), not bare `crane`.
2. **Runbook** (owner-run, every live step ask-first), in this order:
   1. `just talos diff-node` on all nodes, expecting `No changes.` on control-2/3.
   2. Preconditions: Ceph `HEALTH_OK`, no remapped PGs, no other OSD down or degraded and no recent flaps on control-2/3 (size 2 / min_size 1 means osd.3's PGs run on one copy for the whole window; a passthrough VM boot with a new GPU function can stall).
   3. `talosctl upgrade --image ghcr.io/tanguille/installer/control-1:<tag> -m powercycle` on control-1 last pinned by digest (R3 option c), then verify the digest the node pulled.
   4. The upgrade boots with the old machine config, so the modules stay blacklisted: `just talos diff-node control-1 192.168.0.11`, then `apply-node` (owner OK), reboot if Talos says one is needed. Do not apply the `KernelModuleConfig` before the image has the modules (UNVERIFIED whether that fails boot).
   5. Post-checks: node allocatable shows `nvidia.com/gpu`; an `nvidia-smi` test pod with `runtimeClassName: nvidia` sees the TU106; DCGM metrics in VictoriaMetrics; `talosctl -n 192.168.0.11 ls /sys/class/drm` plus a per-node driver map, and the jellyfin VAAPI device path unchanged (R6).
3. **Rollback**: primary path is `talosctl -n 192.168.0.11 rollback` (A/B; the previous kernel and extensions are self-consistent under `sig_enforce`). "Upgrade back to the previous tag" is not possible under R3 (c): the old image is overwritten. Remove the Flux Kustomization to stop the operator; control-2/3 are never touched.
4. **Docs pass**: grep for every old name or behavior this change alters; update comments and docs; follow the plan's Process Instructions below.
5. **Final validation**: `bash .agents/skills/pr-review/scripts/validate-pr.sh`, `git diff --check`, shellcheck on touched `*.sh`.

#### Verification
- [ ] Every local reference in changed docs exists
- [ ] `validate-pr.sh` passes on the whole branch
- [ ] Commits are Conventional Commit titles, no attribution lines, nothing pushed

#### Files Changed Summary
```
docs/<runbook>.md   rollout and rollback
```

#### Continuation Prompt
> Finish the NVIDIA GPU plan from `docs/plans/nvidia-gpu-operator-control-1.md`. Verify branch and worktree, `git pull --ff-only && git status`, run the final validation, then ask the owner before any push or PR.

---

## Spike results (Chunk 1, 2026-11-14)

**Verdict: R1 cleared. NVIDIA open modules 595.112.07 compile against kernel 7.3.4 (Talos v1.14.6 toolchain). Route C works.**

### Pins (all read from upstream, nothing from memory)

| Item | Value | Source |
|------|-------|--------|
| Talos | `v1.14.6` | `.justfile` / tuppr CR |
| `TOOLS` | `v1.14.0-12-g5959f76` | `siderolabs/talos@v1.14.6` Makefile |
| `PKGS` | `v1.14.0-59-ge6ad19d` (`PKGS_SHA=e6ad19d`) | same Makefile (also `siderolabs/extensions@v1.14.6` Makefile) |
| Driver (production) | `595.112.07` | `extensions/nvidia-gpu/vars.yaml@v1.14.6`, `NVIDIA_DRIVER_PRODUCTION_VERSION` |
| amd64 tarball sha256 | `3ea206a1f7536703bec86fa83708dcbe153aada74f63c83ebeb5bc3995508f49` | same file, `NVIDIA_PKGS_PRODUCTION_AMD64_SHA256` (sha512 `d5c63603...fd90` in the same file) |
| Tarball URL | `https://developer.download.nvidia.com/compute/nvidia-driver/redist/nvidia_driver/linux-x86_64/nvidia_driver-linux-x86_64-595.112.07-archive.tar.xz` | `siderolabs/pkgs@e6ad19d` `nvidia-open-gpu-kernel-modules/production/pkg.yaml` |
| Toolkit extension (stock, by digest) | `ghcr.io/siderolabs/nvidia-container-toolkit-production:595.112.07-v1.19.1@sha256:44b192dd...bec8e` | `crane export ghcr.io/siderolabs/extensions:v1.14.6 - \| tar -xO image-digests` |
| Stock module extension (to be replaced) | `ghcr.io/siderolabs/nvidia-open-gpu-kernel-modules-production:595.112.07-v1.14.6@sha256:9c8e48eb...5785f` | same file |
| Newest upstream driver | 620.70.06 (2026-11-05); production branch at this Talos tag stays 595.112.07 | `gh api repos/NVIDIA/open-gpu-kernel-modules/releases` |

R4 holds by construction: the toolkit extension (`nvidia-pkgs-production`) and the module package both consume `NVIDIA_DRIVER_PRODUCTION_VERSION` from the same `vars.yaml`; the toolkit tag `595.112.07-v1.19.1` and the module tag `595.112.07-v1.14.6` carry the same driver. The tarball sha256 and sha512 both verified locally (`sha256sum -c`, `sha512sum -c`: OK).

### Compile check (R1)

Machine had podman 6.1.3, no docker/buildx. Everything below ran locally, no cluster or host access.

```sh
# kernel builder stage, same build-args as scripts/talos-kernel-build.sh (KERNEL_VERSION default 7.3.4)
cd docker/talos-kernel
podman build --target builder \
  --build-arg KERNEL_VERSION=7.3.4 --build-arg TOOLS_REV=v1.14.0-12-g5959f76 --build-arg PKGS_SHA=e6ad19d \
  -t nvidia-spike-builder .
# ~25 min on 24 cores, exit 0. config delta: +306 -198. Image 21.3 GB. Kernel release 7.3.4-talos,
# clang/ld.lld 22.1.8. podman only warns that SHELL is not persisted in OCI format (irrelevant: the
# stage is only used with an explicit `bash -c`).

# modules, flags copied from pkgs nvidia-open-gpu-kernel-modules/production/pkg.yaml
# (env LLVM=1, ARCH=x86_64; make LD=ld.lld OBJDUMP=llvm-objdump -j $(nproc) SYSSRC=/src)
podman run --rm -v <driver-tree>:/nvidia-driver -e LLVM=1 -e ARCH=x86_64 localhost/nvidia-spike-builder \
  bash -c 'cd /nvidia-driver/kernel-open && make LD=ld.lld OBJDUMP=llvm-objdump -j $(nproc) SYSSRC=/src modules'
```

Result: exit 0 in 41 s, zero compiler errors. Success lines:

```text
7.3.4-talos
  MODPOST Module.symvers
  CC [M]  nvidia.mod.o ... nvidia-uvm.mod.o nvidia-modeset.mod.o nvidia-drm.mod.o nvidia-peermem.mod.o
  LD [M]  nvidia-modeset.ko / nvidia-peermem.ko / nvidia-uvm.ko / nvidia-drm.ko / nvidia.ko
  BTF [M] nvidia-peermem.ko / nvidia-modeset.ko / nvidia-drm.ko / nvidia-uvm.ko / nvidia.ko
```

Only warnings: about 13.9k `objtool: ... 'naked' return found in MITIGATION_RETHUNK build` from the closed `nv-kernel.o_binary` and `nv-modeset-kernel.o_binary` blobs (upstream, build still succeeds; `mitigations=off` is in the cmdline anyway), plus a handful of `-Wformat-security` and `-Wexpansion-to-defined`.

Upstream 7.3 issues are runtime-only and do not block the build: NVIDIA/open-gpu-kernel-modules #1507 (RTD3 reports "Not supported" because the `nv-probe` kthread runs in nullfs on 7.3; reported on 615.78.08) and PR #1512 (open, fixes it). Affects runtime power management only; irrelevant for a passthrough VM that never suspends the card. Not a blocker; recheck on a driver bump.

### Signing (Route C, local proof)

The kernel's `.config` has `CONFIG_MODULE_SIG=y`, `MODULE_SIG_ALL=y`, `MODULE_SIG_SHA512=y`, `MODULE_SIG_KEY="certs/signing_key.pem"`. NVIDIA's own `modules_install` signs with the builder's key, no extra step:

```sh
make LD=ld.lld OBJDUMP=llvm-objdump -j $(nproc) modules_install SYSSRC=/src \
  INSTALL_MOD_PATH=/nvroot/usr INSTALL_MOD_DIR=extra INSTALL_MOD_STRIP=1
# SIGN /nvroot/usr/lib/modules/7.3.4-talos/extra/nvidia{,-uvm,-modeset,-drm,-peermem}.ko
```

- All 5 `.ko` carry `~Module signature appended~` (the `grep -FL` check pkgs runs printed nothing).
- `modinfo`: `signer: Build time throw-away kernel key`, `sig_key: 09:AD:C3:76:...`, `sig_hashalgo: sha512`, `vermagic: 7.3.4-talos SMP preempt mod_unload modversions`, `version: 595.112.07`.
- **Plan correction:** NVIDIA's `kernel-open/Makefile` sets `INSTALL_MOD_DIR ?= kernel/drivers/video`. Without `INSTALL_MOD_DIR=extra` the modules land in `kernel/drivers/video/`, next to in-tree files. `?=` means passing `INSTALL_MOD_DIR=extra` works (verified). Route C must pass it, as above.
- `modules_install` also runs its own `depmod` and writes `modules.*` files into the prefix. Ignore them: the imager reruns `depmod` over the merged tree (`internal/pkg/extensions/kernel_modules.go`, `GenerateKernelModuleDependencyTreeExtension`), so the extension only needs the `.ko` files under `usr/lib/modules/<rel>/`.
- Dependencies resolve against the real kernel: with `depmod -b /rootfs/usr -F /rootfs/boot/System.map --errsyms -w 7.3.4-talos` (the exact gate Talos uses) on the kernel tree plus `extra/`, no nvidia warning (only the pre-existing in-tree `duplicate module alias` lines). Result: `nvidia-modeset -> nvidia, video, wmi`; `nvidia-drm -> nvidia-modeset, nvidia, video, wmi, drm_ttm_helper, ttm`; `nvidia-uvm -> nvidia`. `video.ko` and `drm_ttm_helper.ko`/`ttm.ko` are in Talos' `hack/modules-amd64.txt`; `wmi.ko` moved to `kernel/drivers/platform/wmi/wmi.ko` in 7.3, which the script's reconcile pass already rewrites. Standalone `depmod` (no System.map) prints `needs unknown symbol acpi_video_register_backlight / drm_fbdev_ttm_driver_fbdev_probe`; that is expected, do not mistake it for a failure.
- Not verified locally (needs the real CI build and a node): that the signed modules actually load under `module.sig_enforce=1`. The signature trailer, the key and the vermagic are all consistent with the kernel they were built in.

### TU106 support

`supported-gpus/supported-gpus.json` in the 595.112.07 tarball lists `NVIDIA GeForce RTX 2070` for devids `0x1F02, 0x1F07, 0x1F10, 0x1F14, 0x1F50, 0x1F54` (and the 2070 Super `0x1E91`), all with feature `kernelopen`, so the open modules support it. GSP firmware `firmware/gsp_tu10x.bin` ships in the same tarball (the toolkit extension installs it to `/usr/lib/firmware/nvidia/595.112.07`). The exact devid the VM sees is UNVERIFIED (no host access); every desktop 2070 devid is covered.

### Route C: exact mechanics

1. **Source stage** (new, like `source`): download the tarball, `sha256sum -c`, extract `*/kernel-open` only. Driver version and sha come in as build-args from `extensions/nvidia-gpu/vars.yaml@${TALOS_VERSION}` (`yq -r .NVIDIA_DRIVER_PRODUCTION_VERSION`, `.NVIDIA_PKGS_PRODUCTION_AMD64_SHA256`); the script already clones `extensions`, so clone it before the kernel build. No hardcoded value, no Renovate pin.
2. **In the `builder` stage, after the kernel `make` and before/independent of the `/rootfs` layout step:** the commands above, with `INSTALL_MOD_PATH=/nv-rootfs/usr`. Same stage, same `signing_key.pem`. Then copy only `/nv-rootfs/usr/lib/modules/<rel>/extra` into `/rootfs/usr/lib/modules/<rel>/` **after** the existing `depmod ... -F System.map` step, so the kernel's own `modules.dep` is untouched. `installer-base` ignores it: the Talos Dockerfile installs `hack/modules-amd64.txt` entries by exact path (`xargs -a modules-amd64.txt install -D`, Talos `Dockerfile` ~L704-722) and runs `depmod --errsyms` on that subset only.
3. **`-pkg` image**: derive it from the pushed kernel image, never from a second `--target` build (builder cache miss means a new signing key and modules that silently fail to load). Needs `${PREFIX}/nvidia-open-gpu-kernel-modules-production-pkg:${VERSION}` holding `/usr/lib/modules/<rel>/extra/*.ko` (extension `pkg.yaml` does `cp -R /usr/lib/modules/* /rootfs/usr/lib/modules`). Two options for Chunk 2, pick the simpler one that works under `docker buildx` on the runner: (a) `crane export "${KIMG}" - | tar -x --wildcards 'usr/lib/modules/*/extra/*'` then `crane append` a single layer onto an empty base and set platform linux/amd64; (b) a 3-line Dockerfile `FROM scratch` + `COPY --from=${KIMG} /usr/lib/modules/<rel>/extra/ /usr/lib/modules/<rel>/extra/` built with `buildx --push` (`<rel>` = `${KERNEL_VERSION}-talos`). Neither was run (no registry write access by design); the `.ko` content they would carry was produced and validated above.
4. **Extension**: `make -C extensions nvidia-open-gpu-kernel-modules-production PUSH=true PLATFORM=linux/amd64 REGISTRY=... USERNAME=... PKGS_PREFIX="${PREFIX}" PKGS="${VERSION}"`. The Makefile tags the image with `bldr eval --target ... '{{.VERSION}}'`, and `vars.yaml` sets `VERSION: "<driver>-<ABBREV_TAG>"`, so it publishes `${PREFIX}/nvidia-open-gpu-kernel-modules-production:595.112.07-v1.14.6`. The script's existing discovery (`git describe` into `EXT_TAG`, `crane ls ... | grep -F -- "-${EXT_TAG}"`) matches that (`-v1.14.6`). Not runnable locally (`make` needs `docker buildx` and bldr).
5. **Imager input**: in the `while read ext` loop add `siderolabs/nvidia-open-gpu-kernel-modules-production` -> `--system-extension-image "${NVIDIA_MODULES_REF}"`; `siderolabs/nvidia-container-toolkit-production` already falls through to the `image-digests` lookup (`ghcr.io/siderolabs/nvidia-container-toolkit-production:595.112.07-v1.19.1@sha256:...` is present there).

### Extension rootfs layout (validator contract)

```text
usr/lib/modules/<rel>/extra/nvidia.ko nvidia-uvm.ko nvidia-modeset.ko nvidia-drm.ko nvidia-peermem.ko
usr/local/lib/modprobe.d/nvidia.conf        # blacklist nvidia, nvidia_uvm, nvidia_drm, nvidia_modeset
manifest.yaml                                # name nvidia-open-gpu-kernel-modules-production, version 595.112.07-v1.14.6, talos >= v1.2.0
```

Proved with the real validator image (`ghcr.io/siderolabs/extensions-validator:c0c37e7`, the one `extensions/internal/base/pkg.yaml` pins): a hand-assembled rootfs from the signed modules above plus the real `nvidia.conf` and a manifest copied from the template passes `extensions-validator validate --rootfs=... --pkg-name=nvidia-open-gpu-kernel-modules-production` (rc 0). Rules it enforces (`talos pkg/machinery/extensions/validate.go`): no world-writable or special files, regular files only under `AllowedPaths`. `<rel>` must equal the installed kernel release (`7.3.4-talos`) because the imager copies by kernel version dir.

### Toolkit and sysctl facts (from `siderolabs/extensions@v1.14.6`)

- Machine config needs: `KernelModuleConfig` per module (`nvidia`, `nvidia_uvm`, `nvidia_modeset`, `nvidia_drm`; the extension's `nvidia.conf` blacklists them so they must be loaded explicitly), `SysctlConfig` `net.core.bpf_jit_harden: "1"`, and the CRI runtime `nvidia` handler is registered by the toolkit (`/etc/cri/conf.d/10-nvidia-container-runtime.part`, `BinaryName=/usr/local/bin/nvidia-container-runtime`), so the RuntimeClass handler is `nvidia` only.
- The toolkit expects the driver userspace at `/usr/local` (libs, `nvidia-smi`, `nvidia-modprobe`), firmware at `/usr/lib/firmware/nvidia/595.112.07`, udev rule `15-nvidia-device.rules`. Services: `ext-nvidia-persistenced` (waits for `udevd` and `/sys/bus/pci/drivers/nvidia`), then `ext-nvidia-cdi-gen` writes `/run/cdi/nvidia.yaml`. This matches the plan's `hostPaths.driverInstallDir=/usr/local`, `driver.enabled=false`, `toolkit.enabled=false`.
- `KernelModuleConfig` takes parameters (`KernelModuleSpec.Parameters`, `kernel_module_spec.go`), so `nvidia_drm` can be loaded as `modeset=0`. NVIDIA's default is `modeset=1` in 595.112.07 (`nvidia-drm-os-interface.c:41 nv_drm_modeset_module_param = true`), which creates a second DRM card (R6). With `modeset=0` the module returns early (`nvidia-drm-drv.c:1947`) and registers no DRM device. Nothing in the toolkit extension needs `nvidia_drm`; CUDA, NVENC and NVDEC need `nvidia` and `nvidia_uvm`. Recommendation for Chunk 4: load `nvidia`, `nvidia_uvm`, `nvidia_modeset`, and `nvidia_drm` with `modeset=0` only if the operator validator wants it; default to skipping `nvidia_drm`. Runtime need is UNVERIFIED until the node runs.
- UNVERIFIED item "applying the `KernelModuleConfig` before the image has the modules breaks boot": source says no. `KernelModuleSpecController.Run` collects load errors and returns them, which only makes the controller retry with backoff; it is not a boot sequence phase. Keep the runbook order anyway (upgrade first, then apply).

### R3 option (c): tag overwrite mechanics

- **What `talosctl upgrade` does** (Talos v1.14.6 source): the API server (`v1alpha1_server.go` `Upgrade`) first calls `install.PullAndValidateInstallerImage`, which pulls into the **system** containerd namespace with `WithSkipIfAlreadyPulled`, so it runs `/bin/installer --help` from whatever image already sits under that name. Then `install.RunInstallerContainer` pulls again **without** skip (`PullWithRetriesAndTimeout`, the `Pull` option is deprecated and defaults to true) and removes the image after a successful install. The system containerd lives on tmpfs, so every reboot also empties it. Net effect: for a tag ref a stale cache can only serve the `--help` validation, and only when the same tag was pulled since the last boot (a failed earlier attempt keeps the image on purpose, "so that a retry doesn't have to pull it again"). The actual installer is resolved against ghcr.
- **Spegel is not in this path.** Spegel (`kubernetes/apps/kube-system/spegel`, `containerdRegistryConfigPath: /etc/cri/conf.d/hosts`) mirrors the CRI namespace for kubelet pulls. The Talos installer pull uses Talos' own resolver built from machine registry config (`crires.RegistryBuilder`); there is no `RegistryMirrorConfig` for ghcr in `talos/`. The installer is never a pod image.
- **Digest pinning works and is canonical.** `imageref.Parse` uses `reference.ParseDockerRef` and documents "dropping the tag from a reference carrying both a tag and a digest". So `...installer/control-1:v1.14.6-k7.3.4@sha256:<new>` is handled as `...installer/control-1@sha256:<new>`: a name that cannot hit a stale tag cache. The node log prints that ref (`performing upgrade via "<ref>"`, `pulling "<ref>"`), so `talosctl -n 192.168.0.11 dmesg | grep -E 'performing upgrade|pulling'` proves which digest was requested. After boot, `talosctl -n 192.168.0.11 get extensions` must list `nvidia-open-gpu-kernel-modules-production` (`595.112.07-v1.14.6`) and `talosctl read /proc/version` shows a kernel build timestamp that differs from control-2/3.
- **Recorded before any overwrite** (`crane digest`, 2026-11-14): `installer/control-1:v1.14.6-k7.3.4` = `sha256:0548cb8a4c7f6887f14db7b7f66b7e67a50433974cc1776c366aae67bf4b9bd7` (docker manifest v2, 2 layers); `installer/shared:v1.14.6-k7.3.4` = `sha256:a5649e02f452bfdc844f9927500f841f89c8fea78d8279619240bd3f0bd92ad5` (must stay unchanged after the forced build; a cheap post-check). GHCR keeps an overwritten tag's old manifest as an untagged version until someone deletes it, so `talosctl upgrade --image ghcr.io/tanguille/installer/control-1@sha256:0548cb8a...` stays possible as a second rollback path if that version is not pruned. A/B `talosctl rollback` is still primary. Worth saying in the Chunk 5 runbook. Tag history also shows a precedent for revision tags (`v1.14.2-k7.2.9-r1`), which tuppr would see as a different version string; option (c) deliberately avoids that.
- **Forced rebuild design for `scripts/talos-kernel-build.sh`** (Chunk 2):
  1. `FORCE_NODE="${FORCE_NODE:-}"` (env, set from a `workflow_dispatch` input `force_node`). Validate it is one of `"$@"` and that `installer_ref "${FORCE_NODE}"` is not `.../installer/shared:*` (refuse, exit 1).
  2. Pre-check loop fills `declare -A PUBLISH=()`: a node is added when its ref is missing **or** it is `FORCE_NODE` (log `forced: <ref> (old digest <crane digest>)`). Exit 0 as today when `PUBLISH` is empty. Default behaviour with no flag is unchanged: a merge-triggered run still no-ops because the tag exists, so the overwritten control-1 image survives the merge.
  3. Publish loop: `[[ -n "${PUBLISH[${node}]:-}" ]] || { log "skip ${node}: ${dst} exists"; continue; }` before the imager call. `BUILT[schematic]` dedupe stays. This is what keeps `installer/shared` (control-2/3) and any other existing ref from being re-pushed with the new kernel and key (CRIT-2).
  4. After `crane push`, record `crane digest "${dst}"` in `PUBLISHED` and print it in the step summary, so the owner copies the digest for `talosctl upgrade ...@sha256:`.
  5. Side effect to document in the README: a forced run also overwrites the intermediates tagged `${VERSION}` (`kernel`, `linux-firmware`, `amdgpu`, `nvidia-open-gpu-kernel-modules-production(-pkg)`, `installer-base`, `imager`). Nothing at runtime references them; control-2/3 installers stay on the original build. If a registry `kernel:buildcache` hit reproduces the old builder layers the signing key may even be unchanged (the Dockerfile edit invalidates every layer after the first changed instruction, so expect a new key); either way the control-1 installer is internally consistent.
- Signing divergence: control-1 ends up with a different kernel build and key than control-2/3. Fine under `sig_enforce` (modules and kernel match per node), record it in the README.

### Still UNVERIFIED after the spike

- The signed modules loading on a booted node under `module.sig_enforce=1`, and `nvidia-smi` seeing the TU106 (rollout only).
- `docker buildx` / `bldr` paths (`make -C extensions nvidia-open-gpu-kernel-modules-production`, the `-pkg` derivation, the imager run): not runnable on this machine; Chunk 2 verifies locally with whatever builder it has, CI dispatch is Chunk 5.
- Whether the gpu-operator validator or dcgm need `nvidia_modeset`/`nvidia_drm` (Chunk 3/4/5 runtime check).
- The exact devid the passthrough VM exposes (any desktop RTX 2070 devid is in the supported list).

### Local artifacts (for Chunk 2 reuse)

Kept on this machine: podman image `localhost/nvidia-spike-builder` (21.3 GB, kernel builder stage; the `signing_key` inside is a throw-away from this spike build, not related to any node), driver tarball and extracted tree under the session scratchpad. If Chunk 2 only appends NVIDIA instructions after the kernel `make`, podman's layer cache reuses the 25 min kernel compile. Remove with `podman rmi localhost/nvidia-spike-builder` and `podman image prune` when done.

---

## Testing Strategy

### Unit Tests
- Shell: shellcheck on `scripts/talos-kernel-build.sh`; extend the existing Python/bash tests only where the build script already has them (`scripts/test_*.py` pattern)

### E2E Verification
- [ ] CI build on the branch (dispatched in Chunk 5, after Chunk 4) publishes a pullable control-1 installer (README `curl` check returns 200)
- [ ] After the owner-run upgrade: node allocatable shows `nvidia.com/gpu`, an `nvidia-smi` pod sees the TU106, R9700 workloads unaffected

---

## Rollback Plan

If issues are discovered:
1. Before merge: abandon the branch; nothing is applied to the cluster by the PR itself.
2. After merge, before the node upgrade: remove the `gpu-operator` Flux Kustomization (operator pods only); revert the `talos/` changes (renders only).
3. After the node upgrade: `talosctl -n 192.168.0.11 rollback` (A/B); upgrading back is not possible under R3 (c); nodes control-2/3 were never touched.

---

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the plan's contents are consolidated into existing docs, remove the plan file; if no relevant docs exist, rework the plan into a reference document.

**Important**: Every prompt should verify the branch and worktree before doing any work.

---

## Review Report (opus, 2026-11-14)

Verdict on the first draft: not executable as written. 4 CRIT, 5 HIGH, 6 MED, 5 LOW, 4 AMB, 4 PIT. Facts table confirmed except Dockerfile line numbers (fixed) and the omitted `prometheusrule.yaml` (added as Chunk 3 step 9).

- CRIT-1 [FIXED, owner decision]: any per-control-1 tag is reverted by tuppr (plain string compare). Owner picked option (c), overwrite the tag; R3 records the consequences (no upgrade-back, forced control-1-only rebuild, digest pinning).
- CRIT-2 [FIXED]: the all-or-nothing no-op pre-check could re-push `installer/shared`. Chunk 2 step 4: skip refs `crane manifest` resolves except the forced control-1 ref.
- CRIT-3 [FIXED]: the CI dispatch ran before the schematic change. Moved to Chunk 5 step 1; Chunk 4 joined Wave 2 (depends on Chunk 1 only).
- CRIT-4 [FIXED]: Route A would publish the kernel signing key. Struck; Route C (build in the existing builder stage, derive the `-pkg` image, reuse the extensions target) is Decision 1.
- HIGH-1 [FIXED]: the R1 fallback went the wrong way (LTS 580 is older). Rewritten: stop, backport patch or stock-kernel control-1.
- HIGH-2 [FIXED]: driver version and sha derived from `extensions/nvidia-gpu/vars.yaml` at the Talos tag (R4, Chunk 2 step 1).
- HIGH-3 [FIXED]: runbook now diffs and applies the machine config after the upgrade (Chunk 5 step 2.4).
- HIGH-4 [FIXED]: `runtimeclass.yaml` dropped, operator owns `nvidia`, `cdi.enabled` set explicitly (Chunk 3).
- HIGH-5 [FIXED]: new R6 and a DRM/VAAPI post-check (Chunk 4 step 2, Chunk 5 step 2.5).
- MED-1 to MED-6 [FIXED]: `KernelModuleConfig` and `SysctlConfig` documents, schematic entry mapped by name in the script, NFD verification rewritten, A/B rollback as the primary path, Ceph window preconditions, write sets extended (`talos/README.md`, `talos/AGENTS.md`, root `README.md`).
- LOW-1 to LOW-5 [FIXED]: no root apps kustomization, no tolerations, module/`-pkg` images stay private, PrometheusRule decision, build-time note in R5.
- AMB-1 to AMB-4 [FIXED]: concrete compile command, sysctl value, CDI/runtimeClass settings, dispatch point.
- Still UNVERIFIED, for Chunk 1 and the rollout: that 595.112.07 compiles on 7.3.x, whether applying the `KernelModuleConfig` before the image has the modules breaks boot, and whether `nvidia_drm` is needed by the toolkit.
