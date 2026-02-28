---
name: backup-restore
description: |
  Manage VolSync Kopia-based backups and restores for Kubernetes PersistentVolumes.
  Use when: creating backups, restoring from backup, checking backup status, scheduling,
  or disaster recovery. This cluster uses Kopia mover (NOT restic).
---

# Backup & Restore (VolSync + Kopia)

This cluster uses VolSync with Kopia mover for backups.

## Quick Reference

| Operation | Command |
|-----------|---------|
| List backups | `kubectl get replicationsources -A` |
| List restores | `kubectl get replicationdestinations -A` |
| Trigger sync | Annotate: `kubectl annotate rs/<name> volsync.backube/sync=true --overwrite` |
| Check status | `kubectl get rs <name> -n <ns> -o yaml` |

## Check Backup Status

```bash
# All ReplicationSources
kubectl get replicationsources -A

# Specific app
kubectl get replicationsource <app> -n <namespace> -o yaml

# Check conditions
kubectl describe replicationsource <app> -n <namespace>
```

## Trigger Manual Sync

```bash
# Annotate to trigger immediate sync
kubectl annotate replicationsource/<name> -n <namespace> volsync.backube/sync=true --overwrite

# Or use kubectl patch
kubectl patch replicationsource <name> -n <namespace> -p '{"metadata":{"annotations":{"volsync.backube/sync":"true"}}}' --type=merge
```

## Restore from Backup

1. **Find the snapshot**:
```bash
kubectl get replicationdestination <app> -n <namespace> -o yaml
# Look at status.latestImage
```

2. **Create restore PVC** (using VolumePopulator):
```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <app>-restore
  namespace: <namespace>
spec:
  dataSourceRef:
    kind: ReplicationDestination
    name: <app>
    namespace: <namespace>
  accessModes: ["ReadWriteOnce"]
  storageClassName: <storage-class>
  resources:
    requests:
      storage: <size>
```

3. **Apply**:
```bash
kubectl apply -f restore-pvc.yaml
```

## Create New Backup

Reference existing patterns in `kubernetes/components/volsync/`:
- `replicationsource.yaml` - Backup definition
- `replicationdestination.yaml` - Restore definition

Key fields:
```yaml
apiVersion: volsync.backube/v1alpha1
kind: ReplicationSource
metadata:
  name: <app>
  namespace: <namespace>
spec:
  sourcePVC: <pvc-name>
  trigger:
    schedule: "0 2 * * *"  # Cron: daily at 2am
  kopia:
    repository: <app>-volsync-secret
    volumeSnapshotClassName: csi-ceph-blockpool
    storageClassName: ceph-block
    accessModes: ["ReadWriteOnce"]
```

## Privileged Movers

Some apps need privileged movers. Add annotation to PVC:
```yaml
metadata:
  annotations:
    volsync.backube/privileged-movers: "true"
```

## Troubleshooting

**Backup failing**:
- Check repository secret exists
- Check storage class available
- Check mover pod logs: `kubectl logs -n volsync-system -l app.kubernetes.io/name=volsync`

**Restore not working**:
- Verify snapshot exists in ReplicationDestination
- Check PVC dataSourceRef matches ReplicationDestination name

## Related

- **debug-cluster**: For debugging backup issues
- **docs/useful_commands.md**: General kubectl reference
