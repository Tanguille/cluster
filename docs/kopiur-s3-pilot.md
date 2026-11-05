# Kopiur TrueNAS S3 pilot

The [pilot Kustomization](../kubernetes/apps/default/kopiur-s3-pilot/ks.yaml)
adds a namespaced `Repository` and a manual-only `dumbassets-s3-pilot` policy in
`default`. It snapshots the existing dumbassets PVC into a separate S3 bucket.
The existing `kopia-nas` repository, NFS mounts, backup schedules and Kopia browser
continue unchanged. No `SnapshotSchedule` is created for the pilot.

TrueNAS `27.0.0-RC.1` was provisioned on 2026-11-05. Its native S3 service is
[experimental](https://www.truenas.com/docs/scale/27/shares/s3/).

## Provisioned state

- HTTPS listener: `192.168.0.27:9000`, region `us-east-1`, enabled at boot.
- TLS: inherits the existing UI wildcard certificate, including future UI
  certificate replacements; no separate certificate or private key copy.
- Bucket: `kopiur-s3-pilot`, dataset `TanguilleServer/kopiur-s3-pilot`, on the same
  pool as the NFS repository. Versioning and Object Lock are off.
- Account: `kopiur-s3-pilot`, uid 3002, dedicated primary group, no password login,
  no shell login, no SMB access or administrative roles. It owns only the pilot
  bucket; no global S3 grants were added. Its key cannot create or delete buckets.
- Credentials and a new repository password are encrypted in the pilot's
  `app/secret.sops.yaml`. No existing credentials were changed.
- Verified against TrueNAS directly: trusted HTTPS, signed PUT/GET/ListObjectsV2,
  byte-for-byte payload round trip, anonymous GET denied, and DELETE. The temporary
  test object was removed. No Kopia repository has been initialized yet.

The [k8s-gateway configuration](../kubernetes/apps/network/k8s-gateway/app/helmrelease.yaml)
adds a dedicated `s3.${SECRET_DOMAIN}` zone resolving directly to `${TRUENAS_IP}`.
Pre-deployment connectivity checks used a
client-side DNS override while preserving hostname and certificate verification.

## Provisioning and activation

TrueNAS provisioning was authorized and completed. PR #6573 deployed DNS with
the Kubernetes pilot suspended. The follow-up activation sets `spec.suspend: false`;
once merged, Flux creates the repository and manual-only policy.

- After deploying the DNS change, verify `s3.${SECRET_DOMAIN}` resolves to
  `${TRUENAS_IP}` from the cluster and LAN, and verify HTTPS on port 9000 without
  a DNS override. The S3 listener itself is already running.
  An unsigned `curl https://s3.${SECRET_DOMAIN}:9000/` must complete TLS verification
  and return S3 `AccessDenied` (HTTP 403); do not use `-k` or `--resolve` for this check.
- Retain the pilot repository password outside the cluster using the human-held
  backup procedure in [SOPS](sops.md). The encrypted Secret contains
  `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `KOPIA_PASSWORD`.
- Keep activation in Git. The enabled Kustomization creates the repository and
  policy but does not schedule backups; run the manual tests below after it is Ready.

For reprovisioning, preserve the bucket/account separation above and use the
[S3 backend requirements](https://kopiur.home-operations.com/backends/s3/).
Kopiur creates the repository, not the bucket. Never reuse the production NFS
dataset `/mnt/TanguilleServer/VolsyncKopia` as this bucket.

The repository grants its mover pods the existing `egress.home.arpa/lan: allow`
exception so pod-originated S3 requests reach TrueNAS. This is the namespace's
existing LAN exception, not a restriction to port 9000. The production workloads'
network policies remain unchanged. Maintenance uses Kopiur's managed defaults.
Kopiur's [TLS configuration](https://github.com/home-operations/kopiur/blob/0.10.17/crates/api/src/common/mod.rs#L242)
defaults both `disableTls` and `insecureSkipVerify` to false, so an unexpected DNS
target cannot silently downgrade the client to HTTP or bypass certificate checks.
The repository uid/gid defaults deliberately cover this one app's backup and restore;
explicit per-recipe settings override them in
[`resolve_mover`](https://github.com/home-operations/kopiur/blob/0.10.17/crates/api/src/common/mover.rs#L350).

### Post-merge connectivity validation (2026-11-05)

PR #6573 merged as `8fbb630f1`; k8s-gateway reported a successful Helm upgrade.
Both the LAN workstation and the existing `default/homepage` pod resolved
`s3.tanguille.site` to `192.168.0.27` and received HTTP 403 over verified HTTPS
without a DNS override. Homepage uses the same LAN exception label as pilot movers.
The dumbassets app pod resolved DNS but its HTTPS request timed out; it lacks that
exception and matches the live LAN-deny policy. No application labels were changed.

The production `ClusterRepository/kopia-nas` remained Ready and BackendReachable.
The pilot Kustomization was still suspended and its Repository did not exist, so
backup, restore and maintenance validation await the activation change.

## Manual backup and isolated restore

Run from the repository with its kubeconfig selected. These commands create pilot
backup/restore jobs and a new PVC; execute only after activation is approved.

```bash
mise exec -- kubectl -n default wait \
  --for=condition=Ready repository/kopiur-s3-pilot --timeout=5m
pilot_snapshot="dumbassets-s3-pilot-$(date +%s)"
mise exec -- kubectl kopiur snapshot now \
  -n default --policy dumbassets-s3-pilot --name "$pilot_snapshot" --wait
mise exec -- kubectl kopiur snapshots list \
  -n default --policy dumbassets-s3-pilot
```

After the first backup succeeds, repeat it with a new snapshot name to measure an incremental run. Record
elapsed time, bytes uploaded, mover CPU/memory and TrueNAS CPU/disk load. Compare
with the normal dumbassets NFS runs, distinguishing initial backup from incremental
backup and cold cache from warm cache. This 1Gi PVC is a compatibility smoke test;
it cannot establish large-repository or four-concurrent-job performance.

Restore only to a fresh pilot PVC. Do not use the in-place production recovery
procedure in [the restore guide](kopiur-restore.md) for this test.

```bash
set -euo pipefail
mise exec -- kubectl kopiur restore -n default \
  --from-snapshot "$pilot_snapshot" --on-missing-snapshot fail \
  --repository kopiur-s3-pilot --repository-kind repository \
  --create-pvc dumbassets-s3-pilot-restore --size 1Gi \
  --storage-class ceph-block --name dumbassets-s3-pilot-restore --wait
mise exec -- kubectl -n default get \
  restore/dumbassets-s3-pilot-restore -o json | jq -e \
  '.status.phase == "Completed" and
   any(.status.conditions[]; .type == "Ready" and .status == "True") and
   .status.resolved.repository.kind == "Repository" and
   .status.resolved.repository.name == "kopiur-s3-pilot"'
```

`--name` is supported by the installed CLI (0.7.1) and honored by the
[upstream restore builder](https://github.com/home-operations/kopiur/blob/0.10.17/crates/ops/src/actions/restore.rs#L63).
Never claim the production passive `dumbassets-restore` with a pilot PVC.

Mount `dumbassets-s3-pilot-restore` read-only at `/data` in an isolated inspection pod.
Set `pilot_inspection_pod` to that pod's name. List the exact snapshot, then choose
regular files covering the application's database/configuration and uploaded assets:

```bash
mise exec -- kubectl kopiur ls -n default "$pilot_snapshot"
```

Download paths are relative to the snapshot root: `config.json`, for example,
not `/data/config.json`. `sourcePathOverride: /data` records the backup identity;
it does not add a `data/` directory to the stored tree. For each selected file, run:

```bash
set -euo pipefail
read -r -p 'Snapshot-relative file path: ' pilot_file
pilot_check_dir="$(mktemp -d)"
trap 'rm -rf -- "$pilot_check_dir"' EXIT
mise exec -- kubectl kopiur download -n default \
  "$pilot_snapshot" "$pilot_file" "$pilot_check_dir/reference"
mise exec -- kubectl -n default exec \
  "$pilot_inspection_pod" -- cat "/data/$pilot_file" | cmp - "$pilot_check_dir/reference"
pilot_owner="$(mise exec -- kubectl -n default exec \
  "$pilot_inspection_pod" -- stat -c '%u:%g' "/data/$pilot_file")"
test "$pilot_owner" = '65532:65532'
rm -rf -- "$pilot_check_dir"
trap - EXIT
```

Record the selected paths and results. This is sampled content/ownership verification,
not proof of whole-tree completeness. Do not compare against the changing live PVC
or attach the restored PVC to the production application.

Before recommending migration, also test full maintenance, retention cleanup and
another restore after maintenance. For a throughput decision, compare representative
data on both backends at equal concurrency (the pilot cap matches NFS at four).
Record results here; none of these runtime tests have been performed yet.

```bash
mise exec -- kubectl kopiur maintenance run -n default \
  --repository kopiur-s3-pilot --full --wait
```

## Stopping the pilot

Suspending the Flux Kustomization only stops Flux reconciliation: it does not pause
already-created Kopiur resources. Set the pilot Repository's `spec.suspend: true`
in Git and let Flux apply that first; stop manual triggers, wait for active pilot
jobs to finish and verify that maintenance is no longer scheduling jobs. Then
suspend its Flux Kustomization if retaining the experiment.
Remove pilot PVCs, snapshots, credentials, the bucket and the k8s-gateway `s3` zone only with explicit approval
after preserving any results needed. Never remove the production NFS repository.
