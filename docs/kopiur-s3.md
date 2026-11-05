# Kopiur TrueNAS S3 repository

[`kopia-s3`](../kubernetes/apps/kopiur-system/kopiur/repository/clusterrepository-s3.yaml)
is a second `ClusterRepository`, on TrueNAS native S3. `kopia-nas` stays the default.
An app moves when its `ks.yaml` sets `KOPIUR_REPOSITORY: kopia-s3`, which the shared
[backup component](../kubernetes/components/kopiur/backup/snapshotpolicy.yaml) puts on
its `SnapshotPolicy`. Identities stay `<app>@<namespace>:/data`.

TrueNAS `27.0.0-RC.1` was provisioned on 2026-11-05. Its native S3 service is
[experimental](https://www.truenas.com/docs/scale/27/shares/s3/). No backup, restore or
maintenance has run on it yet, and none of the procedures below have been exercised.

## Provisioned state

- HTTPS listener: `192.168.0.27:9000`, region `us-east-1`, enabled at boot.
- TLS: inherits the existing UI wildcard certificate, including future UI
  certificate replacements; no separate certificate or private key copy.
- Bucket `kopiur-s3-pilot`, dataset `TanguilleServer/kopiur-s3-pilot`, on the same pool
  as the NFS repository. Versioning and Object Lock are off.
- Account `kopiur-s3-pilot`, uid 3002, dedicated primary group, no password login,
  no shell login, no SMB access or administrative roles. It owns only this bucket; no
  global S3 grants were added. Its key cannot create or delete buckets.
- The bucket, dataset and account keep their original names: S3 buckets cannot be
  renamed, so changing them means provisioning replacements.
- `kopia-s3-credentials` ([SOPS](../kubernetes/apps/kopiur-system/kopiur/repository/s3-secret.sops.yaml))
  holds `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `KOPIA_PASSWORD`. Retain the
  password outside the cluster using the human-held procedure in [SOPS](sops.md).
- Verified against TrueNAS directly: trusted HTTPS, signed PUT/GET/ListObjectsV2,
  byte-for-byte payload round trip, anonymous GET denied, and DELETE.

**Offsite:** TrueNAS cloud sync `id 1` covers only `VolsyncKopia`. `kopia-s3` has no R2
copy until a second task exists. Add it before moving any app whose loss would matter:
the S3 key can delete every object in the bucket and is projected into app namespaces for
each mover run, so a leaked key only misses the R2 copy and ZFS snapshots.

For reprovisioning, keep the bucket and account separate and use the
[S3 backend requirements](https://kopiur.home-operations.com/backends/s3/). Kopiur
creates the repository, not the bucket.

## Network

The [k8s-gateway configuration](../kubernetes/apps/network/k8s-gateway/app/helmrelease.yaml)
serves a dedicated `s3.${SECRET_DOMAIN}` zone resolving directly to `${TRUENAS_IP}`.
An unsigned `curl https://s3.${SECRET_DOMAIN}:9000/` must complete TLS verification and
return S3 `AccessDenied` (HTTP 403); do not use `-k` or `--resolve` for this check.

S3 is pod traffic, unlike the kubelet's NFS mounts, so `moverDefaults.podLabels` grants
movers the `egress.home.arpa/lan: allow` exception that `default` and `media` require.
It is that LAN exception, not a restriction to port 9000. Browse sessions ignore
`podLabels`, so run `kubectl kopiur` browse, `ls` and `download` in `kopiur-system`.
Kopiur's [TLS configuration](https://github.com/home-operations/kopiur/blob/0.10.17/crates/api/src/common/mod.rs#L242)
defaults `disableTls` and `insecureSkipVerify` to false, so an unexpected DNS target
cannot downgrade the client to HTTP or bypass certificate checks.

Checked on 2026-11-05 from the LAN and `default/homepage`, which carries the same label:
both got HTTP 403 over verified HTTPS. The dumbassets app pod, without the label, resolved
DNS but timed out.

## Move an app

Existing Snapshot rows carry `deletionPolicy: Delete` and stay in the policy's retention
bucket after a repoint. As S3 rows displace them, retention prunes them and the finalizer
deletes from `kopia-nas`, so **a bare repoint erases the app's NFS history within about
7 days**. Step 1 prevents that: the rows still age out, but their kopia snapshots stay.

```bash
app=<app> ns=<namespace>
retain_nfs_rows() {
  mise exec -- kubectl -n "$ns" get snapshot -o json | jq -r --arg app "$app" \
    '.items[] | select(.spec.policyRef.name == $app
      and .status.resolved.repository.name == "kopia-nas") | .metadata.name' |
    xargs -r mise exec -- kubectl -n "$ns" patch snapshot \
      --type=merge -p '{"spec":{"deletionPolicy":"Retain"}}'
}
retain_nfs_rows                                          # 1. before the repoint
# 2. Set KOPIUR_REPOSITORY: kopia-s3 in the app's ks.yaml and merge.
mise exec -- kubectl kopiur snapshot now --policy "$app" -n "$ns" --wait   # 3.
retain_nfs_rows                                          # 4. rows that landed in between
```

Run step 3 straight away. `fromPolicy` resolves in the policy's current repository with
`onMissingSnapshot: Continue`, so a PVC recreated before the first S3 snapshot comes up
**empty**.

Then restore into a throwaway PVC with the
[side-car procedure](../.agents/skills/backup-restore/references/restore-pvc.md), using the
app's uid, and check content and ownership against the exact snapshot. Never claim the
app's own PVC or its `<app>-restore`.

After the last app, change the component default to `kopia-s3`, drop the per-app settings
and set `kopia-nas` to `mode: ReadOnly`.

## Rollback

Set `catalog.adoption: Ignore` on `kopia-nas` first: adoption can otherwise re-attach
discovered NFS rows to the policy and prune them under `Delete`. Then remove
`KOPIUR_REPOSITORY` from the app. Backups made on S3 stay in `kopia-s3`.

## NFS history

Keep `kopia-nas`, its dataset and the R2 bucket `volsync`. History stays reachable through
the Kopia browser, which still reads the NFS repository, and through a `Restore` naming
`spec.repository` and a raw `source.identity`, as in
[the restore guide](kopiur-restore.md). Never delete the repository or repurpose its dataset.

The TanguilleServer ZFS snapshot task (daily, 14 days, recursive) should hold freed blobs,
so read kopia statistics, not dataset `used`, when checking cleanup. It had not snapshotted
this dataset yet on 2026-11-06, so confirm it does before relying on it.
