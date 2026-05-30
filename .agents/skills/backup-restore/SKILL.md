---
name: backup-restore
description: >-
  Manage VolSync Kopia-based backups and restores for Kubernetes PVs in this cluster.

  user: "check backup status for app X" → list ReplicationSources, describe conditions
  user: "restore from backup" → find snapshot, create restore PVC with VolumePopulator
  user: "trigger manual backup" → annotate volsync.backube/sync=true
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
| Trigger sync | `kubectl annotate rs/<name> -n <ns> volsync.backube/sync=true --overwrite` |
| Status | `kubectl describe replicationsource <name> -n <ns>` |

## Check status

```bash
kubectl get replicationsources -A
kubectl describe replicationsource <app> -n <namespace>
kubectl get rs <app> -n <namespace> -o yaml
```

## Trigger manual sync

```bash
kubectl annotate replicationsource/<name> -n <namespace> volsync.backube/sync=true --overwrite
```

## Restore from backup

1. **Find snapshot** — `kubectl get replicationdestination <app> -n <namespace> -o yaml` → `status.latestImage`
2. **Create restore PVC** — `dataSourceRef` → `ReplicationDestination` (see [references/restore-pvc.md](references/restore-pvc.md))

## New backup

Reference patterns in `kubernetes/components/volsync/`. Use Kopia mover settings consistent with existing apps.

Privileged movers when required:

```yaml
metadata:
  annotations:
    volsync.backube/privileged-movers: "true"
```

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

## Progressive disclosure

- Restore PVC spec: [references/restore-pvc.md](references/restore-pvc.md)

Format reference: [agentskills.io](https://agentskills.io/specification).
