# Restore PVC from ReplicationDestination

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

## ReplicationSource template

Reference `kubernetes/components/volsync/` for full patterns:

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
