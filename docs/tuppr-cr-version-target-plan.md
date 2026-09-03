# Make Talos + kernel bumps merge-and-forget

**Branch:** `feat/tuppr-cr-version-target`
**Worktree:** `.claude/worktrees/tuppr-cr-version`

Goal: get back the property plain tuppr had before the custom kernel — a
Renovate PR lands, you merge it, the fleet rolls itself. Today a bump costs a
manual pre-merge workflow dispatch, a ~90 min watch, and then `apply-node` on
every node from a workstation holding the age key.

## Why merging does nothing today

The target version tuppr compares against does not live in git-synced cluster
state. It lives in each node's machine config:

```yaml
# talos/nodes/controlplane/control-3.yaml.j2
nodeAnnotations:
  tuppr.home-operations.com/version: {{ pinned }}
```

That annotation only reaches a node through `just talos apply-node`, and
`getTargetVersion` prefers it over `spec.talos.version`. So merging a CR bump
changes nothing a node can see, and `docker/talos-kernel/README.md` "The
rollout is not self-closing" correctly ends the procedure with three manual
`just talos upgrade-node <node> <ip>` calls.

Automating the build alone does not fix this. The version has to move into the
CR.

### Why it was put in the annotation

`docker/talos-kernel/README.md` states the blocker plainly:

> `spec.talos.version` cannot carry it — that field is Renovate-managed against
> `siderolabs/talos` and would rewrite `v1.13.9-k7.1.9` to `v1.13.10`, eating
> the suffix.

That is correct for the *annotation-style* manager it uses today. It is not a
property of the field. Two file-scoped regex custom managers can each own one
half of the string.

## Verified before writing this

Everything below was checked against source at tuppr 0.5.2, not assumed.

| Claim | Evidence |
|---|---|
| `prePull` defaults on | `talosupgrade_types.go:21` `+kubebuilder:default=true` |
| A missing installer parks the run, it does not fail it | `prepull.go:133` sets `JobPhasePending` / `ReasonPrePullFailed`; requeue `prePullFailureBackoff` = 1m doubling, capped 5m at `attempts >= 4`. No attempt cap, no transition to `Failed` |
| Terminal `Failed` is a different path | `upgrade.go:64` — real upgrade failures, cleared by `constants.ResetAnnotation` (`annotations.go:48`). Does not apply to pre-pull |
| The CRD and the webhook accept the suffix | `talosupgrade_types.go:13` pattern `^v[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z0-9\-\.]+)?$`; `webhook.go:80` / `validation.go:120` use the same pattern, so Flux's apply is not rejected |
| The annotation outranks the CR | `upgrade.go:855` `getTargetVersion` |
| A generation change re-arms completed nodes | `upgrade.go:583` `findNextNodes` skips `Status.CompletedNodes`; `annotations.go:97-105` resets it on generation change |
| **Cordoning does not hold a node back** | `findNextNodes` (`upgrade.go:566-621`) contains no `Unschedulable` or `Spec.Taints` check — verified by grep. tuppr cordons and drains itself |
| **Reverting `install.image`'s tag does not hold a node back** | `buildTalosUpgradeImage` (`upgrade.go:820,851`) keeps only the repo and substitutes the target tag |
| Outdated nodes carry a taint while parked | `upgrade.go:606-608` + `nodes.go:68-89`, `PreferNoSchedule` |
| `flate.yaml` would break | `.github/workflows/flate.yaml:124` reads `.spec.talos.version` into a `releases/download/${TALOS_VERSION}` URL — a `-k` suffix 404s |
| `talosctl.image.tag` must stay plain | `jobs.go:559-568` uses the tag verbatim when set |
| The talos group would capture the new manager | `.renovaterc.json5:167-177` groups any `siderolabs/*` on `docker`/`github-releases` |
| `versioning: loose` already applies to the kernel datasource | `.renovaterc.json5:291-298` keys off `matchDatasources: ["custom.linux-kernel"]` |
| Nothing else reads the annotation | grep: only the three node templates and the README |
| 0.5.3 changes nothing relied on here | its only controller change is a `ghcr.io/siderolabs/installer` → Image Factory redirect, which never matches `ghcr.io/tanguille/installer/*` |

