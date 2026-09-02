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

The procedure is [docs/kopiur-restore.md](../../../docs/kopiur-restore.md): suspend both
Kustomization and HelmRelease, pause KEDA, scale down every deployment sharing the PVC, delete
the PVC, resume, verify. That file is the single copy, and also covers point-in-time restore
(`offset`/`asOf`/`snapshotRef`/`identity`), the kopiur#233 orphaned-`prime-*`-PVC trap and its
sweep commands, and the live-uid check.

Not in that file: when the live uid does not match the manifest's `runAsUser`,
`KOPIUR_PUID`/`KOPIUR_PGID` (and, only for genuine root,
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

Symptom-keyed failures (PVC never claimed, restore stalling, wrong `dataSourceRef`, mover stuck
with no output) → [docs/kopiur-restore.md](../../../docs/kopiur-restore.md) Troubleshooting.
For mover logs and deep failures → [debug-cluster](../debug-cluster/SKILL.md).
