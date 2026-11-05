# Forgejo and Flux outage recovery

GitHub stays the normal authoring location. Forgejo keeps a writable
`tanguille/cluster` copy, and after cutover Flux reads Forgejo's `main`. Until
then Flux stays on GitHub and the sync CronJob is suspended.

- UI: `https://forgejo.${SECRET_DOMAIN}`, internal, HTTPS Git only.
- In-cluster Git: `http://forgejo.default.svc.cluster.local:3000/tanguille/cluster.git`.
- Kopiur backs up `/var/lib/gitea`. The `forgejo-sync-cache` PVC is disposable.
- Only Git branches and tags are covered; issues, PRs and CI are not.

## Setup

1. Create `kubernetes/apps/default/forgejo/app/secret.sops.yaml` with Secret
   `forgejo-secret`, key `SECRET_KEY`, set to a fresh random value, and add it to
   the app's `kustomization.yaml`. Keep this key across restarts and restores.
2. Deploy with sync still suspended and wait for `deployment/forgejo-app` to be
   healthy.
3. Create the administrator, saving the generated password straight to the
   password manager:

   ```bash
   mise exec -- kubectl -n default exec -it deployment/forgejo-app -- \
     forgejo admin user create --username tanguille --email YOUR_EMAIL \
     --admin --random-password --must-change-password
   ```

4. Create an empty public `tanguille/cluster` repository with default branch
   `main`. Do not add a README or enable mirroring.
5. Create a token limited to this repository with `write:repository`, store it as
   key `token` of `forgejo-sync-secret` in `sync-secret.sops.yaml`, and add that
   to the Kustomization. Branch protection must allow it to push `main`.
6. With no other sync Job running, run the first sync:

   ```bash
   mise exec -- kubectl -n default create job forgejo-sync-initial --from=cronjob/forgejo-sync
   mise exec -- kubectl -n default wait --for=condition=complete job/forgejo-sync-initial --timeout=300s
   ```

   Compare `git ls-remote --heads --tags` on both remotes, verify anonymous reads
   and source-controller access, record a Kopiur snapshot, and protect `main`
   against force pushes on both services.
7. Set `controllers.sync.cronjob.suspend: false` on GitHub and verify a scheduled
   sync succeeds.

## Flux cutover

Do not add `dependsOn: forgejo` to the root source or sync: that creates a
recovery dependency cycle.

1. Suspend only the `flux-instance` Kustomization, so Flux can't switch to a
   lagging mirror that still names GitHub.
2. In `flux-instance/app/helmrelease.yaml`, set `sync.url` to the in-cluster URL
   above and `sync.interval` to `1m`. Merge through GitHub.
3. Wait for the sync. Verify both `main` hashes match and source-controller can
   fetch the commit, then resume `flux-instance`.
4. Verify `GitRepository/flux-system` shows the Forgejo URL, Ready=True and the
   expected revision, and that a harmless GitHub commit reaches Flux.
5. Rehearse an outage write on a temporary branch, and confirm the sync refuses a
   divergent source before relying on it for `main`.

## Sync behavior

`forgejo-sync` runs every five minutes and never force-pushes. Any rejected ref
fails the Job, so read its logs even when `main` is current. Rebased branches and
rewritten tags keep their old Forgejo history, and deletions are not propagated:
reconcile these by hand, checking first for Forgejo-only commits.

## GitHub outage

1. Set `suspend: true` for the sync in Forgejo's copy of the HelmRelease and wait
   for any active sync Job to finish. Never run manual and scheduled sync Jobs
   together: `Forbid` does not lock manually created Jobs.
2. Review and merge on Forgejo; Flux deploys `main`.
3. New installs and restarts can still fail if `ghcr.io` or other external
   dependencies are down.

## Return to GitHub

1. Leave sync suspended and inspect
   `git log --left-right --graph github/main...forgejo/main`.
2. Move outage work to GitHub with normal pushes. Forgejo's deployed commits must
   stay ancestors of GitHub `main`, so do not squash-merge them, and never
   force-push either `main`.
3. Set `suspend: false` in GitHub, then run one manual sync Job from the
   still-suspended CronJob, with no other Job active.
4. Verify matching `main` hashes, Flux's Ready revision and a scheduled sync.

## If Forgejo is unavailable

Keep a fresh clone or Git bundle and the SOPS recovery material outside the
cluster. Kopiur restores the Forgejo PVC; the sync cache can start empty. For a
full rebuild, revert the cutover in the recovery checkout before bootstrapping.

On a running cluster, first make sure GitHub contains all deployed outage work:
stale history could revert workloads or prune resources. Suspend the
`flux-instance` Kustomization and its HelmRelease, change the live FluxInstance
sync URL back to GitHub, and persist that URL in Git before resuming. Patching
only the generated GitRepository is not enough.