Deployed tuppr is **0.5.2** (`app/ocirepository.yaml:13`).

## The one trade-off to decide first

`docker/talos-kernel/README.md` argues the annotation is per-node **on
purpose**:

> Keep the annotation per-node rather than hoisting it to a shared layer: it is
> what allows one node to move while the others stay put, which is how all
> three were rolled.

Moving to the CR gives that up as the *default*. control-1 is the outlier —
TrueNAS VM, only dGPU host, its own schematic and installer repo, hypervisor
reset history — so this is a real loss.

**The hold you get back is declarative, in the same file as the version.** The
CRD has `spec.nodeSelector` (a full `LabelSelector` — confirmed present on the
deployed CRD, alongside `drain`, `maintenance`, `parallelism`, `silences`):

```yaml
  nodeSelector:
    matchExpressions:
      - key: kubernetes.io/hostname
        operator: NotIn
        values: ["control-1"]
```

Flux-applied, reviewable, revertible, no age key and no `apply-node` — the
per-node hold the README argued for, moved to the CR layer rather than lost.
Verify it against `findNextNodes` before relying on it.

The imperative fallback, if you want a hold that leaves no diff:
`getTargetVersion` still prefers a node annotation, and after Step 1 Talos no
longer owns that key, so `kubectl annotate node control-1
tuppr.home-operations.com/version=<current string>` pins it and `...version-`
releases it.

Do **not** reach for cordoning or reverting `install.image` — both are verified
above to be ignored by tuppr, and a repo revert to `factory.talos.dev` silently
reinstalls the stock kernel.

`spec.maintenance.windows` is the declarative answer to "keep the window
closed", which Step 3 currently leaves to `workflow_dispatch`.

If per-node staging by default still matters more than merge-and-forget, stop
here: Step 4 (the Renovate annotation gap) is already shipped and is worth
having regardless.

## Steps

### Step 1: one atomic PR — managers, CR, consumers, templates — **DONE** (e37104552)

Shipped on `feat/tuppr-cr-version-target`, stacked on `feat/talos-1.14.0-k7.1.13`
so the CR can name `v1.14.0-k7.1.13` directly and the merge is a live test of
the new path rather than a no-op.

Two deviations from the text below, both deliberate:

- The Talos-half regex gained `(?:-[a-z]+\.\d+)?` so it also matches a
  prerelease pin (`v1.14.0-rc.2-k7.1.10`, which is what the fleet ran when this
  was written). Without it the manager silently matches nothing on an rc.
- The `-k` guard was added to `.justfile` `template` as well as the build
  script, so a half-applied tree fails to *render*, not just to build.

Verified: all three nodes render and pass `talosctl validate -m metal`; no
`tuppr` string survives in any rendered config; the guard fires
(`tuppr CR names k7.1.13, Dockerfile builds 7.1.99`); `.renovaterc.json5` parses
and registers both managers; and the first-occurrence replacement semantics were
simulated against both regexes — the un-narrowed kernel regex turns
`v1.17.3-k7.3` into `v1.17.4-k7.3`, the shipped one into `v1.17.3-k7.4`.

Still outstanding for this step: a Renovate dry-run against the real config
(needs node 24 + a GH token). The repo's own validator false-flags
`managerFilePatterns`, so a clean validator run is not the bar.

These **cannot** be separate merges. Flux applies main immediately and
`talosupgrade.yaml` is already a build trigger path, so every intermediate
state is live and broken:

- CR moved but `.justfile` not: `template` renders
  `pinned=v1.14.0-k7.1.13-k7.1.13` (`.justfile:49` concatenates), and any
  `apply-node` in that window writes a nonexistent `install.image`.
- CR moved but build script not: `talos/mod.just:144` passes the full string as
  `$1`, so `talos-kernel-build.sh:31` runs `git clone --branch v1.14.0-k7.1.13`
  and fails.
