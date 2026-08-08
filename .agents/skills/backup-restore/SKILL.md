---
name: backup-restore
description: >-
  Manage kopiur Kopia-based backups and restores for Kubernetes PVs in this cluster.

  user: "check backup status for app X" → describe the SnapshotPolicy/SnapshotSchedule
  user: "restore from backup" → follow the restore workflow below
  user: "trigger manual backup" → `kubectl kopiur snapshot now --policy <app> -n <ns> --wait`
  user: "backup failing" → delegate to debug-cluster for mover pod logs

  Use when the user mentions backups, restores, snapshots, SnapshotPolicy, SnapshotSchedule,
  Restore, ClusterRepository, PVC recovery, or disaster recovery. This cluster uses kopiur
  (home-operations, kopia-native) — not VolSync, not restic. Migrated 2026-07-12.
compatibility: Requires `kubectl` access to the cluster and the `kopiur` kubectl plugin; kopiur CRDs and the shared ClusterRepository secret must exist.
---

# Backup and restore (kopiur + Kopia)

## Quick reference

| Operation | Command |
|-----------|---------|
| List policies | `kubectl get snapshotpolicy -A` |
| List schedules | `kubectl get snapshotschedule -A` |
| List snapshots for an app | `kubectl kopiur snapshots list --policy <app> -n <ns>` |
| Trigger manual snapshot | `kubectl kopiur snapshot now --policy <app> -n <ns> --wait` |
| Check a restore | `kubectl get restore <app>-restore -n <ns>` |
| Status | `kubectl describe snapshotpolicy <app> -n <ns>` |

## Check status

```bash
kubectl get snapshotpolicy -A
kubectl describe snapshotpolicy <app> -n <namespace>
kubectl kopiur snapshots list --policy <app> -n <namespace>
```

A healthy policy shows `Ready: True` and a recent `status.lastSuccessfulSnapshot`. The
`SnapshotSchedule` object's `status.observedGeneration` sticking behind
`.metadata.generation` is a known cosmetic quirk (fleet-wide, harmless) — it makes the owning
Flux Kustomization's `wait: true` health check flap (`HealthCheckFailed ... status: 'InProgress'`,
self-clears on retry). Don't chase it; trust the policy's own `Ready` condition and a fresh
manual snapshot instead.

## Trigger manual snapshot

```bash
kubectl kopiur snapshot now --policy <app> -n <namespace> --wait
```

## Restore from backup

**Standard restore-to-latest** (the app's `Restore` CR already exists from setup — restoring
just means giving it a fresh empty PVC to populate):

1. `flux suspend kustomization <app> -n <ns>` **and** `flux suspend helmrelease <app> -n <ns>` —
   suspending only the Kustomization doesn't stop helm-controller's own reconcile, which can
   revert manual scaling/KEDA-pause changes.
2. If the app uses `components/nfs-scaler`: pause KEDA —
   `kubectl patch scaledobject <app> -n <ns> --type=merge -p
   '{"metadata":{"annotations":{"autoscaling.keda.sh/paused":"true"}}}'`.
3. `kubectl scale deployment <app> -n <ns> --replicas=0` (scale **every** deployment sharing
   the PVC — some apps like karakeep have 2-3); wait for pod deletion
   (`kubectl wait --for=delete pod -l app.kubernetes.io/name=<app> -n <ns>`) — a lingering pod
   holds `kubernetes.io/pvc-protection` and blocks the delete indefinitely.
4. `kubectl delete pvc <app> -n <ns>`; wait for it to be genuinely gone (`kubectl get pvc` →
   `NotFound`, not `Terminating` — Ceph RBD detach can take tens of seconds to minutes).
