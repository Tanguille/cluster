# Forgejo Actions Runner Implementation Plan

**Created:** 2026-11-28
**Status:** Approved
**Complexity:** Medium
**Estimated Chunks:** 3
**Branch:** `feat/forgejo-actions`
**Worktree:** `.claude/worktrees/forgejo-actions` (from `origin/main`; the main checkout is 501 commits behind with unrelated uncommitted work, do not use it)

---

## Overview

Turn on Forgejo Actions and add one runner that starts a pod per job, so Rust repositories hosted on Forgejo get CI. Up to 3 jobs run at once. A cache server on a ceph-block PVC backs `actions/cache`, so `Swatinem/rust-cache` keeps `~/.cargo` and `target/` between runs. Modeled on `eleboucher/homelab` (`kubernetes/apps/forgejo/forgejo-runner`), without Kueue, JuiceFS, S3 or the dind/heavy pod specs.

---

## User Stories

### US-1: Run CI on a Forgejo repository
**As** the owner **I want to** push a repo with `.forgejo/workflows/*.yaml` to Forgejo **so that** its jobs run on the cluster.

#### Acceptance Criteria
- [ ] Actions is enabled; the runner shows Idle under Site administration → Actions → Runners.
- [ ] A workflow with `runs-on: ubuntu-latest` runs in its own pod in namespace `forgejo-runner`, and the pod is deleted afterwards (within 10 min if the runner was aborted: TTL backstop).
- [ ] The `tanguille/cluster` mirror has `has_actions: false`; no mirror sync queues jobs.
- [ ] At most 3 job pods exist at once (`capacity: 3`).

### US-2: Rust builds with a warm cache
**As** the owner **I want** `runs-on: rust` jobs to restore the cargo/target cache **so that** repeat builds skip most compilation.

#### Acceptance Criteria
- [ ] The second run of the same commit logs a `rust-cache` hit and finishes noticeably faster than the first (record both durations).
- [ ] The cache survives a restart of the cache-server pod (data on the PVC).
- [ ] No job pod is OOMKilled in the test build; peak memory is recorded (`kubectl top pod` or VictoriaMetrics `container_memory_working_set_bytes`).

### US-3: Jobs are isolated
#### Acceptance Criteria
- [ ] Job pods run with `hostUsers: false` (`kubectl get pod -o jsonpath='{.spec.hostUsers}'` = `false`, and `/proc/self/uid_map` inside the job is not `0 0 4294967295`).
- [ ] Job pods cannot reach the kube-apiserver, the LAN, or apps in `default` other than Forgejo.

---

## Technical Analysis

### Facts checked while planning (2026-11-28)
| Fact | Source |
|------|--------|
| Upstream runner latest `v13.2.2`; plugin protocol needs ≥ v13.1.0 | `code.forgejo.org/api/v1/repos/forgejo/runner/releases`, plugin README |
| Plugin + chart latest `v13.3.1` (2026-11-23), chart `oci://git.erwanleboucher.dev/eleboucher/charts/forgejo-runner`; chart version = plugin image tag | `git.erwanleboucher.dev/api/v1/repos/eleboucher/runner-k8s-plugin/releases`, `charts/forgejo-runner/Chart.yaml` |
| Plugin repo: single maintainer, no license file, 0 stars. Owner accepted this on 2026-11-28 | repo API (`licenses: null`) |
| Chart values: `forgejo.url`, `forgejo.existingSecret` (keys `token`, `uuid`), `image`, `plugin.image/resources`, `runnerConfig`, `podSpecs`, `podLabels`, `rbac.create`, `resources`. No persistence values: `data` is an emptyDir | `charts/forgejo-runner/values.yaml`, `templates/deployment.tpl` |
| Chart RBAC: Role on `batch/jobs` create/get/list/watch/delete, `pods` get/list/watch, `pods/exec`, `pods/log` in the release namespace | `templates/rbac.tpl` |
| Cluster: Kubernetes v1.37.2, Talos v1.15.0-beta.1, containerd 2.3.7 | `kubectl version`, `kubectl get nodes` |
| Free memory requests: control-1 14.7Gi, control-2 2.8Gi, control-3 5.6Gi | `kubectl describe nodes` (allocatable − requests) |

