# Restore PVC from ReplicationDestination

Side-car restore into a fresh PVC. The component's ReplicationDestination trigger is `manual: restore-once`, so `status.latestImage` is stale unless you re-trigger the RD first — for restore-in-place follow [docs/volsync-restore.md](../../../../docs/volsync-restore.md).

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <app>-restore
  namespace: <namespace>
spec:
  dataSourceRef:
    apiGroup: volsync.backube
    kind: ReplicationDestination
    name: <app>-dst
  accessModes: ["ReadWriteOnce"]
  storageClassName: <storage-class>
  resources:
    requests:
      storage: <size>
```

## Enable backups for an app

Apps never write ReplicationSource YAML — add the volsync component to the app's `ks.yaml` and set the postBuild vars:

```yaml
spec:
  components:
    - ../../../../components/volsync
  postBuild:
    substitute:
      APP: <app>
      VOLSYNC_CAPACITY: <size> # required
```

Set `VOLSYNC_STORAGECLASS`, `VOLSYNC_PUID`, `VOLSYNC_PGID`, `VOLSYNC_ACCESSMODES` only when deviating from the component defaults. Example: `kubernetes/apps/media/radarr/ks.yaml`.