- CR moved but `flate.yaml` not: 404 on every container PR.
- Managers alone are a no-op — both regexes require `-k`, which the CR lacks
  until it moves.

**1a. `.renovaterc.json5` — two `customManagers`:**

```json5
{
  description: "tuppr target v<talos>-k<kernel>: Talos half",
  customType: "regex",
  managerFilePatterns: ["/tuppr/upgrades/talosupgrade\\.yaml$/"],
  matchStrings: ["version:\\s+(?<currentValue>v\\d+\\.\\d+\\.\\d+)-k"],
  depNameTemplate: "siderolabs/talos",
  datasourceTemplate: "github-releases"
},
{
  description: "tuppr target v<talos>-k<kernel>: kernel half",
  customType: "regex",
  managerFilePatterns: ["/tuppr/upgrades/talosupgrade\\.yaml$/"],
  matchStrings: ["-k(?<currentValue>\\d+\\.\\d+(?:\\.\\d+)?)"],
  depNameTemplate: "linux",
  datasourceTemplate: "custom.linux-kernel"
}
```

The kernel regex deliberately does **not** anchor on the Talos prefix. Renovate
auto-replace does `replaceString.replace(escape(currentValue), newValue)` —
first occurrence, non-global — over the whole matched span. With a span of
`version: v1.17.3-k7.3` and a bare-series `currentValue` of `7.3` (the `loose`
case this repo explicitly plans for), the first occurrence of `7.3` is inside
`v1.17.3`, and Renovate would corrupt the Talos half. A span of `-k7.3` has
exactly one occurrence and cannot mismatch.

The Talos half is safe as written: span `version: v1.14.0-k`, currentValue
`v1.14.0`, suffix untouched.

Both regexes are RE2-safe (`(?<name>)`, `(?:)`, `\d`, `\s` only) — local
renovate falls back to JS RegExp and will not catch a violation the hosted run
rejects.

The two managers extract two deps into two branches; no conflict inside
Renovate, and Renovate rebases the second after the first merges. `depName:
linux` is the same dep as the Dockerfile `ARG`, so both files land in one
branch — which is exactly what the guard in 1c relies on.

**1b. `talosupgrade.yaml`:**

- `spec.talos.version` → `v1.14.0-k7.1.13`.
- Delete the `# renovate:` annotation above it; 1a owns it now. Leaving both
  means two managers fighting over one line.
- Break the `&talosVersion` anchor. `talosctl.image.tag` stays the **plain**
  upstream tag with its own
  `# renovate: datasource=github-releases depName=siderolabs/talos` —
  siderolabs never publishes `talosctl:v1.14.0-k7.1.13`, and the README records
  that ImagePullBackOff measured on control-2 on 2026-08-21.

**1c. consumers:**

- `.github/workflows/flate.yaml:124` → read `.spec.talosctl.image.tag`.
- `scripts/talos-kernel-build.sh:10` takes the full string and splits it:

  ```bash
  VERSION="${1:?usage: talos-kernel-build.sh <version> <node>...}"
  TALOS_VERSION="${VERSION%-k*}"
  KERNEL_VERSION="$(just kernel-version)"
  [[ "${VERSION#*-k}" == "${KERNEL_VERSION}" ]] || {
      echo "CR names k${VERSION#*-k}, Dockerfile builds ${KERNEL_VERSION}" >&2; exit 1; }
  ```

- `.justfile` `template`: `pinned` becomes `just tuppr-version talos` verbatim,
  no concatenation — this also closes the "`pinned` derived in two places"
  duplication. Add the same guard here, because `pinned` now comes from the CR
  while `kernelVersion` still comes from the Dockerfile ARG:

  ```bash
  [[ "${talos_version#*-k}" == "${kernel_version}" ]] || { echo "..." >&2; exit 1; }
  ```

  A half-merged tree then fails to render instead of pinning a node to a tag
  whose contents differ from what the `kernelVersion` comment claims.