5. Resume: `flux resume helmrelease <app> -n <ns>` then `flux resume kustomization <app> -n <ns>`.
6. Watch the `Restore` CR: `kubectl get restore <app>-restore -n <ns> -o jsonpath='{.status.phase}'`
   → `Completed`, and `Ready: True` (not `Ready: False` — see the #233 warning below).
7. Scale back up (`kubectl scale deployment <app> -n <ns> --replicas=1` — apps without
   `nfs-scaler` need this manual step; scaler apps recover on their own). Verify: pod healthy,
   `kubectl exec ... id` uid matches file ownership, fresh
   `kubectl kopiur snapshot now --policy <app> -n <ns> --wait` succeeds.

**Confirmed live** (2026-07-12, tested on `dumbassets` and used to recover `karakeep`): the
*same* already-`Completed` `Restore` object correctly re-claims and re-populates a brand new PVC
— you do **not** need to delete/recreate the `Restore` CR itself to restore again. Step 4 alone
is sufficient for a plain restore-to-latest.

**Point-in-time / specific-snapshot restore**: edit the `Restore` CR's `spec.source.fromPolicy`
before deleting the PVC — `offset: N` picks the Nth-previous snapshot (0 = latest), or
`asOf: "<RFC3339 timestamp>"` picks the newest snapshot at or before that time. `spec.source`
also supports `snapshotRef` (pin an exact `Snapshot` CR by name) and `identity` (raw kopia
identity match, for foreign/aged-out catalog entries) — see
`kubectl get crd restores.kopiur.home-operations.com -o jsonpath='{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.source}'`
for the full field shapes. These fields live in the app's own
`app/restore.yaml` (or the rendered output of `components/kopiur/restore` if it doesn't have
one) — this is a real git change, not a live patch, unless testing.

## ⚠️ Critical gotcha: kopiur#233 (open, unfixed)

**Never let an app's `Restore` CR get deleted-and-recreated while its target PVC is `Bound`** —
a Flux prune/recreate cycle, a `ClusterRepository` reset, or a forced Kustomization re-apply all
qualify. This silently restores into an orphaned `prime-<uid>` staging PVC that never gets
garbage-collected. **The symptom is invisible to normal health checks**: `status.phase` still
reports `Completed`, but `status.conditions` stay `Ready: False` — Flux's own health check and
kstatus won't flag it. Confirmed live 2026-07-12: `karakeep`'s double race-hit during its cutover
left exactly this signature (`Completed`/`Ready: False`) plus an orphaned 5Gi `prime-*` PVC that
sat unnoticed for 104 minutes.

**Sweep for it**:

```bash
# Any Restore with Completed phase but Ready: False — the silent signature
kubectl get restore -A -o json | jq -r '.items[] | select(.status.phase == "Completed" and (.status.conditions[] | select(.type == "Ready" and .status == "False"))) | "\(.metadata.namespace)/\(.metadata.name)"'

# Any orphaned staging PVC (full-size Ceph storage, invisible otherwise)
kubectl get pvc -A -l kopiur.home-operations.com/op=restore-populate

# Any app PVC stuck Terminating despite pods still running (a related failure mode:
# deleting a PVC without fully scaling down first, e.g. mid-recovery-procedure)
kubectl get pvc -A | grep -i terminating
```

If a `Restore` CR is affected: it's usually still safely re-triggerable via the plain
"delete the PVC" recipe above (the CR itself wasn't recreated, just its staging PVC leaked) —
scale down properly this time, let any stuck-Terminating PVC finish, resume, and verify
`Ready: True` this time. Delete any orphaned `prime-*` PVC directly (`kubectl delete pvc
<name> -n <ns>` — check `ownerReferences` first to confirm which `Restore` it belongs to and
that the Restore's current live state (`status.pvcPrime`) doesn't still reference it).

## Identity / uid gotchas — verify live, don't trust the manifest

Several apps' `securityContext.runAsUser` in `helmrelease.yaml` does **not** match the uid that
actually owns their files, because the image's own entrypoint drops privilege internally
(gosu/s6-overlay/su-exec) after starting as root — invisible to Kubernetes. Found live on
`fileflows`, `odysseus`, and `nextcloud` this migration; `odysseus` specifically crashlooped
(`sqlite3.OperationalError: unable to open database file`) after being restored as root when its
real runtime uid was 1000. **Before assuming an app's mover identity, verify live**:

```bash
kubectl exec -n <ns> deployment/<app> -- id
kubectl exec -n <ns> deployment/<app> -- ls -la <actual-mount-path>   # not the first path you guess — check volumeMounts/volumes for the real PVC claimName first, some apps mount more than one PVC
```

If they don't match, `KOPIUR_PUID`/`KOPIUR_PGID` (and, only for genuine root,
`KOPIUR_MOVER_CAPS_ADD: "[DAC_READ_SEARCH]"`) go in the app's own `ks.yaml`
`postBuild.substitute` block — see [references/restore-pvc.md](references/restore-pvc.md).

## Enable backups for a new app

Reference patterns in `kubernetes/components/kopiur/`. See
[references/restore-pvc.md](references/restore-pvc.md) for the full substitute-variable list.

## Delegation

| Scenario | Action |
|----------|--------|
| Single status check | Inline |
| Multiple apps | Parallel subagents per app |
| Restore | Sequential: suspend → scale → delete PVC → resume → verify |
| Unknown failure | debug-cluster subagent |

## Troubleshooting (inline)

- Repository secret: `kubectl get secret -n kopiur-system kopia-secret` (shared across all apps
  via credential projection — apps don't carry their own copy)
- `ClusterRepository` health: `kubectl get clusterrepository kopia-nas`
- `dataSourceRef` on the PVC should be `{apiGroup: kopiur.home-operations.com, kind: Restore,
  name: <app>-restore}` — `kind: ReplicationDestination` means a stale volsync-era resource
  won a race (shouldn't happen post-decommission, but check if `dataSourceRef` looks wrong)
- Repo-wide permission drift: kopia silently retries `PermissionDenied` forever instead of
  erroring — a stuck mover with no log output and near-zero CPU is this, not a slow backend;
  check `find /repo -type d -not -perm 0775` / `-type f -not -perm 0664` from a mover shell

For mover logs and deep failures → [debug-cluster](../debug-cluster/SKILL.md).
