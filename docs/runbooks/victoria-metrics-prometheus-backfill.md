# VictoriaMetrics Prometheus Historical Backfill Runbook

## Scope

Manual, approved operation only. This is not a Flux-managed automatic import.

## Rules

- Use `vmctl prometheus` against a Prometheus TSDB snapshot.
- Remote-read is a fallback only if snapshot-based import is not possible.
- Do not mount the live Prometheus RWO PVC into another pod while Prometheus is running.
- Prefer a Rook-Ceph CSI `VolumeSnapshot` and PVC clone.
- Use the Prometheus admin API only temporarily during the approved maintenance window.

## Command sequence

Set shared variables. Fill in `PROM_PVC` from the preflight output.

```bash
export K="/home/tanguille/.local/bin/mise exec -- env KUBECONFIG=/home/tanguille/cluster/kubeconfig"
export NS=observability
export PROM_PVC="<prometheus-data-pvc>"
export SNAPSHOT_CLASS=csi-ceph-blockpool
export STORAGE_CLASS=ceph-block
export CLONE_SIZE="<match-source-prometheus-pvc-size>"
export IMPORT_END=2026-05-12T21:20:00Z
export VM_ADDR=http://vmsingle-victoria-metrics.observability.svc.cluster.local:8428
```

Create the Prometheus TSDB snapshot before decommissioning the legacy Prometheus instance. After the migration removes Prometheus, this runbook can only import from an already-created TSDB snapshot clone:

```bash
$K kubectl -n "$NS" port-forward svc/prometheus-operated 9090:9090 >/tmp/prometheus-port-forward.log 2>&1 &
export PROM_PF_PID=$!
curl -sS -XPOST 'http://127.0.0.1:9090/api/v1/admin/tsdb/snapshot' | tee /tmp/prometheus-snapshot.json
export PROM_SNAPSHOT="$(jq -r '.data.name' /tmp/prometheus-snapshot.json)"
kill "$PROM_PF_PID"
printf 'Prometheus snapshot: %s\n' "$PROM_SNAPSHOT"
```

Create a CSI `VolumeSnapshot` and clone PVC for the import source:

```bash
cat <<EOF | $K kubectl apply --dry-run=server -f -
---
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: prometheus-tsdb-snapshot
  namespace: ${NS}
spec:
  volumeSnapshotClassName: ${SNAPSHOT_CLASS}
  source:
    persistentVolumeClaimName: ${PROM_PVC}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: prometheus-vm-backfill-clone
  namespace: ${NS}
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: ${STORAGE_CLASS}
  resources:
    requests:
      storage: ${CLONE_SIZE}
  dataSource:
    name: prometheus-tsdb-snapshot
    kind: VolumeSnapshot
    apiGroup: snapshot.storage.k8s.io
EOF

cat <<EOF | $K kubectl apply -f -
---
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: prometheus-tsdb-snapshot
  namespace: ${NS}
spec:
  volumeSnapshotClassName: ${SNAPSHOT_CLASS}
  source:
    persistentVolumeClaimName: ${PROM_PVC}
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: prometheus-vm-backfill-clone
  namespace: ${NS}
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: ${STORAGE_CLASS}
  resources:
    requests:
      storage: ${CLONE_SIZE}
  dataSource:
    name: prometheus-tsdb-snapshot
    kind: VolumeSnapshot
    apiGroup: snapshot.storage.k8s.io
EOF
```

Run a narrow `up` import first:

```bash
cat <<EOF | $K kubectl -n "$NS" apply -f -
---
apiVersion: batch/v1
kind: Job
metadata:
  name: vmctl-prometheus-backfill-up
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: vmctl
          image: victoriametrics/vmctl:v1.143.0
          args:
            - prometheus
            - -s
            - --disable-progress-bar
            - --prom-snapshot=/prometheus/prometheus-db/snapshots/${PROM_SNAPSHOT}
            - --prom-concurrency=2
            - --vm-concurrency=1
            - --prom-filter-time-end=${IMPORT_END}
            - --prom-filter-label=__name__
            - --prom-filter-label-value=up
            - --vm-addr=${VM_ADDR}
          volumeMounts:
            - name: prometheus-data
              mountPath: /prometheus
              readOnly: true
      volumes:
        - name: prometheus-data
          persistentVolumeClaim:
            claimName: prometheus-vm-backfill-clone
EOF

$K kubectl -n "$NS" logs -f job/vmctl-prometheus-backfill-up
```

After the narrow import validates, run the full import:

```bash
cat <<EOF | $K kubectl -n "$NS" apply -f -
---
apiVersion: batch/v1
kind: Job
metadata:
  name: vmctl-prometheus-backfill-full
spec:
  backoffLimit: 0
  template:
    spec:
      restartPolicy: Never
      containers:
        - name: vmctl
          image: victoriametrics/vmctl:v1.143.0
          args:
            - prometheus
            - -s
            - --disable-progress-bar
            - --prom-snapshot=/prometheus/prometheus-db/snapshots/${PROM_SNAPSHOT}
            - --prom-concurrency=4
            - --vm-concurrency=2
            - --prom-filter-time-end=${IMPORT_END}
            - --vm-addr=${VM_ADDR}
          volumeMounts:
            - name: prometheus-data
              mountPath: /prometheus
              readOnly: true
      volumes:
        - name: prometheus-data
          persistentVolumeClaim:
            claimName: prometheus-vm-backfill-clone
EOF

$K kubectl -n "$NS" logs -f job/vmctl-prometheus-backfill-full
```

