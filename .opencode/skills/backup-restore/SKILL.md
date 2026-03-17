---
name: backup-restore
description: |-
  Manage VolSync Kopia-based backups/restores for Kubernetes PVs.
  
  user: "check backup status for app X" → list ReplicationSources, describe conditions
  user: "restore from backup" → find snapshot, create restore PVC with VolumePopulator
  user: "trigger manual backup" → annotate with volsync.backube/sync=true
  user: "backup failing" → spawn debug-cluster subagent for mover pod logs
  
  Use proactively when: user mentions backups, restores, snapshots, ReplicationSource,
  ReplicationDestination, PVC recovery, or disaster recovery scenarios.
  
  This cluster uses Kopia mover (NOT restic).
---

# Backup & Restore (VolSync + Kopia)

## Quick Reference

| Operation | Command |
|-----------|---------|
| List backups | `kubectl get replicationsources -A` |
| List restores | `kubectl get replicationdestinations -A` |
| Trigger sync | `kubectl annotate rs/<name> volsync.backube/sync=true --overwrite` |
| Check status | `kubectl describe replicationsource <name> -n <ns>` |

---

## Check Status

```bash
# All apps
kubectl get replicationsources -A

# Specific app
kubectl describe replicationsource <app> -n <namespace>

# Check conditions
kubectl get rs <app> -n <namespace> -o yaml
```

## Trigger Manual Sync

```bash
kubectl annotate replicationsource/<name> -n <namespace> volsync.backube/sync=true --overwrite
```

## Restore from Backup

### Step 1: Find Snapshot

Locate the available snapshot in the ReplicationDestination status.

```bash
kubectl get replicationdestination <app> -n <namespace> -o yaml
# Look for status.latestImage
```

### Step 2: Create Restore PVC

Create a new PVC that references the ReplicationDestination as its data source.

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
  accessModes: ["ReadWriteOnce"]
  storageClassName: <storage-class>
  resources:
    requests:
      storage: <size>
```

---

## Create New Backup

Reference patterns in `kubernetes/components/volsync/`:

```yaml
apiVersion: volsync.backube/v1alpha1
kind: ReplicationSource
metadata:
  name: <app>
  namespace: <namespace>
spec:
  sourcePVC: <pvc-name>
  trigger:
    schedule: "0 2 * * *"
  kopia:
    repository: <app>-volsync-secret
    volumeSnapshotClassName: csi-ceph-blockpool
    storageClassName: ceph-block
    accessModes: ["ReadWriteOnce"]
```

## Privileged Movers

Add to PVC for apps requiring privileged movers:

```yaml
metadata:
  annotations:
    volsync.backube/privileged-movers: "true"
```

---

## When to Delegate

### Spawn debug-cluster subagent when:

- Backup failures requiring mover pod logs
- Multiple apps affected simultaneously
- Unknown root cause suspected

### Spawn backup-restore subagent when:

- PARALLEL: Status checks across multiple apps (spawn one per app)
- SEQUENTIAL: Restore flow depends on finding snapshot first

### Inline vs Delegate Decision:

| Scenario | Action |
|----------|--------|
| Single status check | Inline |
| Multiple app checks | PARALLEL subagents |
| Restore operation | SEQUENTIAL: find snapshot → restore |
| Debug unknown failure | Delegate to debug-cluster |

### Subagent Spawn Example:

```
background_task(
  agent="backup-restore",
  description="Check backup status for app1",
  prompt="Check ReplicationSource status for app1 in namespace..."
)
```

---

## Troubleshooting

Spawn **debug-cluster** subagent for:

- Mover pod logs: `kubectl logs -n volsync-system -l app.kubernetes.io/name=volsync`
- Repository secret validation
- Storage class availability issues
- Unexplained backup failures

### Quick checks (inline):

- Repository secret exists: `kubectl get secret <app>-volsync-secret -n <ns>`
- Snapshot exists in ReplicationDestination status
- PVC dataSourceRef matches ReplicationDestination name

## Related Skills

- **debug-cluster**: Complex backup debugging
- **docs/useful_commands.md**: General kubectl reference