Re-check every version and digest from the registry at execution time; do not copy them from this table.

### Design
- **Namespace `forgejo-runner`** (new), like `kritika`, which also runs job pods in its own namespace. Job pods run untrusted build code with internet access. In `default` they would pass `default-ingress-allowlist` (intra-namespace allowed) to every app there.
- **Runner**: the eleboucher chart (inline OCIRepository in the HelmRelease, as in `actions-runner-system/.../rsume/helmrelease.yaml`). Runner `capacity: 3`, labels `ubuntu-latest`/`default` → `podspec-default.yaml`, `rust` → `podspec-rust.yaml`.
- **Cache**: a separate app-template HelmRelease `forgejo-cache` in the same Flux Kustomization and `app/` directory as the runner (one Kustomization, no dependsOn between them; the runner retries the cache). It runs `forgejo-runner cache-server --dir /data --port 4000 --secret $CACHE_SECRET` on a 50Gi ceph-block PVC. The runner uses it through `cache.external_server` and proxies it to job pods on `proxy_port: 8089`. We use a separate pod because the chart has no persistence values. No kopiur backup: losing the cache only costs one cold build.
- **Pod specs** (both `hostUsers: false`, `restartPolicy: Never`, image `ghcr.io/bjw-s-labs/forgejo-runner:ubuntu-26.04@sha256:<digest from registry>`, env `TAR_OPTIONS=--no-same-owner`, `DEBIAN_FRONTEND=noninteractive`):
  - default: requests 500m / 512Mi, limit memory 4Gi.
  - rust: requests 2 CPU / 4Gi / 20Gi ephemeral-storage, limit memory 8Gi, no CPU limit, env `CARGO_BUILD_JOBS=4` and `CARGO_INCREMENTAL=0` (incremental artifacts bloat the cache and CI rebuilds from clean anyway). Own `shared` emptyDir with `sizeLimit: 30Gi` (the plugin's default `shared` volume is 10Gi and holds checkout + `target/`).
  - Both specs: `automountServiceAccountToken: false`, `priorityClassName: homelab-optional` (CI never preempts services). Rust also gets **required** nodeAffinity `kubernetes.io/hostname In [control-1]`: control-3 (5.6Gi free) would fit one 4Gi request but not its 8Gi burst.
  - Worst case 3 rust jobs = 12Gi requests / 24Gi limits on control-1 (14.7Gi free requests). Jobs that don't fit stay Pending; `poll_timeout: 1h` so a Pending job waits instead of failing after 10m.
- **Network** (Cilium; namespace components `common`, `deny-lan-egress`, `deny-apiserver-egress`):
  - Runner pod gets label `egress.home.arpa/kube-apiserver: allow` (it creates Jobs) via `podLabels`. Job pods don't get it.
  - `CiliumNetworkPolicy forgejo-runner-jobs` (selects job pods by `app.kubernetes.io/managed-by: forgejo-runner`, which the plugin always sets and the runner pod lacks): `enableDefaultDeny.egress: true`; egress to kube-dns 53, Forgejo (`default`, `app.kubernetes.io/name: forgejo`) 3000, `envoy-internal` gateway pods (`network`, `gateway.networking.k8s.io/gateway-name: envoy-internal`) 443 for `ROOT_URL` checkout, runner pod 8089 (cache proxy), `world` 80/443. Ingress default deny.
  - Runner pod: ingress default deny except job pods on 8089; egress open apart from the namespace deny overlays (it fetches `uses:` actions from data.forgejo.org/github.com itself).
  - `CiliumNetworkPolicy forgejo-cache`: ingress only from the runner pod on 4000.
  - Forgejo's `networkpolicy.yaml`: add ingress from namespace `forgejo-runner` on 3000 (runner and job pods; `default-ingress-allowlist` does not list it).
- **Forgejo URL for jobs**: `ROOT_URL` is `https://forgejo.${SECRET_DOMAIN}` behind `envoy-internal`. `deny-lan-egress` blocks the `envoy-internal-tls` gateway pods and LAN CIDRs. Forgejo hands jobs its `ROOT_URL` for checkout; after service translation that is the envoy-internal gateway pods, so the job CNP allows them on 443 from the start (above). Leave `DEFAULT_ACTIONS_URL` at its default (`https://data.forgejo.org`).
- **Mirror must not trigger CI**: Forgejo runs Actions on mirror syncs and falls back to `.github/workflows` (16 workflows, all `runs-on: ubuntu-latest`). Before the runner registers, turn the Actions unit off on `tanguille/cluster` and confirm `has_actions: false`.

### New Files
| File | Purpose |
|------|---------|
| `kubernetes/apps/forgejo-runner/kustomization.yaml` | Namespace kustomization: `namespace: forgejo-runner`, components common, deny-lan-egress, deny-apiserver-egress; resources `./forgejo-runner/ks.yaml` |
| `kubernetes/apps/forgejo-runner/forgejo-runner/ks.yaml` | One Flux Kustomization `forgejo-runner` (path `app`, `dependsOn: [{name: forgejo, namespace: default}]`, cross-namespace dependsOn as in `kritika/kritika/ks.yaml`) |
| `kubernetes/apps/forgejo-runner/forgejo-runner/app/{kustomization,helmrelease,networkpolicy}.yaml` | Runner HelmRelease, runner + job-pod CNPs |
| `kubernetes/apps/forgejo-runner/forgejo-runner/app/{cache-helmrelease,cache-pvc,cache-networkpolicy}.yaml` | Cache server (app-template) + 50Gi ceph-block PVC (`kustomize.toolkit.fluxcd.io/prune: disabled`, as `family-blog/app/pvc.yaml`) + CNP |
| `kubernetes/apps/forgejo-runner/forgejo-runner/app/secret.sops.yaml` | `forgejo-runner-secret`: `token`, `uuid`, `cache-secret`; owner generates before merge (Chunk 3) |

### Modified Files
| File | Changes |
|------|---------|
| `kubernetes/apps/default/forgejo/app/helmrelease.yaml` | Drop `FORGEJO__actions__ENABLED: "false"` (Actions defaults to on) |
| `kubernetes/apps/default/forgejo/app/networkpolicy.yaml` | Add ingress from namespace `forgejo-runner` on 3000 |
| `kubernetes/apps/default/networkpolicy.yaml` | Update the comment that lists forgejo's extra sources (flux-system) to add `forgejo-runner` |
| `docs/forgejo.md` | Replace "CI is not covered" with a CI section: labels, cache, limits, example Rust workflow, runner re-registration |

### Conventions Checklist
- [ ] `.yaml` only, block-style YAML, schema comment on every manifest (match neighbors)
- [ ] Resource values rounded; requests just below measured average once data exists
- [ ] No version-pin comments; images as `tag: <version>@sha256:<digest>` (learned-workspace.md), including `image.tag` and `plugin.image.tag`; Renovate packageRule groups the chart and plugin image (same version)
- [ ] SOPS: owner edits/encrypts `secret.sops.yaml` (Ask-first tier); never commit plaintext
- [ ] `bash .agents/skills/pr-review/scripts/validate-pr.sh` passes; follow `.agents/common-operations.md` validation
- [ ] Conventional commit, no Claude attribution; push only on owner OK

---

## Decisions & Trade-offs

### Decision 1: Runner backend
1. Upstream runner + rootless dind in one pod: official images only, but one shared memory limit for all 3 jobs.
2. eleboucher k8s plugin: a pod per job with its own requests and limits; one-person, unlicensed dependency.

**Chosen:** 2 (owner, 2026-11-28). **Mitigation:** the plugin image is pinned by digest; if the project disappears, fall back to option 1 (the workflows don't change, only `runs-on` labels).

### Decision 2: Cache backend
JuiceFS + S3 (theirs) vs the runner's cache server on a ceph-block PVC. **Chosen:** PVC. One runner, no shared-cache need, no new systems.

### Decision 3: Concurrency control
Kueue vs runner `capacity: 3` + realistic requests. **Chosen:** capacity. The scheduler holds jobs that don't fit as Pending. Revisit only if burst OOMs show up.

### Decision 4: Namespace
`default` vs a new `forgejo-runner` namespace. **Chosen:** new namespace (isolation from `default` apps, apiserver deny by default).

---

## Dependencies

### Prerequisites
- Owner OK to apply (`flux reconcile`) and to create the SOPS secret.

### External Dependencies
- `oci://git.erwanleboucher.dev/eleboucher/charts/forgejo-runner`, `git.erwanleboucher.dev/eleboucher/runner-k8s-plugin` image (third party)
- `data.forgejo.org/forgejo/runner`, `ghcr.io/bjw-s-labs/forgejo-runner` (job image), `ghcr.io/bjw-s-labs/helm/app-template` (existing `app-template` OCIRepository)

---

## Execution Plan

### Progress Tracker

#### Wave 1 (parallel) — Manifests
- [ ] Chunk 1: Enable Actions on Forgejo + ingress from the runner namespace
- [ ] Chunk 2: `forgejo-runner` namespace: cache server, runner, policies

#### Wave 2 (sequential) — Roll out and prove
- [ ] Chunk 3: Register, apply, end-to-end Rust test, docs (depends on Chunks 1, 2)

### Wave Conflict Matrix
| Chunk | Write Set | Dependencies | Wave |
|-------|-----------|--------------|------|
| 1 | `kubernetes/apps/default/forgejo/app/helmrelease.yaml`, `kubernetes/apps/default/forgejo/app/networkpolicy.yaml`, `kubernetes/apps/default/networkpolicy.yaml` | None | 1 |
| 2 | `kubernetes/apps/forgejo-runner/**` except `app/secret.sops.yaml`, `.renovaterc.json5` | None | 1 |
| 3 | `kubernetes/apps/forgejo-runner/forgejo-runner/app/secret.sops.yaml`, `kubernetes/apps/forgejo-runner/forgejo-runner/app/kustomization.yaml` (add the secret), `docs/forgejo.md` | Chunks 1, 2 | 2 |

---

### Chunk 1: Enable Actions on Forgejo
**Status:** Not Started
**Wave:** 1
**Estimated Context:** ~10k tokens
**Complexity:** Low

#### Scope
- `kubernetes/apps/default/forgejo/app/helmrelease.yaml`: delete the line `FORGEJO__actions__ENABLED: "false"`.
- `kubernetes/apps/default/forgejo/app/networkpolicy.yaml`: add a second `fromEndpoints` entry `io.kubernetes.pod.namespace: forgejo-runner` to the existing ingress rule's port 3000.
- `kubernetes/apps/default/networkpolicy.yaml`: update the comment naming forgejo's extra sources.

#### Prerequisites
None (Wave 1)

#### Detailed Steps
1. Verify branch/worktree (`git -C .claude/worktrees/forgejo-actions status -sb` shows `feat/forgejo-actions`), then `git pull --ff-only` is not applicable (new branch); `git fetch && git rebase origin/main` if main moved.
2. Make the two edits. Keep `FORGEJO__federation__ENABLED` etc. untouched.
3. Grep for anything citing Actions being off: `grep -rn 'actions__ENABLED\|CI are not\|issues, PRs and CI' docs kubernetes`. `docs/forgejo.md` is updated in Chunk 3.

#### Verification
- [ ] `flux-local`/kustomize build of `kubernetes/apps/default` succeeds (per `.agents/common-operations.md`).
- [ ] After rollout (Chunk 3): `kubectl -n default exec deploy/forgejo -- wget -qO- http://localhost:3000/api/v1/repos/tanguille/cluster | jq .has_actions` (new repos default to Actions enabled; mirror stays read-only).

#### Files Changed Summary
```
feat(forgejo): enable Actions and admit the runner namespace
```

---

### Chunk 2: forgejo-runner namespace
**Status:** Not Started
**Wave:** 1
**Estimated Context:** ~40k tokens
**Complexity:** Medium

#### Scope
All new files under `kubernetes/apps/forgejo-runner/` listed in New Files, except the encrypted secret content (Chunk 3). Add `./secret.sops.yaml` to `app/kustomization.yaml` only in Chunk 3, so Wave 1 builds.

#### Prerequisites
None (Wave 1)

#### Detailed Steps
1. **Namespace kustomization**: copy `kubernetes/apps/kritika/kustomization.yaml`, set `namespace: forgejo-runner`, components `common`, `deny-lan-egress`, `deny-apiserver-egress`. Comment: own namespace because job pods run untrusted code. Confirm that `common` creates the Namespace (it includes `namespace.yaml`) and that `kubernetes/apps` is discovered automatically (`kubernetes/flux/cluster/ks.yaml` path `./kubernetes/apps`).
2. **ks.yaml**: one Kustomization `forgejo-runner` (path `.../app`, `dependsOn: [{name: forgejo, namespace: default}]`), shaped like `kritika/kritika/ks.yaml`.
3. **Cache** (`app/cache-helmrelease.yaml`, `chartRef: {kind: OCIRepository, name: app-template}` as Forgejo does):
   - No initContainer: the runner image holds `/bin/forgejo-runner` and runs as 1000:1000 (v13.2.2 Dockerfile). Container image `data.forgejo.org/forgejo/runner:<tag>@sha256:<digest>`, `command: [forgejo-runner, cache-server, --dir, /data, --port, "4000", --secret, $(CACHE_SECRET)]`, env `CACHE_SECRET` from `forgejo-runner-secret` key `cache-secret`. An empty secret makes it exit 0 (restart loop, no error crash).
   - `strategy: Recreate` (bbolt single writer), non-root securityContext matching Forgejo's (uid/gid 1000, fsGroup 1000, drop ALL, RuntimeDefault seccomp).
   - tcpSocket 4000 probes; resources requests 10m / 64Mi, limit 512Mi (adjust from measurement later).
   - Service `forgejo-cache` port 4000; PVC `forgejo-cache` 50Gi ceph-block mounted at `/data`.
4. **Runner** (`app/helmrelease.yaml`): inline chart `oci://git.erwanleboucher.dev/eleboucher/charts/forgejo-runner` at the latest tag (registry). Values:
   - `forgejo.url: http://forgejo.default.svc.cluster.local:3000`, `forgejo.existingSecret: forgejo-runner-secret`.
   - `image.tag: <chart default>@sha256:<digest>` and `plugin.image.tag: <chart version>@sha256:<digest>`, set explicitly so Renovate tracks both. Add a `.renovaterc.json5` packageRule grouping the chart and `git.erwanleboucher.dev/eleboucher/runner-k8s-plugin` (chart version = plugin tag); check with a Renovate lookup dry run.
   - `plugin.resources`: requests 5m / 64Mi, limit 512Mi (their 128Mi OOMKilled at capacity 3).
   - `podLabels: {egress.home.arpa/kube-apiserver: allow}`.
   - `runnerConfig`: `cache: {enabled: true, external_server: http://forgejo-cache.forgejo-runner.svc:4000/, secret_url: file:/etc/forgejo-runner-secret/cache-secret, proxy_port: 8089}`, `container: {docker_host: "-", workdir_parent: shared/workdir}`, `plugins.k8s: {address: unix:///plugin/forgejo-runner-k8s.sock, options: {namespace: forgejo-runner, poll_timeout: 1h}}`, `runner: {capacity: 3, timeout: 3h, shutdown_timeout: 3m, labels: [ubuntu-latest:k8s:///config/podspec-default.yaml, default:k8s:///config/podspec-default.yaml, rust:k8s:///config/podspec-rust.yaml]}`. Check `secret_url` semantics and key names against the runner config reference for the deployed version (`forgejo-runner generate-config`), not this plan.
   - `podSpecs.podspec-default.yaml` / `podspec-rust.yaml` per the Design section. A comment explains the rust limit: 3 jobs = 12Gi requests, extra jobs wait Pending.
5. **Policies**: `app/networkpolicy.yaml` = `forgejo-runner-jobs` (endpointSelector `app.kubernetes.io/managed-by: forgejo-runner`, rules per Design) plus a runner-pod ingress policy (default deny, job pods on 8089). Runner egress stays open apart from the namespace deny overlays. **Never** use `ingressDeny` toward the kube-apiserver entity (it is `host` in this cluster). `app/cache-networkpolicy.yaml` = ingress to cache from runner pods only on 4000.

#### Verification
- [ ] Kustomize/flux-local build of `kubernetes/apps/forgejo-runner` succeeds; HelmRelease values validate against the chart (`helm template oci://... --version <tag> -f <values>` renders the Deployment with our podSpecs ConfigMap).
- [ ] `validate-pr.sh` passes.

#### Files Changed Summary
```
feat(forgejo-runner): add per-job-pod runner with a ceph-block cache
```

---

### Chunk 3: Register, roll out, prove, document
**Status:** Not Started
**Wave:** 2
**Estimated Context:** ~50k tokens
**Complexity:** Medium

#### Prerequisites
Chunks 1 and 2.

#### Detailed Steps
1. **Before merge**, generate the runner secret and the SOPS file so everything ships in one PR (owner runs; the values are secret): `token` = `openssl rand -hex 20`, `cache-secret` = `openssl rand -hex 32`. Add `secret.sops.yaml` to `app/kustomization.yaml`. Commit Chunks 1-3 manifests; **ask the owner** before pushing, opening the PR, and applying.
2. After merge, Forgejo restarts with Actions on. **First** disable Actions on the mirror: `curl -X PATCH .../api/v1/repos/tanguille/cluster -d '{"has_actions":false}'` with an admin token, and confirm `has_actions: false`. Then register the runner with the pre-generated token (the runner pod retries until registered):
   ```bash
   SECRET=$(openssl rand -hex 20)
   mise exec -- kubectl -n default exec deploy/forgejo -- \
     forgejo forgejo-cli actions register --name cluster --secret "$SECRET" --scope ""
   ```
   `register` prints the runner uuid; it is derived from the token, so it can also be computed before merge. Check flags with `forgejo forgejo-cli actions register --help`. If the printed uuid differs from the one in `secret.sops.yaml`, the owner updates the SOPS file.
3. (Folded into step 1.)
4. Reconcile (owner OK). Check: runner pod Ready, Idle in the Forgejo admin UI, cache pod Ready, PVC Bound.
5. **hostUsers smoke test**: push a scratch repo with a workflow running `cat /proc/self/uid_map; id`. Expect a non-identity uid map. If pods fail to start with a user-namespace error, stop and report (Talos/kernel support), do not drop `hostUsers: false` without the owner's OK.
6. **Isolation test** in the same job: `curl -sk https://kubernetes.default.svc` fails, `curl -s http://forgejo.default.svc.cluster.local:3000/api/healthz` succeeds, a LAN address (router) fails.
7. **Checkout**: confirm `actions/checkout` works; if not, apply the envoy-internal egress fix from the Design section and record it.
8. **Rust E2E**: a small real Rust repo with `runs-on: rust`, `actions/checkout`, `dtolnay/rust-toolchain@stable` (via full `https://github.com/...` URL if the data.forgejo.org mirror lacks it), `Swatinem/rust-cache@v2`, `cargo build --locked && cargo test`. Run twice; record durations, cache hit log, peak memory. Then trigger 3 parallel runs and confirm ≤3 job pods, no OOMKilled (`kubectl get events -n forgejo-runner --field-selector reason=OOMKilling` and pod statuses).
9. Delete the cache pod, rerun: still a cache hit.
10. **Docs**: in `docs/forgejo.md` replace "Only Git branches and tags are covered; issues, PRs and CI are not." with what is now true, and add a "CI" section: labels and their limits, cache location/size, example workflow, how to re-register the runner, the third-party plugin risk and fallback (Decision 1). Grep for other references to Forgejo CI being off.
11. Set request values from the measured averages (owner rule: requests just below average) in a follow-up commit if they differ.

#### Verification
- [ ] All US-1..US-3 acceptance criteria checked with the command output saved.
- [ ] `flux get hr -n forgejo-runner` all Ready; no Warning events in `forgejo-runner` after the test runs.

#### Files Changed Summary
```
feat(forgejo-runner): add runner registration secret
docs(forgejo): document Actions CI and the Rust runner
```

---

## Testing Strategy

### Unit Tests
None (manifests only); rendering and validation via flux-local/helm template and `validate-pr.sh`.

### E2E Verification
- [ ] Scratch workflow: uid map, isolation probes.
- [ ] Rust repo: cold vs warm run times, cache hit, 3 concurrent runs without OOM.

---

## Rollback Plan

1. Suspend or delete `kubernetes/apps/forgejo-runner` (Flux prunes the runner and cache; the PVC has `prune: disabled`, so delete it by hand if the cache is unwanted).
2. Revert Chunk 1 to turn Actions off again; Forgejo data is unaffected (Actions tables stay empty or unused).
3. Fallback if the plugin project disappears: upstream runner with a rootless dind sidecar, same labels.

---

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the plan's contents are consolidated into existing docs, remove the plan file; if no relevant docs exist, rework the plan into a reference document.

**Important**: Every prompt should verify the branch and worktree before doing any work.

---

## Review Report (2026-11-28, Opus review subagent)

- CRIT-1 [FIXED] Flux deadlock: cache Kustomization waited on a secret only the runner Kustomization applied. Merged into one Kustomization/`app/` dir, no dependsOn between cache and runner.
- CRIT-2 [FIXED] Mirror syncs would run the 16 `.github/workflows` on the runner. Chunk 3 disables Actions on the mirror before registration; acceptance criterion added.
- HIGH-1 [FIXED] Runner egress would block fetching `uses:` actions: runner egress left open, ingress restricted instead.
- HIGH-2 [FIXED] Memory math: rust jobs required on control-1, `homelab-optional` priority, ephemeral-storage request.
- HIGH-3 [FIXED] Two-PR rollout: secret generated before merge, one PR.
- MED-1 [FIXED] `tag@digest` convention + Renovate grouping. MED-2 [FIXED] `automountServiceAccountToken: false`. MED-3 [FIXED] 30Gi `shared` emptyDir for rust. MED-4 [FIXED] `poll_timeout: 1h`. MED-5 [FIXED] stale comment in `default/networkpolicy.yaml` added to Chunk 1.
- LOW-1 [FIXED] write set. LOW-2 [FIXED] 8088 dropped. LOW-3 accepted (argv secret inside the pod). LOW-4 noted in Chunk 2.
- AMB-1..6, PIT-1 [FIXED] inline. PIT-2: Renovate dry-run lookup in Chunk 2. PIT-3 [FIXED] in US-1.
