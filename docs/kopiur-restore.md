# Kopiur Restore Guide

This guide explains how to properly restore data from kopiur (Kopia-native) backups.

For the switch to `kopia-s3` and reaching older NFS history, see the
[S3 guide](kopiur-s3.md).

## Understanding the Components

The `components/kopiur` component creates, per app:

- `SnapshotPolicy`: the backup identity + retention/schedule config
- `SnapshotSchedule`: the cron trigger (hashed minute + jitter, not a fleet-wide fixed time)
- `PersistentVolumeClaim`: created via `dataSourceRef` pointing at the `Restore` below
- `Restore`: a **passive populator** — it does nothing until a PVC's `dataSourceRef` claims it,
  then restores the selected snapshot into that PVC

**Important**: the PVC spec is **immutable** after creation. `kind` in `dataSourceRef` must be
`Restore` (`apiGroup: kopiur.home-operations.com`).

## Identity: verify live, don't trust the manifest

Several apps' `securityContext.runAsUser` in their `helmrelease.yaml` does **not** match the uid
that actually owns their files — the image's own entrypoint (gosu/s6-overlay/su-exec) drops
privilege internally after starting as root, invisible to Kubernetes. Confirmed on `fileflows` and
`nextcloud`. **Before touching an app's `mover.securityContext`, verify**:

```bash
kubectl exec -n <ns> deployment/<app> -- id
kubectl exec -n <ns> deployment/<app> -- ls -la <actual-mount-path>
```

Check `volumeMounts`/`volumes` first for the real PVC `claimName` and mount path — some apps
mount more than one PVC (e.g. nextcloud has a second, unrelated 500Gi NFS volume at the
"obvious" `/var/www/data` path; the migrated one is elsewhere).

## Recover Hermes when its RBD volume is still attached to an unavailable node

Hermes normally resumes on its existing `ai/hermes` claim. On 25 November 2026 the TrueNAS
host rebooted at 05:15 UTC. Its `control-1` guest lost Kubernetes readiness at 05:16. The
scheduler placed the replacement pod on `control-3` at 05:21. The old pod still held the
ReadWriteOnce volume on `control-1`, and the replacement stayed in `ContainerCreating`. The
attach/detach controller reported the volume in use on the old node. Attachment on the new node
started when the old node recovered at 09:21, and Hermes became available at 09:23.

Identify the old attachment, replacement node and backup age before choosing a recovery action:

```bash
kubectl -n ai get pods -l app.kubernetes.io/name=hermes -o wide
kubectl -n ai get events --sort-by=.lastTimestamp --field-selector=reason=FailedAttachVolume
pv=$(kubectl -n ai get pvc hermes -o jsonpath='{.spec.volumeName}')
kubectl get volumeattachments.storage.k8s.io -o json | jq --arg pv "$pv" \
  '.items[] | select(.spec.source.persistentVolumeName == $pv) | {node: .spec.nodeName, attached: .status.attached}'
kubectl get nodes
kubectl -n rook-ceph get cephcluster
kubectl -n ai get snapshotpolicy hermes -o jsonpath='{.status.lastSuccessfulSnapshot}{"\n"}'
```

- **Planned maintenance.** While the original kubelet can still unmount the filesystem, cordon
  and drain its node through the approved maintenance workflow. Review disruption budgets and
  any `emptyDir` data before permitting its deletion. Wait for detachment, the replacement's
  Ready condition and healthy Ceph quorum before shutting down the original node or its host.
  The TrueNAS host supplies a Ceph OSD through `control-1` and the Kopia S3 repository.
  Account for both when planning host maintenance. [Storage topology](drives.md)
- **Node unreachable or restarting.** Recover the kubelet, guest or connectivity first. A
  missing lease, failed ping, or paused VM can coexist with a live Ceph client. Keep the claim
  and VolumeAttachment intact. Force-deleting the pod, attachment or RBD lock can expose
  SQLite to two writers.
