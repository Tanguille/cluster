# VolSync → Kopiur Migration Plan

Migrate PVC backups from the volsync perfectra1n fork (kopia mover) to
[kopiur](https://github.com/home-operations/kopiur), the home-operations
Kopia-native operator. The existing NFS kopia repository is **adopted in
place** — no data movement, full history preserved.

**Branch**: `feat/kopiur-migration`
**Worktree**: `.worktrees/kopiur-migration` — create with
`git worktree add .worktrees/kopiur-migration -b feat/kopiur-migration`,
then copy `.mcp.json`, `.env`, `CLAUDE.local.md`, `.vscode/`, and `.claude/`
into the worktree (branch/worktree verification per step: see Process
Instructions at the bottom). Neither exists yet as of this revision.

## Revision note (2026-07-11)

The original draft (2026-07-10, pinned kopiur `0.4.13`) was cross-checked
against: onedr0p/home-ops's 9 seed commits *and* 21 further commits they
made afterward; 8 independently-verified real kopiur adopters
(bjw-s-labs/home-ops, buroa/k8s-gitops, szinn/k8s-homelab, jfroy/flatops,
vrozaksen/home-ops, carldanley/homelab, eleboucher/homelab,
binaryn3xus/HomeOps); and a fresh pass over kopiur's own ADRs, releases,
CRD schemas, and issue tracker (not the README — confirmed stale again).
Four things changed the plan's shape, not just its field names:

1. **Version bumped `0.4.13` → `0.7.1`.** 0.5.0/0.6.0/0.7.0 each shipped
   breaking API changes. Installing fresh at 0.7.1 skips 0.6.0's worst trap
   (CRD relocation into Helm's `crds/` dir cascade-deletes every kopiur
   object on upgrade unless pre-annotated) — that only bites in-place
   upgraders, not us.
2. **Use the official `kubectl kopiur migrate volsync` CLI** instead of
   hand-authoring the ClusterRepository/SnapshotPolicy YAML from scratch.
   It auto-detects the perfectra1n fork, adopts in place, and — the load
   -bearing part — **pins per-app snapshot identity automatically**
   (`<app>@<namespace>:/data`) rather than us reproducing that string via a
   repo-level CEL `identityDefaults` and hoping the sanitization matches.
3. **New, unfixed, today-dated risk: [kopiur#233](https://github.com/home-operations/kopiur/issues/233).**
   Recreating a `Restore` CR whose target PVC is already `Bound` silently
   restores into an orphaned, never-garbage-collected `prime-<uid>` PVC.
   `status.phase` reports `Completed` while `status.conditions` stay
   `Ready=False`, so Flux health checks and kstatus won't catch it. One
   reporter hit 49 orphaned PVCs / 120GiB from a single `ClusterRepository`
   rebuild. **This is now a permanent post-migration operating rule** (see
   the Risk gate), not a one-time step.
4. **Fleet cutover collapsed to one flip.** onedr0p's real commit history
   shows they did *not* canary-then-slow-batch: they ran kopiur dual-write
   fleet-wide first (proving real backups for all 19 apps while volsync
   still owned every PVC), then flipped every PVC in one near-simultaneous
   pair of commits. Phase 5 below now matches that — and matches the
   required-steps list already given for this migration — instead of the
   original per-namespace-over-multiple-sessions batching.

## Why migrate

- The shared component hardcodes `schedule: 0 */12 * * *` for all ~18 apps —
  every backup fires the same second (the 112-concurrent-mover Ceph latency
  storms). Kopiur has native `H` cron hashing + deterministic jitter +
  `concurrencyPolicy: Forbid`; the `volsync-mover-jitter`
  MutatingAdmissionPolicy hack (0–30s sleep) becomes obsolete.
- Kills 18 duplicated per-app sops secrets (same `KOPIA_PASSWORD`/
  `KOPIA_REPOSITORY` everywhere) → one shared secret + credential projection.
- The perfectra1n fork is a community fork of a project whose upstream has no
  kopia mover; kopiur is kopia-native from the org whose patterns this repo
  already follows (charts mirror, k8s-schemas host — kopiur CRD schemas are
  already served there, verified).

## Risk gate (read before every phase)

Kopiur is **alpha** (v0.7.1, repo <2 months old, AGPL-3.0, 30 releases
`0.1.12`→`0.7.1`): every one of 0.5.0/0.6.0/0.7.0 shipped breaking changes,
including one full CRD rename with no aliases (ADR-0004:
`BackupConfig→SnapshotPolicy`, `Backup→Snapshot`, `BackupSchedule→SnapshotSchedule`).
The README's CRD table is stale even at the pinned tag — **trust
`deploy/crds/*.yaml`, `deploy/examples/*.yaml`, and kopiur.home-operations.com,
never the README.**

Confirmed-implemented-late gotcha for calibration: `Restore.spec.target.populator: {}`
was *admitted by the CRD/webhook but entirely unimplemented* as late as
chart 0.4.6 (PVCs sat `Pending` forever, no error) — fixed since, but
"documented" ≠ "implemented" at any given historical tag for this project.
Test the real behavior in Phase 1/4 rather than trusting docs alone.

Mitigations baked into this plan:
- Pin chart tag `0.7.1`; treat kopiur Renovate PRs as read-release-notes
  -first (0.6.0-style breaking chart-value restructures are real and
  recurring).
- CRDs ship inside the chart's own Helm `crds/` directory as of 0.6.0+ — no
  separate CRDs-only chart needed for a *fresh* install (Helm auto-installs
  `crds/` on first install; it does **not** auto-upgrade them later —
  future CRD changes need a manual `kubectl apply -f deploy/crds/`).
- Backup-failure alerting (shipped by the chart) is live from Phase 1, before
  any data-bearing step.
- volsync keeps running fleet-wide until Phase 4's canary is verified; the
  fleet is mixed-mode (dual-write) before the single fleet-wide PVC flip in
  Phase 5 — one writer per identity at all times.
- kopiur **never deletes discovered data** (foreign snapshots are forced
  `deletionPolicy: Retain`), and adoption with a wrong password hard-fails
  rather than re-initializing.
- **[#233](https://github.com/home-operations/kopiur/issues/233) (open,
  filed 2026-07-11) — permanent post-migration rule**: once an app's
  `Restore` CR has successfully populated its PVC, do not let it get
  deleted-and-recreated while that PVC is `Bound` (a Flux prune/recreate
  cycle, a `ClusterRepository` reset, a forced Kustomization re-apply all
  qualify). If any of those happen to a migrated app, check for orphaned
  `prime-*` PVCs afterward (`kubectl get pvc -A -l
  kopiur.home-operations.com/op=restore-populate`) — they silently consume
  full-size Ceph storage and won't show up as a Flux/kstatus failure.
- **[#232](https://github.com/home-operations/kopiur/issues/232) (open)** —
  the schema doc string implies `encryption.passwordSecretRef.namespace` is
  optional; the controller currently rejects reconcile without it. Always
  set it explicitly (the plan below already does).
- **[#210](https://github.com/home-operations/kopiur/issues/210) (open)** —
  discovered snapshots are *permanently* exempt from pruning by design; if
  #233 recurs against an adopted repo, orphaned history compounds forever,
  not just once.
- **Repo-wide `0700` permission drift (found + fixed 2026-07-11)**: kopia
  silently retries `PermissionDenied` forever instead of erroring, so a
  stuck mover with no log output and near-zero CPU is this, not a slow
  backend. Check first with `find /repo -type d -not -perm 0775` / `-type f
  -not -perm 0775` before chasing anything else. Mechanism **confirmed**
  2026-07-12: kopiur writes new blobs `65532:568` mode `0600`/`0700`
  (owner-only, no group-read bit), so any *other* group-568-only reader —
  the fork's mover chief among them — silently hangs on fresh kopiur
  content the same way. This is why the fork's `KopiaMaintenance` is
  disabled in Phase 3 rather than left running dual-write. Full writeup in
  Phase 2/3 below.
  **Live incident, 2026-07-12**: disabling `KopiaMaintenance` wasn't
  sufficient — the fork's regular `ReplicationSource` backups hit the same
  bug independently, because `kopia.maintenance.f` (rewritten by kopiur's
  own maintenance every ~6h) is read on **every** repository connect,
  regardless of which app is backing up. 4 concurrently-running fork
  backups (dumbassets, changedetection, karakeep, nextcloud) were all
  silently stuck at "Connecting to filesystem repository" simultaneously.
  Unblocked immediately with the same repo-wide chmod (this time as uid
  `65532` via a throwaway pod — `kubectl exec` as root failed with
  `Operation not permitted`, because the NFS export has `root_squash`;
  only the actual owning UID can chmod its own files over NFS). **Durable
  fix**: `components/volsync`'s `moverSecurityContext.runAsUser` default
  changed `568` → `65532` (`replicationsource.yaml` +
  `replicationdestination.yaml`; `runAsGroup`/`fsGroup` stay `568`), so the
  fork's mover now shares kopiur's own UID. This closes the gap in both
  directions at once: the fork can read kopiur's `65532`-owned files (UID
  match), and if the fork's *own* v0.22.3 kopia binary also defaults to a
  restrictive create mode, its new writes become `65532:568` too — readable
  by kopiur for the same reason. Only hermes overrides `VOLSYNC_PUID`
  explicitly (`10000`), unaffected by this default change.

## Current state (inventoried 2026-07-03, re-verified 2026-07-11)

- Operator: `kubernetes/apps/volsync-system/volsync/` — OCIRepository
  `oci://ghcr.io/home-operations/charts-mirror/volsync-perfectra1n` tag
  0.18.5, mover image `ghcr.io/perfectra1n/volsync:v0.17.11`, dependsOn
  openebs + snapshot-controller. Sibling `volsync-maintenance` ks runs a
  fork-specific `KopiaMaintenance` CR.
- Repository: kopia filesystem repo on TrueNAS NFS
  (`${TRUENAS_IP}`:`${VOLSYNC_NFS_PATH}`), mounted by movers at `/repository`.
  Secrets carry only `KOPIA_PASSWORD` + `KOPIA_REPOSITORY`.
- Component `kubernetes/components/volsync/`: PVC (dataSourceRef →
  `ReplicationDestination ${APP}-dst`), ReplicationSource (12h cron, kopia,
  copyMethod Snapshot, `csi-ceph-blockpool`, zstd-fastest, retain 24h/7d),
  ReplicationDestination (`manual: restore-once`), per-app sops secret.
- ~18 consumers across `ai`, `default`, `media`, … via `components/volsync`
  with `VOLSYNC_*` substitutions (list: grep `components/volsync` in ks.yaml).
- Extras: kopia web-UI browser app (`volsync-system/kopia/`, keeps working —
  same repo), Grafana dashboard 21356, PrometheusRule (VolSyncComponentAbsent,
  VolSyncVolumeOutOfSync), mover-jitter MutatingAdmissionPolicy, and the
  restore runbook `docs/volsync-restore.md` (superseded in Phase 6/7 — don't
  leave it orphaned).
- Also touching volsync, confirmed by direct grep (2026-07-11), not
  mentioned in the original draft:
  - `kubernetes/components/common/namespace.yaml` sets
    `volsync.backube/privileged-movers: "true"` on **every** app namespace
    via the shared `components/common`. Every cross-referenced kopiur
    adopter that needs root-capable movers uses an equivalent
    `kopiur.home-operations.com/privileged-movers: "true"` annotation — but
    it's opt-in per namespace there, not blanket. Check in Phase 5 whether
    any app's mover actually needs it (candidate: **fileflows** — per
    memory, its image needs root at boot; unclear yet whether that carries
    over to its *backup mover*, which is a separate pod/identity — verify
    before assuming yes or no).
  - `kubernetes/apps/system-upgrade/tuppr/upgrades/{talosupgrade,kubernetesupgrade}.yaml`
    both gate node upgrades on `apiVersion: volsync.backube/v1alpha1, kind:
    ReplicationSource, expr: status.conditions.filter(c, c.type ==
    "Synchronizing").all(c, c.status == "False")`. onedr0p's real commit
    `16687a2a` replaced this exact healthCheck with kopiur equivalents
    (verbatim, confirmed against their live repo):
    ```yaml
    - apiVersion: kopiur.home-operations.com/v1alpha1
      kind: Snapshot
      expr: status.phase != 'Running'
    - apiVersion: kopiur.home-operations.com/v1alpha1
      kind: Restore
      expr: "!(status.phase in ['Resolving', 'Restoring'])"
    ```
    Phase 6 applies the same replacement to both files.
- Identity: the fork records snapshots as `<name>@<namespace>:/data` by
  default (no explicit username/hostname set in our component) — **confirmed
  independently** by vrozaksen/home-ops's own migration
  (`MIGRATION.md`, 2026-07-08, same fork): their SnapshotPolicy pins
  `identity.username: ${APP}`, `identity.hostname: ${NS}` with the comment
  "Pinned to the volsync fork identity (`<app>@<namespace>:/data`) so
  history in the adopted repo continues as one series." Phase 2 still
  re-verifies this against our own discovered snapshots before Phase 4 writes
  anything — don't skip that check just because another cluster confirms the
  pattern.

## Key design decisions

1. **Per-app PVC recreation is the chosen cutover mechanic — chosen, not
   forced, and now confirmed as kopiur's own documented pattern.**
   `docs/gitops.md` states this verbatim: "A bound PVC's `dataSourceRef` is
   immutable. Migrating an existing app onto the volume-populator restore
   path... is therefore a snapshot-gated delete-and-repopulate, not a silent
   `git push`." Recreating each PVC via the populator keeps the
   deploy-or-restore DR property volsync gives us today **and** proves every
   app's backup restorable. The non-destructive alternative (unmanaging the
   field) was rejected: it degrades cluster-rebuild DR to a manual restore
   per app, permanently, to save minutes per app now.
2. **Identity: use `kubectl kopiur migrate volsync`'s per-app pinning, not a
   repo-level CEL guess.** Revised from the original draft's plan to set
   `ClusterRepository.spec.identityDefaults.{hostnameExpr,usernameExpr}`
   cluster-wide. The official migration CLI instead pins
   `SnapshotPolicy.spec.identity` (+ `sources[0].sourcePathOverride`)
   explicitly per app, computed from the actual discovered
   `ReplicationSource`/fork state, and prints the pinned value for manual
   verification before apply — exact, not a best-effort expression matching
   a sanitization rule we'd have to reverse-engineer. Phase 2 runs the tool;
   Phase 4's reusable component parameterizes the identity fields the same
   way it already parameterizes everything else (`${APP}`), using the
   tool's output as the verified source of truth for the field shape.
3. **One `ClusterRepository`, credential projection on.** Replaces 18
   duplicated secrets. Confirmed as the standard pattern across all 8
   cross-referenced NFS-backend adopters.
4. **New component `kubernetes/components/kopiur/` with neutral `BACKUP_*`
   var names.** Only `VOLSYNC_CAPACITY` (16 apps) and `VOLSYNC_PUID/PGID`
   (hermes) are actually overridden in ks.yaml files, and each app's swap
   already edits the adjacent line — renaming there is free, and it avoids a
   kopiur component permanently branded `VOLSYNC_*`. The global
   `BACKUP_NFS_PATH` is added alongside `VOLSYNC_NFS_PATH` in Phase 2 (both
   components read their own during mixed-mode); the old one dies in Phase 6.
5. **Schedule**: same 12h cadence, de-lockstepped via `H` cron + jitter.
   Set `ClusterRepository.spec.scheduleDefaults.timezone: ${TIMEZONE}` once
   (0.5.0+ field — centralizes what used to be repeated on every
   SnapshotPolicy/SnapshotSchedule) using the existing cluster-settings
   `TIMEZONE` var (`Europe/Brussels`) — literal cron values live in Phase
   4's `snapshotschedule.yaml` only.
6. Operator lives in a new `kopiur-system` namespace — confirmed as the
   universal convention across every one of the 8 cross-referenced adopters,
   no exceptions found. The kopia browser app moves there in the final
   phase so `volsync-system` can be deleted.
7. **Monitoring comes from the kopiur chart, enabled at install time.**
   Field paths changed in the 0.6.0 values restructure: `monitoring.
   {dashboards.enabled, serviceMonitor.enabled, prometheusRule.enabled}` —
   not the old `grafanaDashboard`/`metrics.*` top-level keys. We don't run
   grafana-operator, so skip the `dashboards.grafanaOperator.*` sub-block
   several adopters use — plain `dashboards.enabled: true` is enough for a
   ConfigMap-based dashboard, matching how this repo already ships Grafana
   dashboards elsewhere. No hand-rolled alerts; the migration window is
   never blind.
8. **The kopiur controller pod must mount the NFS repo directly**, not just
   the movers. Confirmed by both upstream bug
   [#137](https://github.com/home-operations/kopiur/issues/137) (filesystem
   `Restore` fails with `/repo not mounted` without this) and every
   NFS-backend adopter's actual HelmRelease. Missing from the original
   draft — added to Phase 1's values block below.

---

## Phase 1 — Preflight + install the operator (no behavior change)

Preflight (read-only):
1. Verify worktree + branch (see header); `git pull && git status` on main
   first, branch from fresh main.
2. Confirm kopiur latest release is still `0.7.1`
   (`gh release list -R home-operations/kopiur -L 5`); if newer, check
   `docs/upgrade.md` and the release notes for breaking changes before
   updating the pin below — this project ships breaking chart-value
   restructures routinely (0.5.0, 0.6.0, 0.7.0 all did).
3. Install the `kubectl-kopiur` CLI plugin (krew:
   `kubectl krew install kopiur`, or the goreleaser binary/Homebrew cask
   from the 0.5.1 release) — needed for `migrate volsync` in Phase 2 and
   ad-hoc snapshot/status commands throughout.
4. Confirm CRDs ship inside the main chart (`helm show crds
   oci://ghcr.io/home-operations/charts/kopiur --version 0.7.1` — expect the
   8 kinds: `ClusterRepository, Maintenance, Repository,
   RepositoryReplication, Restore, SnapshotPolicy, Snapshot,
   SnapshotSchedule`, zero `BackupConfig`/`Backup`/`BackupSchedule`
   remnants — confirms ADR-0004's rename is fully applied at this tag).
5. Record current repo stats from the kopia browser UI (snapshot counts per
   identity) — the Phase 2 verification baseline.

Install — create `kubernetes/apps/kopiur-system/kopiur/`:

- `ks.yaml`: one Flux Kustomization, `kopiur` (dependsOn openebs +
  snapshot-controller). No separate CRDs Kustomization needed — Helm
  installs the chart's bundled `crds/` automatically on first install.
- `app/ocirepository.yaml`: `oci://ghcr.io/home-operations/charts/kopiur`,
  tag `0.7.1` (Renovate's Flux manager tracks OCI tags natively — no
  `.renovaterc.json5` change needed; verify on the next Renovate run, and
  read the PR body before merging given this project's breaking-change
  cadence).
- `app/helmrelease.yaml` values (0.6.0+ flattened shape — no `controller:`
  wrapper; webhook TLS self-provisions by default, nothing to set there):
  ```yaml
  installScope: cluster        # ClusterRepository needs ClusterRole
  extraVolumes:
    - name: repo
      nfs:
        server: "${TRUENAS_IP}"
        path: ${BACKUP_NFS_PATH}
  extraVolumeMounts:
    - name: repo
      mountPath: /repo
  podSecurityContext:
    runAsNonRoot: true
    runAsUser: 568
    runAsGroup: 568
    fsGroup: 568
    fsGroupChangePolicy: OnRootMismatch
  features:
    credentialProjection:
      enabled: true            # shared repo, movers in many namespaces
  monitoring:
    dashboards:
      enabled: true
    serviceMonitor:
      enabled: true
    prometheusRule:
      enabled: true            # KopiurBackupStale et al. — live BEFORE any data moves
  ```
- `$schema` annotations per repo convention
  (`https://k8s-schemas.home-operations.com/kopiur.home-operations.com/<kind>_v1alpha1.json`).

Verify: operator Ready, webhook serving, controller pod has `/repo` mounted
and readable (`kubectl exec` a quick `ls /repo`), `kubectl get crd | grep
kopiur.home-operations.com` → 8, kopiur alerts visible in the ruler and the
dashboard in Grafana. Nothing touches volsync; deleting the Kustomization
reverts everything.

**Status**: ☑ done — merged via PR #3800 (2026-07-11); all Verify criteria
above confirmed live in-cluster the same day (Kustomization `Ready=True`,
CRD count 8, 6 `PrometheusRule/kopiur-controller` alerts, dashboard present).
No volsync apps touched.

**Follow-up (2026-07-11): bumped `0.7.1` → `0.7.2`.** Routine per
`docs/upgrade.md` ("no action needed" — stable per-CR credential Secret
naming replaces the old per-run naming, `helm upgrade` adds the RBAC
`secrets` delete verb the cleanup needs). Directly relevant here: Phase 3's
`Maintenance` CR runs on a recurring cron with `credentialProjection`
enabled — exactly the leak this release fixes (0.7.1 minted a fresh
credential Secret copy per run, unbounded).

## Phase 2 — Adopt the repository via `kubectl kopiur migrate volsync`

In `kopiur-system`:

- `BACKUP_NFS_PATH` was already added to cluster-settings in Phase 1 (both
  live until Phase 6).
- **Deviation from the original plan**: `-f kubernetes/apps` (offline/GitOps
  mode) doesn't work in this repo — `ReplicationSource`/`ReplicationDestination`
  are raw manifests in the `volsync` Kustomize Component, substituted by
  Flux's `postBuild.substitute` (`${APP}`, `${TRUENAS_IP}`, etc.) only at
  apply time. Scanning the git tree directly picks up the literal
  placeholders, not resolved values. Ran against the **live cluster**
  instead (read-only; no `--apply`), per namespace (`-A` isn't accepted by
  `migrate volsync`):
  ```bash
  kubectl kopiur migrate volsync -n ai --resolve-secrets -v
  kubectl kopiur migrate volsync -n default --resolve-secrets -v
  kubectl kopiur migrate volsync -n media --resolve-secrets -v
  ```
- stderr accounting: 18 UNMAPPABLE `spec.kopia.moverVolumes` (expected —
  our repo is inline-NFS via `moverVolumes`, not a PVC, so the tool can't
  infer a `backend.filesystem.volume.pvc.name`; fixed manually below) and
  18 UNMAPPABLE `storageClassName`/`accessModes` staging overrides (expected
  per the tool's own docs — kopiur derives these from the source PVC, no
  per-policy override exists). No unexpected UNMAPPABLE entries.
- **The tool emits one namespaced `Repository` object per app** (not a
  single shared `_shared.yaml`), since each app has its own
  `${APP}-volsync-secret`. Confirmed via SHA-256 comparison of
  `KOPIA_PASSWORD` across 6 apps spanning all 3 namespaces (`ai`, `default`,
  `media`) that every app shares the **same password and the same repo
  path** (`filesystem:///mnt/repository`) — genuinely one repository, not
  N separate ones. Consolidated by hand into a single `ClusterRepository`
  instead of applying 18 redundant `Repository` objects (matching Phase 1's
  `installScope: cluster` + `credentialProjection` design intent).
- Fixed the backend: the `Repository`/`ClusterRepository` CRD's
  `backend.filesystem.volume` supports `oneOf: [pvc, nfs]` — used the
  inline `nfs: {server: ${TRUENAS_IP}, path: ${BACKUP_NFS_PATH}}` form
  (same NFS export as Phase 1's operator-level `/repo` mount and the
  fork's own `moverVolumes`), not a PVC.
- `allowedNamespaces.list: [ai, default, media]` — explicit, matching the
  3 namespaces with live volsync apps today (least-privilege for a
  password-bearing cluster-scoped resource; extend when a new namespace
  onboards).
- `create` and `maintenance` left absent (adopt in place; Phase 3 takes
  explicit ownership-transfer control).
- `encryption.passwordSecretRef` has an explicit `namespace: kopiur-system`
  (kopiur#232) and points at a **new** `kopia-nas-password` secret in
  `kopiur-system` (sops-encrypted, password copied from the live
  `ai/hermes-volsync-secret` and verified byte-identical via SHA-256 —
  the plaintext was never printed to any log or chat output) — not one of
  the 18 existing per-app secrets, since those get deleted in Phase 6.
- Applied the (renamed to match this repo's convention)
  `kubernetes/apps/kopiur-system/kopiur/repository/{clusterrepository.yaml,secret.sops.yaml}`,
  reconciled by a second Flux `Kustomization` document
  (`kopiur-repository`, `dependsOn: [kopiur]`) appended to
  `kopiur/ks.yaml`, mirroring the `volsync`/`volsync-maintenance`
  multi-document pattern exactly (including redeclaring `&namespace` in
  the second document — kustomize's YAML parser does **not** share
  anchors across `---`-separated documents, despite `volsync-maintenance`
  appearing to alias across documents at first glance).
- Per-app `SnapshotPolicy`/`SnapshotSchedule` objects (identities verified
  to match `<app>@<namespace>:/data` for all 18 live apps via the dry-run)
  are **not** applied in this phase — that's Phase 4's reusable Component +
  canary, not 18 hand-copied one-off files.

Verify (the load-bearing step of the whole plan):
1. `ClusterRepository` reaches Ready with `create` untouched (wrong password
   = hard AuthFailure, cannot corrupt).
2. Discovered `Snapshot` CRs appear (`kubectl get snapshots -A -l
   kopiur.home-operations.com/origin=discovered`) and their **identities
   exactly match** `<app>@<namespace>:/data` for every app — cross-check
   against the per-app identity the migrate tool pinned in its generated
   output, not just eyeballed.
3. Snapshot counts match the Phase 1 baseline (recorded 2026-07-11 via
   `kubectl -n volsync-system exec <kopia-pod> -- kopia snapshot list --all`,
   23 identities, not 18 — see next bullet).
4. Baseline includes 5 identities with no live app or git reference at all:
   `moltis@ai`, `music-assistant@media`, `n8n@ai`, `open-webui@ai`,
   `trilium@default` — decommissioned apps, orphaned snapshot history only.
   Expected to surface as `discovered` Snapshot CRs with no corresponding
   SnapshotPolicy anywhere in this repo; not a migration target, don't chase
   them into Phase 4/5's per-app table.

**Rollback**: delete the ClusterRepository — catalog rows vanish, repo data
untouched (discovered snapshots are Retain-forced).

### Root cause found during live verification: repo-wide permission drift

Manifests merged clean via PR #3808, but the bootstrap Job then hung
silently — no error, near-zero CPU, only its two startup log lines — across
three separate attempts with deadlines raised 120s → 600s → 2700s
(PRs #3817, #3819). None of it was a timeout problem. Root-caused by
attaching a `kubectl debug` ephemeral container sharing the mover's process
namespace and reading kopia's own internal CLI log (`/var/cache/kopia/logs/
cli-logs/*.log` on the mover's `emptyDir` cache, independent of the
`RUST_LOG`-gated stderr wrapper — this is the technique to reach for first
next time a mover looks silently stuck):

- **1,943 of 3,409 directories (57%) and 7,089 of 8,139 files (87%)** in the
  NFS repo had drifted to mode `0700`, owned by `568:568` — readable only by
  that exact UID. `kopiur-mover` runs as `65532:65532` and, on every
  `PermissionDenied` from a directory walk, kopia retries forever with
  capped exponential backoff (never surfaces an error) — so the Job just
  spun silently until whatever deadline was configured killed it. Fixed
  directly on the NAS data (not git-tracked): `find /repo -type d -not
  -perm 0775 -exec chmod 0775 {} +` and the file equivalent, matching the
  already-working minority permission mode. ~2 minutes total, no data
  touched, reversible.
- Manual `kopia` CLI testing during triage was misleading at first — it ran
  as root (default in the `kopia/kopia` debug image), which bypasses Unix
  DAC entirely, so every manual connect/list/status check looked instantly
  fast while the real (non-root) mover was stuck. Don't trust a root-shell
  repro when the failing process runs non-root — reproduce with matching
  UID/GID, or go straight to inspecting the actual stuck process.
- A concurrent, *unrelated* distraction during the same troubleshooting
  window: `control-2`'s known iGPU GTT leak (`qwen3-embedding`/
  `vmcp-embedding`, see memory) flared up (`NodeNotReady x3 over 146m`) and
  SIGKILLed two bootstrap attempts that happened to land there. Fixed by
  deleting the leaking embedder pods (standard recovery, no reboot). Cost
  real debugging time before the node-event evidence separated it from the
  actual (unrelated) permission-drift root cause.
- Once the repo's read permissions were fixed, bootstrap read-succeeded but
  a *second*, narrower gap surfaced: `kopiur-mover` (`65532:65532`) could
  read everything but couldn't create new paths (`mkdir /repo/s7a:
  permission denied`) — `/repo` and its contents are `568:568`, group-write
  only, and the mover isn't in group 568. Fixed via `spec.moverDefaults.
  securityContext.runAsGroup: 568` on the `ClusterRepository` (PR #3823,
  live-verified with a throwaway UID 65532/GID 568 pod before merging) —
  no repo permission changes, no TrueNAS-side reconfiguration (568 is a
  cluster/NAS-wide TrueCharts-derived convention, confirmed impractical to
  change at the source).
- **Open question, not yet root-caused**: *why* the repo drifted to a mixed
  `0700`/`0775` state in the first place is unknown — plausibly a change in
  kopia's own directory/file creation mode across the fork's version
  history, but unconfirmed. Since the fork keeps writing (dual-write
  through Phase 5) with its own process running as `568:568`, and kopiur's
  own movers now default to `568` too, **watch for recurrence**: if
  either side's kopia binary creates *new* blobs at a restrictive mode
  again, the other side's reads/writes on that new content could silently
  degrade the same way. No monitoring for this exists yet — a stuck-mover
  investigation should check `find /repo -type d -not -perm 0775` /
  `-type f -not -perm 0775` early, not last.

Verified 2026-07-11: `ClusterRepository/kopia-nas` reached `phase: Ready`
(`Bootstrapped: True`, `IndexBlobHealth: True`), 493 discovered `Snapshot`
CRs materialized across exactly 23 unique identities — the 18 live apps
plus all 5 orphaned identities from the Phase 1 baseline, each landing in
the correct namespace, no extras and no gaps.

**Status**: ☑ done — merged via PR #3808 (manifests), #3817 + #3819
(bootstrap deadline, superseded by the real fix below), #3823 (mover
write-access GID). `ClusterRepository/kopia-nas` `Ready` in-cluster,
2026-07-11.

## Phase 3 — Maintenance ownership takeover

Order matters (kopiur's own docs carry the identical warning verbatim:
*"kopiur manages repository maintenance itself and takes over kopia's
maintenance ownership (`kopia maintenance set --owner`) on its first run. A
fork `KopiaMaintenance` left running will fight kopiur over ownership —
delete it once the adopted repository is `Ready`."*):

1. Add standalone `Maintenance` CR in `kopiur-system` referencing the
   ClusterRepository: `spec.repository: {kind: ClusterRepository, name:
   kopia-nas}`, `spec.schedule: {quick: {cron: "H */6 * * *", jitter:
   30m}, full: {cron: "H 3 * * 0", jitter: 1h}}`,
   `ownership.takeoverPolicy: PromptCondition`.
2. Wait for the `MaintenanceYielding`/prompt condition (fork holds the lease).
3. Suspend + remove the `volsync-maintenance` Flux Kustomization (the fork's
   `KopiaMaintenance` CR and its secret).
4. Flip `takeoverPolicy: Force`, wait for lease acquisition + one successful
   quick run, then revert to `Never`.

**Ownership transfer confirmed directly (2026-07-12).** The `Maintenance` CR
reached `LeaseClaimed: True` under `takeoverPolicy: PromptCondition` almost
immediately with no observed lock-file conflict — `spec.ownership` on the
CR is a separate, kopiur-internal bookkeeping value, not what's actually
stamped into the repo. The real kopia-level owner was verified two ways:
`kubectl get maintenance kopia-nas -n kopiur-system` shows
`OWNER: kopiur@kopiur-clusterrepository-kopia-nas`, and a one-shot debug
pod running `kopia maintenance info` directly against the NFS repo
confirmed the identical string, zero occurrences of the old
`maintenance@volsync` owner anywhere in the output (delegated verification,
`kubectl exec` against a live debug pod was unreliable in this environment
across ~5 attempts — a one-shot Job + `kubectl logs` worked). Conclusion:
step 4's explicit `Force` takeover was never needed — the mover's
connect-to-existing bootstrap self-heal (re-stamps the owner if it differs,
best-effort, see `crates/mover/src/main.rs`) already did it silently on an
earlier bootstrap re-run once write permissions were fixed in Phase 2.
`takeoverPolicy` was left at `PromptCondition`, not touched.

**New finding, extends the Phase 2 permission-drift writeup**: the
verification pod found kopiur writes new blobs as `65532:568` mode
`0600`/`0700` (owner-only) — the group-568 bit that made the legacy fork
files group-readable is *not* set on kopiur-written content. This doesn't
block kopiur itself (its mover owns the files via UID), but it's the exact
mechanism the Phase 2 "open question" flagged: anything that reads via
group-568 only (the fork's still-active `568:568` mover, ad-hoc debug
tooling) will silently retry-forever on any blob kopiur wrote after the
2026-07-11 chmod fix. This is a live, confirmed, ongoing risk for as long
as the fork mover keeps running — not hypothetical.

Given that, the fork's `volsync-maintenance` Kustomization is being
disabled now rather than left running dual-write: `KopiaMaintenance/daily`'s
own `spec.enabled: false` (native toggle, not a Flux suspend — Flux suspend
wouldn't stop the CronJob already applied) via PR, ahead of its next
trigger (`30 3,15 * * *`). Full removal (delete the ks + CR + secret) still
deferred to Phase 6 per the original plan; this only disables it.

**Rollback**: flip `KopiaMaintenance/daily`'s `spec.enabled` back to `true`.
There's no kopiur-side Maintenance CR left to revert — re-add
`maintenance.yaml` with `enabled: false` on the ClusterRepository if manual
control is ever needed again.
**Status**: ☑ done — kopia-level owner directly confirmed 2026-07-12
(`kopiur@kopiur-clusterrepository-kopia-nas`). Fork's `KopiaMaintenance/
daily` disabled same day (PR #3831) to close the group-568-read gap above;
full removal still Phase 6. Maintenance now fully operator-managed
(defaults: quick 6h, full daily 03:00) — the explicit `Maintenance` CR was
deleted as unnecessary complexity (PR pending).

**Second bug found 2026-07-12, while checking cluster health before Phase
4**: `kopiur-repository`'s Kustomization was intermittently `Ready: False`
(`HealthCheckFailed... Maintenance/kopiur-system/kopia-nas status:
'Failed'`). Root cause: our `ClusterRepository` never set
`spec.maintenance.enabled: false`. That field **defaults to `true`**
("the operator manages a `Maintenance` CR for this repository"), so the
operator had been auto-creating and self-reconciling its own
default-managed `Maintenance/kopia-nas` (schedule `0 */6 * * *` quick /
`0 3 * * *` full **daily**, `ownership: {owner:
kopiur/clusterrepository/kopia-nas, takeoverPolicy: Never}`) the whole
time — colliding on the exact same name as our git-authored, explicit
`Maintenance/kopia-nas` from step 1 above. Flux would apply our spec
(`owner: kopiur/kopia-nas`, `takeoverPolicy: PromptCondition`, weekly
full `H 3 * * 0`), the operator's own auto-management reconcile would
reset it back moments later, repeat forever — a live spec-level tug of
war, not just a cosmetic drift. `deploy/examples/scenarios/
05-adopt-existing-repo.yaml` documents exactly this: adopting an existing
repo must set `maintenance.enabled: false` so the deliberate, explicit
takeover Maintenance CR governs instead of the auto-managed one. The
kopia-level lease itself (fable's verified `kopiur@
kopiur-clusterrepository-kopia-nas`) was never at risk — that string is
computed from the `ClusterRepository` directly, independent of which
Maintenance CR governs — but the actual **schedule** running in-cluster
was silently wrong (daily full instead of weekly) for as long as this
went uncaught, and the Kustomization's flapping health status made this
Phase look green when it wasn't fully settled.

**Resolution (simplified, 2026-07-12): deleted the explicit `Maintenance`
CR entirely instead of fighting the auto-managed one.** Step 1's
hand-authored object (weekly full, `H`-hashed quick, deliberate
`PromptCondition` takeover) added config to maintain for close to zero
real benefit here — the takeover already happened via the mover's own
bootstrap self-heal regardless of this CR, and a single-repository
homelab has no thundering-herd reason to hash/jitter around a literal
`:00`. Removed `kubernetes/apps/kopiur-system/kopiur/repository/
maintenance.yaml`, left `ClusterRepository.spec.maintenance` unset
(defaults to `enabled: true`) so the operator's own default-managed
`Maintenance/kopia-nas` owns the object outright, accepting its defaults:
quick every 6h, full **daily** at 03:00 (not the originally-planned
weekly). No more competing writers, no more flapping health check.

## Phase 4 — New component + canary app

Create `kubernetes/components/kopiur/` (neutral `BACKUP_*` vars — see
decision #4; field shapes below match kopiur 0.7.1's `deploy/examples/`,
not the stale README):

- `snapshotpolicy.yaml` — `SnapshotPolicy ${APP}`: repository →
  `{kind: ClusterRepository, name: kopia-nas}`; `sources[0].pvc.name: ${APP}`;
  `spec.identity` pinned per decision #2 (verify the exact field shape the
  Phase 2 migrate-tool output used — likely `spec.identity: {username:
  ${APP}, hostname: <namespace, via Flux targetNamespace not a var>}` plus
  `sources[0].sourcePathOverride: /data`); **no `copyMethod` field** — it
  was removed in 0.5.0 and now defaults to `Snapshot`; compression
  `{compressor: zstd}`; retention `keepHourly: 24, keepDaily: 7` (mapped
  from volsync's `retain`); `volumeSnapshotClassName:
  ${BACKUP_SNAPSHOTCLASS:=csi-ceph-blockpool}`; `credentialProjection.enabled:
  true` (opt-in, off by default — confirm still wanted here). Mover
  identity comes from `ClusterRepository.spec.moverDefaults` (Phase 1/2), no
  per-policy override needed unless an app needs different uid/gid (hermes:
  10000/10000 — override `spec.mover.securityContext` there).
- `snapshotschedule.yaml` — `policyRef: {name: ${APP}}`,
  `schedule: {cron: "H */12 * * *", jitter: 30m}` (Forbid is the schema
  default).
- `restore.yaml` — `Restore ${APP}-restore`: `source.fromPolicy: {name:
  ${APP}, offset: 0}`, `target.populator: {}` (the **empty object is
  required explicitly** — a bare/missing `target` is invalid per
  ADR-0005 §9), `policy.onMissingSnapshot: Continue` (deploy-or-restore:
  fresh cluster with empty repo → empty PVC, existing repo → restore).
- `pvc.yaml` — same as today's but `dataSourceRef` → `{apiGroup:
  kopiur.home-operations.com, kind: Restore, name: ${APP}-restore}` and
  `${PVC_CAPACITY:=5Gi}` / `${PVC_STORAGECLASS:=ceph-block}` /
  `${PVC_ACCESSMODES:=ReadWriteOnce}` — named for what they actually size
  (the app's real PVC), not `BACKUP_*`; there's no separate "backup
  storage" concept in either volsync or kopiur, both mechanisms
  parameterize the live app volume directly (the old `VOLSYNC_CAPACITY`
  had the exact same property, just unnoticed).
- `kustomization.yaml` (Component) wrapping the four.

**Canary: `dumbassets`** (1Gi, `default`, lowest value). Cutover recipe:

1. `flux suspend kustomization dumbassets`; scale app to 0.
2. Trigger a final volsync backup (`kubectl patch replicationsource dumbassets
   ... trigger.manual`) and wait for completion — the restore point is fresh.
3. In git: swap `components/volsync` → `components/kopiur` in the app's
   ks.yaml and rename any `VOLSYNC_*` substitute overrides to `BACKUP_*` on
   the same lines.
4. Delete the old PVC only (`kubectl delete pvc dumbassets -n default` — the
   data lives in the repo now). The ReplicationSource, ReplicationDestination,
   and `${APP}-volsync-secret` left the git tree in step 3; Flux `prune: true`
   removes them on reconcile — confirm they're gone rather than pre-deleting.
5. Push + reconcile (`task reconcile` or `flux reconcile kustomization
   dumbassets --with-source`): kopiur Restore resolves the latest snapshot,
   populator fills the new PVC, app scales back up.
6. Verify: app data intact; `kubectl kopiur snapshot now --policy dumbassets
   -n default --wait` lands a NEW snapshot whose identity (from the Snapshot
   CR status / `kubectl kopiur snapshot list`) **equals the discovered
   history's identity**.
7. Watch one scheduled run fire at its hashed minute (not :00) — proves the
   `H`-hash/jitter/Forbid controller logic once, live.
8. From this point on, **treat this app's `Restore` CR as write-once** — see
   the Risk gate's #233 note. Don't force-reconcile or prune-and-recreate its
   Kustomization without checking for orphaned `prime-*` PVCs afterward.

**Component built 2026-07-12** — `kubernetes/components/kopiur/` (4 files +
`kustomization.yaml`), verified with a standalone `kustomize build` against
the component before wiring any app. Two deviations from the draft above,
both confirmed against `crates/api/src/identity.rs` (`resolve_identity`),
not guessed:
- **No `spec.identity` override needed at all.** The default `username` is
  the `SnapshotPolicy`'s own `metadata.name`, the default `hostname` is its
  `metadata.namespace` — since both objects are named `${APP}` in the app's
  own namespace, the defaults already resolve to exactly `<app>@<namespace>`,
  matching the fork's recorded identity with zero config. Only
  `sources[0].sourcePathOverride: /data` is needed, because kopiur's own
  default source path is `/pvc/<name>`, not `/data` — the fork's mover
  always recorded `/data` regardless of the app's real container mount
  path, so this one field is what actually preserves continuity.
- Added `ClusterRepository.spec.credentialProjection.allowed: true` (the
  repository-owner gate) — the draft's per-policy `credentialProjection.
  enabled: true` is necessary but not sufficient; the CRD requires the gate
  set on the owning `ClusterRepository` too, or every mover Job 403s
  copying the credential Secret into its own namespace.

**Rollback (component)**: unreferenced by any app yet — delete the
directory, nothing live depends on it.

**Canary cutover executed 2026-07-12.** Followed the recipe: suspended the
Kustomization + scaled to 0, triggered a final volsync backup, swapped
`components/volsync` → `components/kopiur` in git (PR #3835, also renamed
`BACKUP_*` → `PVC_*`), deleted the old PVC, resumed + reconciled.

Hit the mirror-image of the Phase 2/3 permission bug live, twice, mid-cutover:
- The final volsync backup (and 3 *other* apps' concurrently-running
  scheduled backups — changedetection, karakeep, nextcloud) all hung
  silently on `kopia.maintenance.f` going `0600`/uid-65532 after the
  Phase 3 Maintenance simplification. Fixed with the same repo-wide chmod,
  this time run as uid `65532` via a throwaway pod (root-in-pod couldn't
  chmod uid-65532-owned files itself — the NFS export has `root_squash`,
  so only the true owning UID can self-chmod over NFS).
- The first `Restore` attempt then failed outright (kopiur's mover has a
  **bounded** retry-then-fail, unlike raw kopia's retry-forever — a real
  improvement) on a *fresh* batch of ~60 blobs another app's volsync
  backup had just written as `568:568` mode `0600` in the few minutes
  between the chmod and the restore — confirming the fork's own kopia
  v0.22.3 binary also defaults to a restrictive create mode, not just
  kopiur's. This is what PR #3836 (durable fix, merged same day) targets:
  `components/volsync`'s `moverSecurityContext.runAsUser` default `568` →
  `65532`, aligning the fork onto kopiur's own mover UID so both sides can
  read each other's new writes regardless of either binary's create-mode
  default. Fixed the immediate drift with one more repo-wide chmod (as uid
  `568` this time), force-reconciled all 17 remaining volsync apps so they
  picked up the new UID immediately, deleted the failed (terminal, kopiur
  Restores don't self-retry) `Restore` CR, and let Flux recreate it fresh.
- Second attempt succeeded cleanly.

**Verified**: app data intact post-restore (`Assets.json`/`SubAssets.json`,
byte-identical, original `Jul 19 2025` timestamps preserved); a fresh
kopiur snapshot resolved identity `dumbassets@default:/data` — an **exact
match** to the fork's historical identity, confirming timeline continuity;
a fully independent restore test (`kubectl kopiur`-equivalent `Restore` CR
with `target.pvc` into a throwaway PVC, not the migration's own populator
path) landed the same byte-identical data, proving the read path
end-to-end, not just the one-time migration populator.

**Not yet done**: watching one `SnapshotSchedule`-fired run land at its
hashed (non-`:00`) minute (plan step 7) — the manual snapshot above proved
the mechanism but not the cron/jitter/hash logic specifically; check back
within 12h. Per the Risk gate's #233 note, `dumbassets-restore` is now
write-once — don't force-reconcile or prune-and-recreate its Kustomization
without checking for orphaned `prime-*` PVCs first.

**Status**: ☑ done. Component built + wired; canary (`dumbassets`) fully
cut over, verified, and round-trip-tested. Surfaced and fixed a real,
previously-undocumented bidirectional permission-collision risk between
kopiur and the fork (PR #3834, #3836) before it could affect the
fleet-wide cutover in Phase 5.

## Phase 5 — Fleet-wide cutover (single flip, not staged batches)

Revised from the original per-namespace/multi-session batching: onedr0p's
real migration ran kopiur dual-write across their *entire* fleet first
(SnapshotPolicy+SnapshotSchedule wired everywhere, volsync's PVC/Restore
still live), proved every app's backups worked, and only then flipped every
PVC in one near-simultaneous pair of commits. Combined with the canary above
already having proven the mechanism once, do the rest of the fleet as one
pass, not a multi-day schedule:

1. Wire `components/kopiur` onto every remaining app's ks.yaml **alongside**
   `components/volsync` (dual-write, matching onedr0p's `f63c6a06` step) —
   but only the `snapshotpolicy.yaml` + `snapshotschedule.yaml` half; do NOT
   include `pvc.yaml`/`restore.yaml` in this pass (split the component's
   `kustomization.yaml` into two — `backup/` and `restore/` — so this step
   can enable one half without the other, same pattern onedr0p and every
   cross-referenced adopter with `components/kopiur/{backup,pvc}` or
   `{kopiur,restore}` split use). **Done 2026-07-12**: split into
   `components/kopiur/{backup,restore}/`, each its own Component; the
   top-level `components/kopiur/kustomization.yaml` now composes both via
   nested `components:` (kustomize supports Components referencing other
   Components), so `dumbassets` (already fully cut over) needs no changes —
   `flate test all` re-verified clean post-split. Dual-write apps in this
   phase reference `components/kopiur/backup` alone.
2. Push, reconcile, confirm every app has produced at least one real kopiur
   snapshot (script this — `(app, namespace)` pairs, `kubectl kopiur
   snapshot list --policy $app -n $ns`) before touching any PVC.
3. One commit per namespace (or one big commit — no cross-app dependency,
   independent PVCs) that: suspends+scales each app, fires a final volsync
   backup, swaps `components/volsync` out entirely (deletes the PVC
   alongside it — same-commit delete+recreate keeps this within one Flux
   reconcile, avoiding a window where the app has no PVC definition at all),
   and enables the `pvc.yaml`/`restore.yaml` half of the kopiur component.
4. Push once, reconcile, scale everything back up. Verify per-app identity
   continuity the same way the canary did (scriptable).
5. Remove `components/volsync` reference and old secrets fleet-wide (Flux
   `prune: true` handles the object cleanup once the git tree no longer
   references them).

Order within the batch, lowest-risk first: remaining `default` apps →
`media` → `ai` (hermes carries `BACKUP_PUID/PGID: 10000`) → **nextcloud
alone, last** (50Gi: the populator restore will take a while — run it in a
quiet window; its database is CNPG-backed, not volsync — only the PVC
moves). Check fileflows specifically for the
`kopiur.home-operations.com/privileged-movers` question flagged in Current
state before assuming the default mover identity works for it.

**Step 1 done 2026-07-12** for all 16 remaining apps except `nextcloud`
(deliberately deferred, see below): wired `components/kopiur/backup` onto
each app's `ks.yaml`, alongside `components/volsync` (dual-write). No new
substitution vars needed — the backup half only reads `${APP}` (already
present everywhere) plus the SnapshotSchedule/Policy's own hardcoded
defaults. **Deviation for hermes**: didn't use the shared component at
all — `spec.mover.securityContext` (the `10000:10000` override) can't be
expressed as a Flux `postBuild.substitute` var without adding a
conditionally-empty field to every *other* app's SnapshotPolicy too, and
Kustomize's own `patches:` can't target it by name (`${APP}` is still an
unresolved literal at `kustomize build` time — Flux's substitution is a
text pass *after* the build, not before). Instead hermes gets its own
hand-authored `snapshotpolicy.yaml`/`snapshotschedule.yaml` directly in
`kubernetes/apps/ai/hermes/app/`, matching the shared component's shape
with `hermes` hardcoded and the mover override added. Verified via
`flate build ks` for both a normal app (changedetection) and hermes — 17
`SnapshotPolicy`/`SnapshotSchedule` pairs total tree-wide (16 dual-write +
the already-cut-over `dumbassets`), no duplicates.

**nextcloud excluded from this pass on purpose** — wiring it now would
start real (if low-frequency) kopiur backups of a 50Gi volume before its
turn; add it alongside its actual cutover instead, per its own row.

**Step 2 done 2026-07-12: reconciled all 16 dual-write apps and triggered
`kubectl kopiur snapshot now --policy <app> -n <ns> --wait` for each.
Result: 7/16 succeeded, 9/16 + hermes failed** (`fileflows`'
privileged-movers question resolved cleanly — it succeeded, no issue).

**Succeeded** (mover's default identity `65532:568` happens to be able to
read the app's PVC data): brrpolice, qbittorrent, fileflows, seerr, bazarr,
qui — plus `dumbassets` from Phase 4.

**Failed, all `PermissionDenied` reading the app's own PVC data** (a
different failure mode than Phase 2/3's shared-repo permission drift —
this is about kopiur's mover reading each app's *source* files, not the
repository): changedetection, karakeep, sonarr, wizarr, prowlarr, radarr,
jellyfin, odysseus, opencode, hermes. Checked each failing app's live pod
identity (`kubectl exec <pod> -- id`):
- **uid=0 (root)**: changedetection, jellyfin, odysseus, opencode, wizarr
- **uid=568, but the specific failing files are mode `0600` owner-only**
  (ASP.NET Core Data Protection keys — deliberately restrictive):
  sonarr, prowlarr, radarr
- **uid=1000**: karakeep
- **uid=10000**: hermes (the hand-authored `mover.securityContext`
  override from step 1 — turns out it wasn't sufficient, see below)

**Root cause, confirmed by a fable-delegated investigation (agent
`a582f664b0bf87c09`) with a live test on `sonarr`**: kopiur's own
repository blobs are *always* written mode `0600`, owned `65532` — kopia's
hardcoded default file-creation mode, which kopiur exposes no override
for. This means **any mover identity other than uid `65532` cannot even
connect to the shared repository at all** — it fails at the index-blob
read step, before ever touching the app's source PVC. Confirmed live:
patching `SnapshotPolicy/sonarr` with `spec.mover.
inheritSecurityContextFrom.pvcConsumer: {}` (inheriting sonarr's own
uid 568) made the mover correctly match sonarr's PVC data, but it then
failed at `repository connect` instead — `permission denied` on
`/repo/xn1/88_/...`, same as every other diagnosed instance of this bug
class this migration. Policy reverted cleanly after the test.

This is a **hard, structural conflict**, not a per-app misconfiguration:
the mover needs uid `65532` to read the shared repo, and needs each app's
own uid to read that app's source data — a single process can't be both.
`inheritSecurityContextFrom` (and hermes's hand-authored uid override)
solve the second half while breaking the first half. Confirmed this also
rules out simply setting `runAsUser: 0` (root) for the 5 root-uid apps:
the kopia repo NFS export has `root_squash` enabled (confirmed earlier
this session — even `kubectl exec`-as-root couldn't `chmod` files it
didn't own), so an inherited/explicit root mover would get squashed to an
anonymous identity server-side and fail identically.

**Why this didn't block Phase 2–4**: `dumbassets` and the 6 apps that
already worked all coincidentally have PVC data already readable by uid
`65532` (or group `568`, with sufficiently permissive file modes) — pure
luck, not a validated pattern. This was never actually solved earlier in
the migration, just not yet triggered.

**Decision (2026-07-12, explicit choice over two other real options —
adding a Linux capability to the mover while keeping uid 65532, requiring
the `privileged-movers` security-gate opt-in; or indefinitely deferring
these 9 apps): fix at the TrueNAS NFS export level with `Mapall`.**
Setting a fixed **Mapall User/Group** on the kopia repo's NFS export
(`/mnt/TanguilleServer/VolsyncKopia`, shared as `${BACKUP_NFS_PATH}`)
makes the NFS *server* treat every connecting client as one fixed
identity, regardless of what uid the connecting pod actually claims. This
cleanly decouples the two conflicting requirements: the mover's *local*
process uid still varies per app (via `inheritSecurityContextFrom`,
solving the source-PVC read), while its *NFS-repo* identity becomes
uniform (solving the repo-connect problem) — permanently, not just for
existing content, since Mapall remaps identity for *all* future
connections too, not only a one-time chmod.

**Action needed (manual, TrueNAS web UI, not git-trackable)**: edit the
NFS share for this export → Advanced Options → set **Mapall User** and
**Mapall Group** to the existing system user/group with uid/gid `568`
(the cluster's long-standing NAS-wide convention). This does **not**
retroactively fix current file modes — a repo-wide `chmod 0775` pass
(same command used twice already this migration) will still be needed
once Mapall is live, to normalize the `0600` blobs kopiur has already
written under the old identity mismatch. After Mapall + one more chmod
pass, re-run step 2's manual-snapshot verification for the 9 failing apps
(with `inheritSecurityContextFrom.pvcConsumer: {}` added to their
SnapshotPolicy) and for hermes (drop the hand-authored uid override in
favor of the same inherit mechanism, once confirmed it works).

**Status**: ☐ blocked on the TrueNAS Mapall change above (manual,
outside this repo) — 7/17 apps verified working, 9/17 + hermes blocked.
Not proceeding to step 3 (the actual PVC flip) for *any* app yet, to keep
Phase 5 a single coherent pass rather than a fragmented partial rollout.

| App | NS | Kopiur snapshot works | Cut over |
|-----|----|----|------|
| dumbassets (canary, Phase 4) | default | ☑ | ☑ |
| brrpolice | media | ☑ | ☐ |
| qbittorrent | media | ☑ | ☐ |
| fileflows | media | ☑ | ☐ |
| seerr | media | ☑ | ☐ |
| bazarr | media | ☑ | ☐ |
| qui | media | ☑ | ☐ |
| changedetection | default | ☐ blocked (root) | ☐ |
| karakeep | default | ☐ blocked (uid 1000) | ☐ |
| jellyfin | media | ☐ blocked (root) | ☐ |
| prowlarr | media | ☐ blocked (568, 0600 files) | ☐ |
| radarr | media | ☐ blocked (568, 0600 files) | ☐ |
| sonarr | media | ☐ blocked (568, 0600 files) | ☐ |
| wizarr | media | ☐ blocked (root) | ☐ |
| hermes | ai | ☐ blocked (10000, needs inherit not hardcode) | ☐ |
| odysseus | ai | ☐ blocked (root) | ☐ |
| opencode | ai | ☐ blocked (root) | ☐ |
| nextcloud (50Gi, last) | default | not tested (unwired on purpose) | ☐ |

Backups fire every 12h — don't leave an app suspended across a window without
its final manual backup (step 3 covers this).

## Phase 6 — Decommission volsync + observability

Only when every row above is ✔:

1. Remove `kubernetes/apps/volsync-system/volsync/` entirely (HelmRelease,
   OCIRepository, mover-jitter MutatingAdmissionPolicy, PrometheusRule,
   Grafana dashboard, both ks). CRDs go with `manageCRDs` on uninstall;
   confirm no `volsync.backube` CRs remain first
   (`kubectl get replicationsources,replicationdestinations -A`).
2. Move the kopia browser app to `kopiur-system` (stateless UI; same NFS
   mount), then delete the `volsync-system` namespace dir + entry.
3. Remove `kubernetes/components/volsync/` and the now-unused
   `VOLSYNC_NFS_PATH` (and any other `VOLSYNC_*`) cluster-settings vars.
4. Remove `volsync.backube/privileged-movers: "true"` from
   `kubernetes/components/common/namespace.yaml`; add the kopiur equivalent
   only for namespaces Phase 5 actually found need it.
5. Replace the VolSync healthCheck in both
   `kubernetes/apps/system-upgrade/tuppr/upgrades/{talosupgrade,kubernetesupgrade}.yaml`
   with the kopiur equivalent (onedr0p's real, live replacement — see
   Current state above for the exact CEL exprs).
6. Monitoring is already live from Phase 1 (chart-shipped). Here: confirm the
   shipped alerts covered both volsync alerts' intent (operator-absent →
   `KopiurRepositoryNotReady`/`KopiurReconcileErrorsHigh`; volume-out-of-sync
   → `KopiurBackupStale`), and tune `backupStaleAfterSeconds` if the default
   is looser than today's threshold.
7. Confirm the Ceph write-latency picture post-migration: the 112-mover
   lockstep spikes should be gone (compare
   `max(rate(ceph_osd_op_w_latency_sum…))` around backup hours before/after),
   and every `SnapshotSchedule` has fired at least once at its hashed minute.

**Status**: ☐ not started

## Phase 7 — Documentation pass & cleanup

- Rewrite `docs/volsync-restore.md` as the kopiur restore runbook (keep its
  suspend → scale → restore → verify skeleton; the Phase 4 recipe supplies
  the kopiur specifics) — supersede, don't orphan.
- Consolidate what's durable into `docs/` (backup architecture: kopiur CRD
  layout, identity scheme, the #233 write-once-Restore operating rule —
  that one needs to survive as a standing caveat, not just live in this
  plan file).
- Update memory: volsync→kopiur completed; mover-storm fix verified; delete
  the stale mover-storm attribution caveats.
- Delete this plan file once consolidated (per process below).
- Remove the worktree: `git worktree remove .worktrees/kopiur-migration`.

**Status**: ☐ not started

---

## Process Instructions

- After completing each step, update the plan with the current status.
- Pause for user confirmation before proceeding to next step.
- Suggest the prompt for continuing to the next step.
- After the last step, make a final documentation pass. Once the contents of
  the plan have been consolidated into existing documentation, the plan file
  can be removed. If there is no relevant existing documentation, the plan
  should be reworked into a reference document.

**Important**: Every prompt should verify the branch and worktree before
doing any work.
