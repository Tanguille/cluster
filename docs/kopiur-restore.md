# Kopiur Restore Guide

This guide explains how to properly restore data from kopiur (Kopia-native) backups.
Supersedes `docs/volsync-restore.md` (VolSync, decommissioned 2026-07-12).

## Understanding the Components

The `components/kopiur` component creates, per app:

- `SnapshotPolicy`: the backup identity + retention/schedule config
- `SnapshotSchedule`: the cron trigger (hashed minute + jitter, not a fleet-wide fixed time)
- `PersistentVolumeClaim`: created via `dataSourceRef` pointing at the `Restore` below
- `Restore`: a **passive populator** — it does nothing until a PVC's `dataSourceRef` claims it,
  then restores the selected snapshot into that PVC

**Important**: the PVC spec is **immutable** after creation, same constraint as before. `kind`
in `dataSourceRef` should be `Restore` (`apiGroup: kopiur.home-operations.com`) — if you ever
see `ReplicationDestination`/`volsync.backube`, something recreated a pre-migration resource;
that shouldn't happen post-decommission.

## Identity: verify live, don't trust the manifest

Several apps' `securityContext.runAsUser` in their `helmrelease.yaml` does **not** match the uid
that actually owns their files — the image's own entrypoint (gosu/s6-overlay/su-exec) drops
privilege internally after starting as root, invisible to Kubernetes. Confirmed on `fileflows`,
`odysseus`, and `nextcloud` during this migration; `odysseus` crashlooped after being restored
as root when its real uid was 1000. **Before touching an app's `mover.securityContext`,
verify**:

```bash
kubectl exec -n <ns> deployment/<app> -- id
kubectl exec -n <ns> deployment/<app> -- ls -la <actual-mount-path>
```

Check `volumeMounts`/`volumes` first for the real PVC `claimName` and mount path — some apps
mount more than one PVC (e.g. nextcloud has a second, unrelated 500Gi NFS volume at the
"obvious" `/var/www/data` path; the migrated one is elsewhere).

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
kubectl wait --for=jsonpath='{}' --timeout=1s pvc/jellyfin -n media 2>&1 || true
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

This is a real git change to the app's `restore.yaml` (or a live `kubectl edit` for a one-off
test) — not a `kubectl patch` trigger like VolSync's `restoreAsOf`.

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

**Critical**: `status.phase: Completed` alone is not sufficient — check `Ready: True` too. See
the kopiur#233 warning below for why.

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
kubectl exec -n media deployment/jellyfin -- ls -la <mount-path>   # uid should match Step 9's id output
kubectl kopiur snapshot now --policy jellyfin -n media --wait      # fresh snapshot should succeed
kubectl kopiur snapshots list --policy jellyfin -n media           # file count/size should be continuous with history
```

## ⚠️ Critical: kopiur#233 (open, unfixed as of this writing)

**Never let an app's `Restore` CR get deleted-and-recreated while its target PVC is `Bound`** —
a Flux prune/recreate cycle, a `ClusterRepository` reset, or a forced Kustomization re-apply all
qualify. This silently restores into an orphaned `prime-<uid>` staging PVC that never gets
garbage-collected — one reporter hit 49 orphaned PVCs / 120GiB from a single
`ClusterRepository` rebuild.

**The symptom is invisible to normal health checks**: `status.phase` still reports `Completed`,
but `status.conditions` stay `Ready: False`. Flux's own health check and kstatus won't flag it.
Confirmed live 2026-07-12: `karakeep`'s double race-hit during its cutover left exactly this
signature, plus an orphaned 5Gi `prime-*` PVC that sat unnoticed for 104 minutes.

**Sweep for it periodically or after any suspicious Flux/repository event**:

```bash
# Restore CRs showing the silent Completed/Ready:False signature
kubectl get restore -A -o json | jq -r '.items[] | select(.status.phase == "Completed" and (.status.conditions[] | select(.type == "Ready" and .status == "False"))) | "\(.metadata.namespace)/\(.metadata.name)"'

# Orphaned staging PVCs (full-size Ceph storage, invisible otherwise)
kubectl get pvc -A -l kopiur.home-operations.com/op=restore-populate

# App PVCs stuck Terminating despite pods running (a related failure mode — deleting a PVC
# without fully draining referencing pods first, e.g. mid-recovery from another race)
kubectl get pvc -A | grep -i terminating
```

If found: the `Restore` CR itself usually wasn't recreated (just its staging PVC leaked), so
it's normally still safely re-triggerable via the standard Step 3-8 recipe above — scale down
*properly* this time (drain every referencing pod), delete the app's PVC (and any stuck
`Terminating` one, once nothing references it), resume, and verify `Ready: True` this time.
Delete orphaned `prime-*` PVCs directly (check `ownerReferences` first to confirm which
`Restore` it belongs to, and that the `Restore`'s *current* live state — `status.pvcPrime` —
doesn't still reference it before deleting).

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

### Issue: PVC dataSourceRef shows `ReplicationDestination`/`volsync.backube` (should never happen post-decommission)

**Cause**: this was the volsync-vs-kopiur race hit repeatedly during the original migration —
volsync's controller (now fully removed) won a repopulation race against kopiur's `Restore`.
Post-decommission this specific failure mode can't recur (there's no volsync controller left to
race), but if you ever see this `Kind` on a PVC's `dataSourceRef`, something is very wrong —
investigate before proceeding, don't just retry.

### Issue: Mover stuck, no log output, near-zero CPU

**Cause**: repo-wide permission drift — kopia silently retries `PermissionDenied` forever
instead of erroring. Check `find /repo -type d -not -perm 0775` / `-type f -not -perm 0664`
from a mover shell before assuming a slow backend.

## Key Points

1. **Always suspend both Kustomization and HelmRelease** before starting a restore
2. **Deleting the PVC alone re-triggers the existing `Restore`** — no need to delete/recreate
   the `Restore` CR itself for a plain restore-to-latest
3. **Never delete-and-recreate a `Restore` CR whose PVC is `Bound`** (kopiur#233) — check for
   orphaned `prime-*` PVCs and the `Completed`/`Ready:False` signature if this ever happens
4. **Verify identity live** (`kubectl exec ... id`) before trusting a manifest's declared
   `securityContext` — entrypoint privilege drops are invisible to it
5. **PVC spec is immutable** — if it's wrong, delete and let the `Restore` recreate it