- **Node independently confirmed powered off and kept off.** For `control-1`, verify the
  `TALOS` VM is stopped in TrueNAS and cannot auto-start during handover. Identify every pod
  and volume on that node and verify the remaining control plane and Ceph can serve I/O.
  With operator approval, use the Kubernetes non-graceful shutdown procedure: cordon the node
  and apply `node.kubernetes.io/out-of-service=nodeshutdown:NoExecute`. This affects all
  non-tolerating pods there and permits their volumes to detach. After a full power cycle and
  verified node/storage recovery, remove the taint and uncordon under the same maintenance
  workflow. [Kubernetes node shutdown](https://kubernetes.io/docs/concepts/cluster-administration/node-shutdown/#non-graceful-node-shutdown-handling)

Verify one ready Hermes replica, the original PV bound to `ai/hermes`, one attached node
matching the running pod, and two successive ten-minute exporter heartbeats in VictoriaMetrics.
Use the separate-claim drill below if filesystem integrity is in doubt.

The installed RBD driver has `enableFencing` and `deployCsiAddons` disabled. Enabling native
Ceph fencing changes the shared driver for every RBD workload. It requires the separate
CSI-Addons controller for automatic unfencing, a trustworthy decision that the old node is
down, and a tested full power-cycle and unfence procedure. [Pinned Rook 1.21.3 fencing configuration](https://github.com/rook/rook/blob/v1.21.3/Documentation/Helm-Charts/csi-drivers-chart.md#network-fencing) · [RBD node loss and recovery](https://github.com/rook/rook/blob/v1.21.3/Documentation/Storage-Configuration/Block-Storage-RBD/block-storage.md#node-loss)

## Verify a Hermes snapshot on a separate claim

For a recovery drill, keep the production claim mounted and the gateway running.

1. Record the production PVC, PV and pod identities. Select a recent `Succeeded` Snapshot
   with a recorded Kopia snapshot id. Pin its Kubernetes name in a new Restore's
   `spec.source.snapshotRef.name`. Set `target.populator: {}` and `policy.onMissingSnapshot: Fail`
   so an empty volume cannot pass the drill. Use the mover uid and gid from the Hermes policy.
2. Create a separate claim with the production class, access mode and capacity. Its
   `dataSourceRef` must name the **drill** Restore. Wait for the claim to bind and for the
   Restore to report both `Completed` and `Ready=True`.
3. Mount the claim read-only in an isolated helper with the same uid, an empty scratch volume,
   no service-account token and no network access. Override the image entrypoint so restored
   cron jobs and messaging adapters never start. Check ownership and private-key permissions
   without displaying file contents.
4. Copy each SQLite database and its WAL/SHM companions together to scratch and run
   `PRAGMA integrity_check` on the copy. Parse the profile settings and schedules, resolve
   each job's script beneath its owning profile, and validate syntax without executing it.
   Check the [backup exclusions](../kubernetes/apps/ai/hermes/ks.yaml) separately:
   a restored lockfile proves dependency metadata survived; rebuilding the excluded runtime
   or packages on clean scratch verifies that they can actually be recovered. In a separate
   controlled environment with registry access, download the runtime artifacts and package
   files required by the restored version and lockfiles. Mount them read-only in the helper
   and rebuild on scratch using the package manager's offline mode.
5. Remove the drill helper, claim, Restore and network policy. Check that the drill PV and
   populator staging resources disappear and that the recorded production identities still match.

## Proper Restore Workflow

### Step 1: Suspend Kustomization AND HelmRelease

Prevent Flux from interfering during the restore. **Both** need to be suspended:

```bash
flux suspend kustomization jellyfin -n media
flux suspend helmrelease jellyfin -n media
```

**Important**: helm-controller's own reconcile of the HelmRelease will keep reverting manual
scaling/KEDA-pause changes if only the Kustomization is suspended.

### Step 2: Pause KEDA (if using nfs-scaler)

```bash
kubectl patch scaledobject jellyfin -n media --type=merge -p '{"metadata":{"annotations":{"autoscaling.keda.sh/paused":"true"}}}'
```

Apps without `components/nfs-scaler` don't need this, but also won't get their replica count
re-asserted automatically on resume (see Step 8).

### Step 3: Scale Down the Application

Scale **every** deployment sharing the PVC — some apps (karakeep: main + chrome + meilisearch)
have more than one:

```bash
kubectl scale deployment jellyfin -n media --replicas=0
kubectl wait --for=delete pod -l app.kubernetes.io/name=jellyfin -n media --timeout=90s
```

A lingering pod holds `kubernetes.io/pvc-protection` and blocks the PVC delete indefinitely.
Some CronJobs (e.g. nextcloud's `*/5 * * * *` maintenance job) leave *completed* pods that
**also** count toward this finalizer — check `kubectl describe pvc` for `Used By` and delete
stray completed pods if the finalizer won't clear.

### Step 4: Delete the Existing PVC

```bash
kubectl delete pvc jellyfin -n media
# poll until genuinely gone, not just Terminating — Ceph RBD detach can take minutes:
until ! kubectl get pvc jellyfin -n media >/dev/null 2>&1; do sleep 5; done
```

**Confirmed live** (2026-07-12, on `dumbassets` and used to recover `karakeep`): the app's
*existing* `Restore` CR — even if already `Completed` from a previous restore — correctly
re-claims and re-populates a fresh PVC on its own. **You do not need to delete or recreate the
`Restore` CR itself** for a plain restore-to-latest. Deleting just the PVC is sufficient.

### Step 5 (optional): Restore to a Specific Point in Time

Skip this step for a plain restore-to-latest. To roll back to an older snapshot, edit the app's
`Restore` CR's `spec.source.fromPolicy` *before* deleting the PVC (Step 4):

```yaml
spec:
  source:
    fromPolicy:
      name: jellyfin
      offset: 1        # 0 = latest (default), 1 = previous, 2 = the one before that, ...
      # asOf: "2026-07-10T04:00:00Z"   # OR: newest snapshot at/before this RFC3339 timestamp
```

`spec.source` also supports `snapshotRef` (pin an exact `Snapshot` CR by name) and `identity`
(raw kopia identity match, for foreign/aged-out catalog entries not tracked by a `Snapshot` CR).
Full field shapes:

```bash
kubectl get crd restores.kopiur.home-operations.com -o jsonpath='{.spec.versions[0].schema.openAPIV3Schema.properties.spec.properties.source}'
```

List available snapshots first:

```bash
kubectl kopiur snapshots list --policy jellyfin -n media
```

No per-app `restore.yaml` exists: the Restore comes from the shared
`kubernetes/components/kopiur/restore/restore.yaml`, so for a one-off test use a live `kubectl edit`.

### Step 6: Resume Kustomization and HelmRelease

```bash
flux resume helmrelease jellyfin -n media
flux resume kustomization jellyfin -n media
```

This recreates the PVC, which the existing `Restore` CR claims and populates automatically.

### Step 7: Wait for Restore to Complete

```bash
until kubectl get restore jellyfin-restore -n media -o jsonpath='{.status.phase}' | grep -qE 'Completed|Failed'; do
  sleep 5
done
kubectl get restore jellyfin-restore -n media -o jsonpath='{.status.phase}{"\n"}'
kubectl get restore jellyfin-restore -n media -o jsonpath='{range .status.conditions[*]}{.type}={.status} {.reason}{"\n"}{end}'
```

**Critical**: `status.phase: Completed` alone is not sufficient; check `Ready: True` too (see
[`Completed` but `Ready: False`](#completed-but-ready-false)).

### Step 8: Scale Up the Application

```bash
kubectl scale deployment jellyfin -n media --replicas=1
kubectl wait --for=condition=ready pod -l app.kubernetes.io/name=jellyfin -n media --timeout=300s
```

**Apps without `components/nfs-scaler` need this manual step** — their `helmrelease.yaml`
never declares `replicas` explicitly, so helm-controller only re-asserts the chart's `replicas:
1` default when it has an actual values/chart diff to apply; a bare Kustomization
`components:`-only change (like a plain restore) leaves the manual `--replicas=0` from Step 3 in
place. Scaler apps recover on their own via KEDA.

### Step 9: Verify Data

```bash
kubectl exec -n media deployment/jellyfin -- id
kubectl exec -n media deployment/jellyfin -- ls -la <mount-path>   # uid should match the `id` output above
kubectl kopiur snapshot now --policy jellyfin -n media --wait      # fresh snapshot should succeed
kubectl kopiur snapshots list --policy jellyfin -n media           # file count/size should be continuous with history
```

## `Completed` but `Ready: False`

kopiur#233 (a `Restore` recreated over a `Bound` PVC restored into an orphaned `prime-*` staging
PVC that was never cleaned up) was fixed in kopiur 0.7.3. The signature is still worth a sweep
after any suspicious Flux or repository event, because Flux's health check does not flag it:

```bash
# Restore CRs showing the silent Completed/Ready:False signature
kubectl get restore -A -o json | jq -r '.items[] | select(.status.phase == "Completed" and (.status.conditions[] | select(.type == "Ready" and .status == "False"))) | "\(.metadata.namespace)/\(.metadata.name)"'

# Orphaned staging PVCs (full-size Ceph storage, invisible otherwise)
kubectl get pvc -A -l kopiur.home-operations.com/op=restore-populate
```

## Troubleshooting

### Issue: PVC created but Restore never claims it

**Cause**: `dataSourceRef` doesn't match the `Restore` CR's name, or the `Restore` CR doesn't
exist yet (Flux hasn't applied it — check `kubectl get restore <app>-restore -n <ns>`).

**Solution**: confirm `kubectl get pvc <app> -n <ns> -o jsonpath='{.spec.dataSourceRef}'` shows
`{apiGroup: kopiur.home-operations.com, kind: Restore, name: <app>-restore}` exactly.

### Issue: Restore takes too long

Large restores (e.g. nextcloud's 50Gi, jellyfin's 28.8GiB) can take 10-30 minutes. Watch
`status.phase` transition `Restoring` → `Completed`; `status.conditions[type=Reconciling]`
tells you if it's actively working or stuck.

### Issue: Mover stuck, no log output, near-zero CPU (NFS repository only)

**Cause**: repo-wide permission drift — kopia silently retries `PermissionDenied` forever
instead of erroring. Check `find /repo -type d -not -perm 0775` / `-type f -not -perm 0664`
from a mover shell before assuming a slow backend.
