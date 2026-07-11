---
name: backup-restore
description: >-
  Manage VolSync Kopia-based backups and restores for Kubernetes PVs in this cluster.

  user: "check backup status for app X" → list ReplicationSources, describe conditions
  user: "restore from backup" → follow docs/volsync-restore.md
  user: "trigger manual backup" → patch spec.trigger.manual
  user: "backup failing" → delegate to debug-cluster for mover pod logs

  Use when the user mentions backups, restores, snapshots, ReplicationSource,
  ReplicationDestination, PVC recovery, or disaster recovery. This cluster uses Kopia (not restic).
compatibility: Requires `kubectl` access to the cluster; VolSync CRDs and Kopia repository secrets must exist for the app.
---

# Backup and restore (VolSync + Kopia)

## Quick reference

| Operation | Command |
|-----------|---------|
| List backups | `kubectl get replicationsources -A` |
| List restores | `kubectl get replicationdestinations -A` |
| Trigger sync | `kubectl patch replicationsource <app> -n <ns> --type merge -p '{"spec":{"trigger":{"manual":"sync-'$(date +%s)'"}}}'` |
| Status | `kubectl describe replicationsource <name> -n <ns>` |

## Check status

```bash
kubectl get replicationsources -A
kubectl describe replicationsource <app> -n <namespace>
kubectl get replicationsource <app> -n <namespace> -o yaml
```

## Trigger manual sync

```bash
kubectl patch replicationsource <app> -n <namespace> --type merge -p '{"spec":{"trigger":{"manual":"sync-'$(date +%s)'"}}}'
```

## Restore from backup

Follow the runbook [docs/volsync-restore.md](../../../docs/volsync-restore.md): suspend Flux ks+hr → delete app PVC → patch `restoreAsOf` + `trigger.manual` on `replicationdestination/<app>-dst` → wait `Synchronizing=False` reason `Successful` → resume Flux. The volsync component names every ReplicationDestination `<app>-dst`, so status checks are `kubectl get replicationdestination <app>-dst -n <namespace> -o yaml`.

For a standalone side-car restore PVC, see [references/restore-pvc.md](references/restore-pvc.md).

## New backup

Reference patterns in `kubernetes/components/volsync/`. Use Kopia mover settings consistent with existing apps.

Privileged movers are already enabled cluster-wide via the namespace annotation in `kubernetes/components/common/namespace.yaml` — nothing to add per app.

## Delegation

| Scenario | Action |
|----------|--------|
| Single status check | Inline |
| Multiple apps | Parallel subagents per app |
| Restore | Sequential: snapshot → PVC |
| Unknown failure | debug-cluster subagent |

## Troubleshooting (inline)

- Secret: `kubectl get secret <app>-volsync-secret -n <ns>`
- Snapshot present on ReplicationDestination
- `dataSourceRef` name matches ReplicationDestination

For mover logs and deep failures → [debug-cluster](../debug-cluster/SKILL.md).

VolSync→kopiur migration is in progress ([docs/kopiur-migration-plan.md](../../../docs/kopiur-migration-plan.md)); this skill stays authoritative until cutover.