Validate historical data, then clean up temporary resources and disable the Prometheus admin API again:

```bash
$K kubectl -n "$NS" port-forward svc/vmsingle-victoria-metrics 8428:8428 >/tmp/vmsingle-port-forward.log 2>&1 &
export VM_PF_PID=$!
curl -G 'http://127.0.0.1:8428/api/v1/query_range' \
  --data-urlencode 'query=up' \
  --data-urlencode 'start=2026-05-01T00:00:00Z' \
  --data-urlencode 'end=2026-05-01T01:00:00Z' \
  --data-urlencode 'step=1m'
kill "$VM_PF_PID"

$K kubectl -n "$NS" delete job vmctl-prometheus-backfill-up vmctl-prometheus-backfill-full --ignore-not-found
$K kubectl -n "$NS" delete pvc prometheus-vm-backfill-clone --ignore-not-found
$K kubectl -n "$NS" delete volumesnapshot prometheus-tsdb-snapshot --ignore-not-found
```

## Preflight

```bash
/home/tanguille/.local/bin/mise exec -- env KUBECONFIG=/home/tanguille/cluster/kubeconfig kubectl -n observability get pods,pvc | grep -E 'prometheus|vmsingle'
/home/tanguille/.local/bin/mise exec -- env KUBECONFIG=/home/tanguille/cluster/kubeconfig kubectl get volumesnapshotclass
/home/tanguille/.local/bin/mise exec -- env KUBECONFIG=/home/tanguille/cluster/kubeconfig kubectl -n observability get svc prometheus-operated vmsingle-victoria-metrics
```

Record the Prometheus PVC name, storage class, size, and any Ceph snapshot class discovered above. The clone PVC request must be at least the source PVC size; in this cluster the live Prometheus PVC was 300Gi.

## Snapshot creation

During the approved maintenance window, temporarily enable the Prometheus admin API before decommissioning the legacy Prometheus instance, reconcile, then create a TSDB snapshot:

```bash
/home/tanguille/.local/bin/mise exec -- env KUBECONFIG=/home/tanguille/cluster/kubeconfig kubectl -n observability port-forward svc/prometheus-operated 9090:9090
curl -XPOST 'http://127.0.0.1:9090/api/v1/admin/tsdb/snapshot'
```

Save the returned snapshot name.

## Example manifest templates

Use the values discovered in preflight. These are safe examples only.

```yaml
---
apiVersion: snapshot.storage.k8s.io/v1
kind: VolumeSnapshot
metadata:
  name: prometheus-tsdb-snapshot
  namespace: observability
spec:
  volumeSnapshotClassName: <rook-ceph-volume-snapshot-class>
  source:
    persistentVolumeClaimName: <prometheus-pvc-name>
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: prometheus-vm-backfill-clone
  namespace: observability
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: <rook-ceph-block-storage-class>
  resources:
    requests:
      storage: <at-least-prometheus-pvc-size>
  dataSource:
    name: prometheus-tsdb-snapshot
    kind: VolumeSnapshot
    apiGroup: snapshot.storage.k8s.io
```

Apply with `kubectl apply --dry-run=server` first.

## Narrow import

Run a one-off job with `victoriametrics/vmctl:v1.143.0`:

```bash
vmctl prometheus -s \
  --disable-progress-bar \
  --prom-snapshot=/prometheus/prometheus-db/snapshots/<snapshot-name> \
  --prom-concurrency=2 \
  --vm-concurrency=1 \
  --prom-filter-time-end=2026-05-12T21:20:00Z \
  --prom-filter-label=__name__ \
  --prom-filter-label-value=up \
  --vm-addr=http://vmsingle-victoria-metrics.observability.svc.cluster.local:8428
```

Use the cloned PVC or exported snapshot contents as the source mounted into the Job.

## Full import

After the narrow import validates, rerun without the label filter:

```bash
vmctl prometheus -s \
  --disable-progress-bar \
  --prom-snapshot=/prometheus/prometheus-db/snapshots/<snapshot-name> \
  --prom-concurrency=4 \
  --vm-concurrency=2 \
  --prom-filter-time-end=2026-05-12T21:20:00Z \
  --vm-addr=http://vmsingle-victoria-metrics.observability.svc.cluster.local:8428
```

If snapshot import is impossible, use remote-read only as a fallback and document why.

## Validation

Port-forward VMSingle locally and query historical data:

```bash
/home/tanguille/.local/bin/mise exec -- env KUBECONFIG=/home/tanguille/cluster/kubeconfig kubectl -n observability port-forward svc/vmsingle-victoria-metrics 8428:8428
curl -G 'http://127.0.0.1:8428/api/v1/query_range' \
  --data-urlencode 'query=up' \
  --data-urlencode 'start=2026-05-01T00:00:00Z' \
  --data-urlencode 'end=2026-05-01T01:00:00Z' \
  --data-urlencode 'step=1m'
```

Expected: `"status":"success"` and non-empty historical results for the retained pre-cutover window.

## Cleanup

- Disable the Prometheus admin API again, reconcile the change, and verify the live/rendered config no longer has `enableAdminAPI: true` (or that it is false/absent).
- Save vmctl Job logs, then delete temporary Job resources.
- Delete the clone PVC and `VolumeSnapshot` after validation succeeds.
- Remove any temporary import manifests once the data is confirmed.
