# Enable backups for an app

Apps never hand-author `SnapshotPolicy`/`SnapshotSchedule`/`Restore` YAML — add the shared
`components/kopiur` component to the app's `ks.yaml` and set the postBuild vars:

```yaml
spec:
  components:
    - ../../../../components/kopiur
  postBuild:
    substitute:
      APP: <app>
      PVC_CAPACITY: <size>   # required
      # Only set these when the app's REAL uid isn't 568 (this cluster's default
      # convention) — verify live with `kubectl exec deployment/<app> -- id` first,
      # never trust the helmrelease's own securityContext alone (entrypoint privilege
      # drops are invisible to it — see SKILL.md's identity gotcha section).
      # KOPIUR_PUID: "1000"
      # KOPIUR_PGID: "1000"
      # Root apps additionally need the capability (backup only, not restore) and the
      # namespace-level kopiur.home-operations.com/privileged-movers: "true" annotation
      # (already applied to ai/default/media via components/kopiur/privileged-movers —
      # check it's composed into the target namespace's kustomization.yaml).
      # KOPIUR_MOVER_CAPS_ADD: "[DAC_READ_SEARCH]"
```

`PVC_ACCESSMODES`/`PVC_STORAGECLASS`/`BACKUP_SNAPSHOTCLASS` are also available (defaults:
`ReadWriteOnce`/`ceph-block`/`csi-ceph-blockpool`) — only set them when deviating. Example
apps: `kubernetes/apps/media/qui` (plain 568 default), `kubernetes/apps/media/jellyfin`
(root, `KOPIUR_MOVER_CAPS_ADD`), `kubernetes/apps/ai/hermes` (one-off uid 10000).

This single component renders `SnapshotPolicy`, `SnapshotSchedule`, `PersistentVolumeClaim`
(with `dataSourceRef` pointing at the `Restore` below), and `Restore` — no separate
"backup-only" vs "backup+restore" split; every app that wants kopiur backups gets both.

## Side-car restore into a fresh PVC (inspect old data without touching the live app)

The `Restore` CR is a **passive populator** — it only acts once a PVC's `dataSourceRef` claims
it. To restore into a *second*, throwaway PVC without touching the app's real one, create both
a new `Restore` (pointing `spec.source.fromPolicy` at the same policy) and a new PVC referencing
it:

```yaml
apiVersion: kopiur.home-operations.com/v1alpha1
kind: Restore
metadata:
  name: <app>-inspect-restore
  namespace: <namespace>
spec:
  source:
    fromPolicy:
      name: <app>
      offset: 0   # or asOf: "<RFC3339>" for a specific point in time
  target:
    populator: {}
  policy:
    onMissingSnapshot: Continue
  credentialProjection:
    enabled: true
  mover:
    securityContext:
      runAsUser: <app's real uid>
      runAsGroup: <app's real uid>
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: <app>-inspect
  namespace: <namespace>
spec:
  dataSourceRef:
    apiGroup: kopiur.home-operations.com
    kind: Restore
    name: <app>-inspect-restore
  accessModes: ["ReadWriteOnce"]
  storageClassName: ceph-block
  resources:
    requests:
      storage: <size>
```

Delete both objects when done — this is a one-off, not something to leave in git.
