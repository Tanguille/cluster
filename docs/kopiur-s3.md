# Kopiur TrueNAS S3 repository

[`kopia-s3`](../kubernetes/apps/kopiur-system/kopiur/repository/clusterrepository.yaml) is
the `ClusterRepository` on TrueNAS native S3. Every app has backed up to it since the
2026-11-06 switch, through the shared
[backup component](../kubernetes/components/kopiur/backup/snapshotpolicy.yaml). Identities
stay `<app>@<namespace>:/data`.

TrueNAS `27.0.0-RC.1` serves it with `s3d` (`truenas_s3.service`, the "TrueNAS S3 gateway"),
TrueNAS's own Rust gateway. Objects are plain files named by kopia blob id in `s3data`,
hard-linked into `.truenas_s3`. The service is
[experimental](https://www.truenas.com/docs/scale/27/shares/s3/). Manage it under
**Shares** > **Object Storage (S3) Buckets** and **Credentials** > **S3 Access Keys**.

## Provisioned state

- HTTPS listener: `192.168.0.27:9000`, region `us-east-1`, enabled at boot.
- TLS: inherits the existing UI wildcard certificate, including future UI
  certificate replacements; no separate certificate or private key copy.
- Bucket `kopiur`, dataset `TanguilleServer/kopiur`, on the same pool as the NFS
  repository. Versioning and Object Lock are off.
- Account `kopiur-s3`, uid 3002, dedicated primary group, no password login,
  no shell login, no SMB access or administrative roles. It owns only this bucket; no
  global S3 grants were added. Its key cannot create or delete buckets.
- `kopia-s3-credentials` ([SOPS](../kubernetes/apps/kopiur-system/kopiur/repository/secret.sops.yaml))
  holds `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` and `KOPIA_PASSWORD`. Retain the
  password outside the cluster using the human-held procedure in [SOPS](sops.md).
- Verified against TrueNAS directly: trusted HTTPS, signed PUT/GET/ListObjectsV2,
  byte-for-byte payload round trip, anonymous GET denied, and DELETE.

**Offsite:** TrueNAS cloud sync task `2` pushes `s3data` to `kopiur/` in the R2 bucket
`volsync` nightly at 02:30. Task `1` still mirrors `VolsyncKopia` to the bucket root and
carries `exclude: /kopiur/**`; without it, its `SYNC` deletes that folder. The restore from
R2 is recorded in [Results](#results-2026-11-06).

Both tasks are `SYNC`, so deletions propagate. The S3 key can delete every object in the
bucket and reaches app namespaces for each mover run, so both also carry
`--max-delete 30000` (about 3x a normal night): a wiped source stops at the cap and the task
fails. Repeat runs would delete more, so treat that failure as urgent. Only ZFS snapshots
are out of the key's reach.

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

## Switch

Existing Snapshot rows carry `deletionPolicy: Delete` and stay in each policy's retention
bucket after a repoint. As S3 rows displace them, retention prunes them and the finalizer
deletes from `kopia-nas`, so **a bare repoint erases all NFS history within about 7 days**.
`retain_nfs_rows` prevents that: the rows still age out, but their kopia snapshots stay.

```bash
retain_nfs_rows() {
  local rows ns
  rows="$(mise exec -- kubectl get snapshot -A -o json)"
  for ns in $(jq -r '[.items[].metadata.namespace] | unique[]' <<<"$rows"); do
    jq -r --arg ns "$ns" '.items[] | select(.metadata.namespace == $ns
      and .status.resolved.repository.name == "kopia-nas") | .metadata.name' <<<"$rows" |
      xargs -r mise exec -- kubectl -n "$ns" patch snapshot \
        --type=merge -p '{"spec":{"deletionPolicy":"Retain"}}'
  done
}
retain_nfs_rows   # 1. before merging: Flux applies the switch within a minute
# 2. Merge the switch: the component moves to kopia-s3 and the kopia-nas files go.
policies="$(mise exec -- kubectl get snapshotpolicy -A -o json)"
jq -e 'all(.items[]; .spec.repository.name == "kopia-s3")' <<<"$policies" >/dev/null ||
  { echo 'not every policy moved yet' >&2; exit 1; }   # 3.
while read -r ns app; do
  mise exec -- kubectl kopiur snapshot now --policy "$app" -n "$ns" --wait &
done < <(jq -r '.items[] | "\(.metadata.namespace) \(.metadata.name)"' <<<"$policies")
wait              # 4. each first S3 snapshot, queued at the concurrency cap
retain_nfs_rows   # 5. rows that landed in between
```

Do not recreate any PVC until every app has its first S3 snapshot. `fromPolicy` resolves
in the policy's current repository with `onMissingSnapshot: Continue`, so a PVC recreated
before then comes up **empty**.

Then restore into throwaway PVCs with the
[side-car procedure](../.agents/skills/backup-restore/references/restore-pvc.md), using each
app's uid, and check content and ownership against the exact snapshot. Never claim an
app's own PVC or its `<app>-restore`.

`status.storageStats.snapshotCount` only updates on a catalog listing, so a repository that
starts empty keeps reporting 0 and trips `KopiurRepoEmpty` even with data in it. Request a
scan with the `kopiur.home-operations.com/catalog-scan-requested-at` annotation.

## Results (2026-11-06)

Incrementals, seconds in kopia. NFS is the hourly median (28 or 29 runs per app); S3 is one
run of all four at the cap. Wall clock is mostly CSI staging and pod start.

| App | NFS | S3 |
| --- | --- | --- |
| jellyfin | 7 | 8 |
| nextcloud | 3 | 2 |
| hermes | 9 | 9 |
| omniroute | 3 | 2 |

| Measure | First S3 run, all 25 apps | S3 incremental, four largest |
| --- | --- | --- |
| Duration | 265 s at the cap of 4 | 113 s |
| TrueNAS disk writes (`sda`+`sdb`, both mirror sides) | 29.6 GB | 185 MB |
| TrueNAS disk reads | 165 MB | 94 MB |
| Disk busy, average and peak | 49%, 85% | 3%, 6% |
| TrueNAS CPU | 45% | 24% |

- Repository size: 15.6G on disk for about 29 GB of source data.
- `s3d`: 69 MiB peak memory, about 40 s of CPU for the first run.
- Mover peak memory: 856 MiB (jellyfin) and 748 MiB (nextcloud) on the first run, against a
  1,012 MiB peak for the large NFS movers over the prior three days. S3 incrementals finished
  too fast to sample.
- **Not comparable:** the only NFS runs at equal concurrency overlapped the R2 sync, so NFS
  disk load was not measured cleanly, and no cold NFS initial run exists. The first S3 run
  was also not fully cold: some apps had already taken a scheduled S3 run.

So incrementals run at the same speed. S3 removes the NFS mounts and the uid 568 repository
permissions from movers, and adds a key, a LAN egress label and an experimental service.

Restores into isolated PVCs matched the exact snapshot, compared with `diff -r` against an
independent `kopia restore`: nextcloud (78,999 files, `33:33`) after full verification, and
karakeep (463 files, `1000:1000`) after full maintenance. Retention deleted snapshots from
the bucket. Blob reclamation is unproven, because kopia keeps unreferenced blobs for at
least 24 hours.

Restore from R2, with no credential leaving TrueNAS: `cloudsync.sync_onetime` (`direction
PULL`, `transfer_mode COPY`) pulled `volsync/kopiur` into a temporary dataset in 230 s
(1,198 objects, 15.5G, 0 errors). 1,197 objects matched `s3data` byte for byte;
`kopia.maintenance` differs because maintenance rewrites it. To read the copy, add kopia's
`.f` suffix to every object, export the dataset read-only, and run `kopia repository connect
filesystem --path=<mount> --flat --readonly`.

- `kopia snapshot verify --verify-files-percent=100` passed for all 25 sources: 190,735
  files, 27.3 GB, 1,658 s over NFS.
- karakeep: the same snapshot id restored from the copy and from the bucket, 463 files,
  `diff -r` clean.
- nextcloud: 78,999 files in 605 s, `diff -r` clean against a bucket snapshot with the same
  root object. Retention had already replaced the copy's snapshot id in the bucket.

Connecting kopia directly to R2 over S3 is untested.

## Rollback

Revert the switch, which brings `kopia-nas` back with `catalog.adoption: Ignore` so adoption
cannot prune NFS rows. Backups made on S3 stay in `kopia-s3`.

## NFS history

Keep the `VolsyncKopia` dataset and the R2 bucket `volsync`. Never delete the data or
repurpose the dataset. The Kopia browser reads the NFS repository directly, so history stays
browsable there. To restore it through Kopiur, bring back `clusterrepository-nas.yaml` and
`secret-nas.sops.yaml` from Git, then use a `Restore` naming `spec.repository` and a raw
`source.identity`, as in [the restore guide](kopiur-restore.md).

The TanguilleServer ZFS snapshot task (daily, 14 days, recursive) should hold freed blobs,
so read kopia statistics, not dataset `used`, when checking cleanup. It had not snapshotted
this dataset yet on 2026-11-06, so confirm it does before relying on it.
