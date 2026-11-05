# Forgejo and Flux outage recovery

GitHub stays the normal authoring location. Forgejo keeps a native pull mirror of
`tanguille/cluster`, refreshed every 10 minutes, and after cutover Flux reads its
`main`. Until then Flux stays on GitHub.

- UI: `https://forgejo.${SECRET_DOMAIN}`, internal, HTTPS Git only.
- In-cluster Git: `http://forgejo.default.svc.cluster.local:3000/tanguille/cluster.git`.
- Kopiur backs up `/var/lib/gitea`, including the mirror settings.
- Only Git branches and tags are covered; issues, PRs and CI are not.

The mirror is read-only and follows GitHub exactly, including force pushes and
deletions. Keep `secret.sops.yaml` with the SOPS backup: `SECRET_KEY` must survive
restarts and restores.

## Setup

1. Create the administrator, saving the generated password straight to the
   password manager:

   ```bash
   mise exec -- kubectl -n default exec -it deployment/forgejo -- \
     forgejo admin user create --username tanguille --email YOUR_EMAIL \
     --admin --random-password --must-change-password
   ```

2. Sign in and change the password. Under **New Migration**, pick **Git**, enter
   `https://github.com/Tanguille/cluster.git`, name it `cluster`, keep it public
   and tick **This repository will be a mirror**.
3. Compare `git ls-remote --heads --tags` on both remotes, verify anonymous reads
   and source-controller access, and record a Kopiur snapshot.

## Flux cutover

Do not add `dependsOn: forgejo` to the root source or sync: that creates a
recovery dependency cycle.

1. Suspend only the `flux-instance` Kustomization, so Flux can't switch to a
   lagging mirror that still names GitHub.
2. In `flux-instance/app/helmrelease.yaml`, set `sync.url` to the in-cluster URL
   above and `sync.interval` to `5m`. Merge through GitHub.
3. Wait for the mirror, or use **Synchronize now**. Verify both `main` hashes match
   and source-controller can fetch the commit, then resume `flux-instance`.
4. Verify `GitRepository/flux-system` shows the Forgejo URL, Ready=True and the
   expected revision, and that a harmless GitHub commit reaches Flux.

## GitHub outage

1. In the repository's settings, under **Danger Zone**, use **Convert to regular
   repository** and type `tanguille/cluster`. This is one-way.
2. Push and merge on Forgejo; Flux deploys `main`.
3. New installs and restarts can still fail if `ghcr.io` or other external
   dependencies are down.

## Return to GitHub

1. Inspect `git log --left-right --graph github/main...forgejo/main` and move all
   outage work to GitHub with normal pushes. Never force-push `main`.
2. Confirm GitHub `main` has the same content as Forgejo's, because Flux prunes to
   whatever the new mirror holds.
3. Delete the Forgejo repository and repeat setup steps 2 and 3. Flux keeps its last
   artifact until the mirror is back.

## If Forgejo is unavailable

Keep a fresh clone or Git bundle and the SOPS recovery material outside the
cluster. Kopiur restores the Forgejo PVC. For a full rebuild, revert the cutover in
the recovery checkout before bootstrapping.

On a running cluster, first make sure GitHub contains all deployed outage work:
stale history could revert workloads or prune resources. Suspend the
`flux-instance` Kustomization and its HelmRelease, change the live FluxInstance
sync URL back to GitHub, and persist that URL in Git before resuming. Patching
only the generated GitRepository is not enough.
