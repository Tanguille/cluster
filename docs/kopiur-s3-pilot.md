# Kopiur TrueNAS S3 pilot

The suspended [pilot Kustomization](../kubernetes/apps/default/kopiur-s3-pilot/ks.yaml)
adds a namespaced `Repository` and a manual-only `dumbassets-s3-pilot` policy in
`default`. It snapshots the existing dumbassets PVC into a separate S3 bucket.
The existing `kopia-nas` repository, NFS mounts, backup schedules and Kopia browser
continue unchanged. No `SnapshotSchedule` is created for the pilot.

On 2026-11-05, TrueNAS reported `27.0.0-RC.1`, no S3 listeners and no buckets.
Its native S3 service is [experimental](https://www.truenas.com/docs/scale/27/shares/s3/).
The endpoint below is a provisioning target, not an already available service.

## Provisioning and activation

Before editing, verify the branch/worktree with `git branch --show-current` and
`git worktree list`, then run `git status` and `git pull --ff-only && git status`.
Provisioning TrueNAS, editing secrets and activating the pilot require owner approval.

- Configure a TrueNAS HTTPS listener on port 9000 and region `us-east-1`.
  Point LAN DNS `s3.${SECRET_DOMAIN}` directly at TrueNAS and install a certificate
  valid for that hostname. If a different listener is chosen, update `backend.s3`
  in [repository.yaml](../kubernetes/apps/default/kopiur-s3-pilot/app/repository.yaml).
  Keep certificate verification enabled; for a private CA, use Kopiur's
  `backend.s3.tls.caBundleRef` with a ConfigMap in `default`.
- Create the empty bucket `kopiur-s3-pilot` on a dedicated dataset, preferably on the
  same pool as the NFS repository for comparison. Do not expose or reuse
  `/mnt/TanguilleServer/VolsyncKopia`. Leave Object Lock, versioning and lifecycle
  expiration disabled for this baseline so they do not interfere with maintenance.
- Create dedicated credentials with list, read, write and delete permissions on
  this bucket only. Kopiur creates the repository, not the bucket. See the
  [S3 backend requirements](https://kopiur.home-operations.com/backends/s3/).
- Add `app/secret.sops.yaml` beside the repository manifest, containing a Secret
  named `kopiur-s3-pilot` in `default`, with encrypted `stringData` keys
  `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `KOPIA_PASSWORD`. Use a new
  repository password and retain it outside the cluster. Follow [SOPS](sops.md),
  then add `secret.sops.yaml` to the pilot's `app/kustomization.yaml` resources.
  No plaintext or dummy credential Secret is included in this PR.
- Change the pilot Flux Kustomization's `spec.suspend` to `false` in Git only after
  those prerequisites are complete. Merging this initial configuration alone
  does not create a repository or start backup jobs.

The repository grants its mover pods the existing `egress.home.arpa/lan: allow`
exception so pod-originated S3 requests reach TrueNAS. This is the namespace's
existing LAN exception, not a restriction to port 9000. The production workloads'
network policies remain unchanged. Maintenance uses Kopiur's managed defaults.

## Manual backup and isolated restore

Run from the repository with its kubeconfig selected. These commands create pilot
backup/restore jobs and a new PVC; execute only after activation is approved.

```bash
mise exec -- kubectl --kubeconfig ./kubeconfig -n default wait \
  --for=condition=Ready repository/kopiur-s3-pilot --timeout=5m
mise exec -- kubectl --kubeconfig ./kubeconfig kopiur snapshot now \
  -n default --policy dumbassets-s3-pilot --wait
mise exec -- kubectl --kubeconfig ./kubeconfig kopiur snapshots list \
  -n default --policy dumbassets-s3-pilot
```

After the first backup succeeds, repeat it to measure an incremental run. Record
elapsed time, bytes uploaded, mover CPU/memory and TrueNAS CPU/disk load. Compare
with the normal dumbassets NFS runs, distinguishing initial backup from incremental
backup and cold cache from warm cache. This 1Gi PVC is a compatibility smoke test;
it cannot establish large-repository or four-concurrent-job performance.

Restore only to a fresh pilot PVC. Do not use the in-place production recovery
procedure in [the restore guide](kopiur-restore.md) for this test.

```bash
mise exec -- kubectl --kubeconfig ./kubeconfig kopiur restore -n default \
  --from-policy dumbassets-s3-pilot --on-missing-snapshot fail \
  --create-pvc dumbassets-s3-pilot-restore --size 1Gi \
  --storage-class ceph-block --name dumbassets-s3-pilot-restore --wait
mise exec -- kubectl --kubeconfig ./kubeconfig -n default get \
  restore/dumbassets-s3-pilot-restore -o yaml
```

Require `Completed` and `Ready=True`, then mount the restored PVC in an isolated
inspection pod and verify file checksums and ownership against the captured source
snapshot. A successful Job alone does not prove the restored application data is
usable. Do not attach the restored PVC to the production application.

Before recommending migration, also test full maintenance, retention cleanup and
another restore after maintenance. For a throughput decision, compare representative
data on both backends at equal concurrency (the pilot cap matches NFS at four).
Record results here; none of these runtime tests have been performed yet.

```bash
mise exec -- kubectl --kubeconfig ./kubeconfig kopiur maintenance run -n default \
  --repository kopiur-s3-pilot --full --wait
```

## Stopping the pilot

Suspending the Flux Kustomization only stops Flux reconciliation: it does not pause
already-created Kopiur resources. Set the pilot Repository's `spec.suspend: true`
in Git and let Flux apply that first; stop manual triggers, wait for active pilot
jobs to finish and verify that maintenance is no longer scheduling jobs. Then
suspend its Flux Kustomization if retaining the experiment.
Remove pilot PVCs, snapshots, credentials and the bucket only with explicit approval
after preserving any results needed. Never remove the production NFS repository.