**1d. node templates:** delete the `nodeAnnotations` block from all three
`talos/nodes/controlplane/control-{1,2,3}.yaml.j2`. Keep `install.image` on
`{{ pinned }}` — tuppr discards the tag and reuses the repo, but the stored
value still drives `just talos upgrade-node` and any fresh install.

**Verify before merging:** `just talos render-config control-{1,2,3}` all yield
`v1.14.0-k7.1.13` and pass `talosctl validate -m metal`; a Renovate dry-run
shows *two* separate updates against `talosupgrade.yaml` and rewrites neither
half wrongly (needs node 24 + a GH token, or it reads as "dep not detected" and
tells you nothing); `flux diff kustomization cluster-apps`.

### Step 2: the last manual `apply-node`

Talos re-enforces node annotations from machine config, so until the config
without it is applied, the stale annotation keeps outranking the CR. Three
`apply-node` runs, once, to never do it again.

**Verify:**
`kubectl get nodes -o custom-columns=NAME:.metadata.name,V:.metadata.annotations.tuppr\.home-operations\.com/version`
shows the column empty for all three.

### Step 3: retire the pre-merge dispatch

`.github/workflows/talos-kernel.yaml` keeps `push: branches: [main]` — that is
now the whole mechanism. Keep `workflow_dispatch` as an escape hatch for when
you *want* the window closed (the held 7.2.x PR when Cilium ships the fix).

**Do not add `.justfile`, `talos/mod.just` or `talos/nodes/**/*.yaml.j2` to
`paths:`.** Every unrelated edit there would start a ~2h run that re-pushes
`kernel:`, `imager:`, `installer-base:` and the installers under the *same*
tags, with a freshly generated module signing key in the amdgpu extension —
contradicting the README's "tags are never reused" property — and
`cancel-in-progress` would abort a real version build in flight. Instead add a
`crane manifest` existence check at the top of the build script that exits 0
when the installer tag already exists. That also makes the Step 1 merge and any
rerun idempotent.

Rewrite `docker/talos-kernel/README.md` "Per-bump maintenance" and "The rollout
is not self-closing": the procedure becomes "merge the PR", plus a note that the
CR sits in `Pending`/`PrePullFailed` for ~2h until the build publishes, and that
Warning events every 5m in that window are expected.

**Add a parked-state alert.** Pre-pull retries forever at 5m and never reaches
`Failed`, and outdated nodes carry the `PreferNoSchedule` taint the whole time.
A failed build therefore leaves the fleet quietly degraded. A Prometheus rule on
phase `Pending` / reason `PrePullFailed` lasting > 4h is what keeps
merge-and-forget from becoming merge-and-never-notice.

### Step 4: Renovate gap on `kubernetesupgrade.yaml`

Already shipped on `feat/talos-1.14.0-k7.1.13` (commit e6e042c3f). Listed for
completeness; nothing to do if that branch merged.

## Fresh installs still work

No annotation → `getTargetVersion` returns the CR value; the node reports
`v<talos>-k<kernel>` because the installer is built with `TAG="${VERSION}"`;
`install.image` stays on `{{ pinned }}`.

## Rollback

Reverting the PR is a **live downgrade command**, not a no-op: the CR goes back
to the older string and tuppr issues `talosctl upgrade` to the older tag on
every node, one at a time. The older installer tag is still published — tags in
`ghcr.io/tanguille/installer/*` are never reused.

Fine for a kernel-only revert. For a Talos *minor* revert, suspend the CR
(`handleSuspendAnnotation`) first and decide deliberately whether the
Kubernetes and CNPG state on the nodes tolerates going backwards.

## The hold still works

7.2.x stays an open, unmerged Renovate PR. Nothing builds and nothing rolls
until it is merged. Do not switch to `allowedVersions: <7.2` — the README
explains it empties the feed once 7.1 leaves `moniker=stable` and bumps stop
silently.

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of the
  plan have been consolidated into existing documentation, the plan file can be
  removed. If there is no relevant existing documentation, the plan should be
  reworked into a reference document.

**Important**: Every prompt should verify the branch and worktree before doing
any work.
