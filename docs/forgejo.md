# Forgejo and Flux outage recovery

GitHub is the normal authoring location for `Tanguille/cluster`. Forgejo keeps a
normal, writable `tanguille/cluster` repository. After the staged cutover, Flux
always reads Forgejo's `main` branch and `kubernetes/apps` path.

The initial deployment deliberately leaves Flux on GitHub and the sync CronJob
suspended. Publishing, SOPS secret creation, and live operations require owner
approval. Run commands through the approved cluster connection; the examples
below use the repository kubeconfig. Verify the branch/worktree and run
`git pull --ff-only && git status` before repository changes.

## Deployment

- Internal UI: `https://forgejo.${SECRET_DOMAIN}`; HTTPS Git access, no SSH listener.
- In-cluster Git: `http://forgejo.default.svc.cluster.local:3000/tanguille/cluster.git`.
- Forgejo rootless, uid/gid 1000, SQLite, 10Gi `forgejo` Ceph PVC.
- Kopiur backs up `/var/lib/gitea`, including repositories, SQLite, generated
  internal credentials and `custom/conf/app.ini`. Keep the separate SOPS key
  backup as described in [sops.md](sops.md).
- The disposable 1Gi `forgejo-sync-cache` PVC avoids cloning the entire GitHub
  history every five minutes. It needs no backup.
- Registration, Actions, LFS and experimental federation are disabled. This first
  deployment covers the cluster repository's ordinary Git content only.

### Credentials and initial repository

1. Create `kubernetes/apps/default/forgejo/app/secret.sops.yaml` containing a
   Kubernetes Secret named `forgejo-secret`, key `SECRET_KEY`, with a freshly
   generated random encryption key. Encrypt it with the repository's SOPS rules
   and add it to the app's `kustomization.yaml`. Never commit a plaintext secret.
   Generate once, retain across restarts/restores: `INSTALL_LOCK=true` does not
   safely generate this key. No GitHub credential is needed to fetch this public
   repository.
2. Publish and deploy the app with sync still suspended. Wait for
   `deployment/forgejo-app` and `/api/healthz` to be healthy.
3. Create the local administrator using the container CLI (no GitHub login
   dependency). Replace the email with the owner's address. Run interactively
   and save the generated password directly in the password manager, not logs:

   ```bash
   mise exec -- kubectl -n default exec -it deployment/forgejo-app -- \
     forgejo admin user create --username tanguille --email YOUR_EMAIL \
     --admin --random-password --must-change-password
   ```

4. Sign in, change the initial password, and create an **empty normal public**
   `tanguille/cluster` repository with default branch `main`. Do not initialize
   it with a README, and do not enable pull or push mirroring. Public read access
   matches the existing GitHub repository and lets Flux read without a token;
   the Forgejo route itself is internal.
5. Create a Forgejo access token restricted to this repository with
   `write:repository`. Store it as key `token` in `forgejo-sync-secret` in a
   separate `sync-secret.sops.yaml`, encrypt it and add that file to the app's
   Kustomization. The token must be able to push `main`; branch protection must
   allow this sync identity. Do not give it GitHub credentials.
6. With no other sync Job running, run the suspended CronJob once:

   ```bash
   mise exec -- kubectl -n default create job forgejo-sync-initial --from=cronjob/forgejo-sync
   mise exec -- kubectl -n default wait --for=condition=complete job/forgejo-sync-initial --timeout=300s
   ```

   Verify branch/tag hashes against GitHub with `git ls-remote --heads --tags`
   on both remotes, verify anonymous reads and network access from Flux's
   source-controller, and record a successful Kopiur snapshot before cutover.
   Protect `main` against force pushes on both services.
7. Set `controllers.sync.cronjob.suspend: false` in Forgejo's HelmRelease on
   GitHub and let Flux deploy it. Verify a scheduled sync succeeds. Flux still
   reads GitHub at this point.

### Flux cutover

The exact reviewed change is [forgejo-flux-cutover.patch](forgejo-flux-cutover.patch).
It changes the FluxInstance's generated source URL and its polling interval to
one minute. All existing `sourceRef.name: flux-system` references remain valid.
Polling avoids depending on a GitHub webhook arriving before a mirror update.

1. Suspend only the `flux-instance` Flux Kustomization before publishing the
   cutover. Keep the root `flux-system` Kustomization, source-controller and
   Forgejo running. This prevents switching to a lagging mirror that still
   declares GitHub as the source.
2. Apply `git apply --check docs/forgejo-flux-cutover.patch`, then
   `git apply docs/forgejo-flux-cutover.patch`; validate and publish the change
   through the normal approved GitHub workflow.
3. Wait for the sync job to copy the merged commit. Verify **both `main` hashes
   match**, the copied manifest contains the Forgejo URL, and the source-controller
   can fetch it. Resume the `flux-instance` Kustomization.
4. Verify `GitRepository/flux-system` has the Forgejo URL, Ready=True, and the
   expected revision; verify the root Kustomization and HelmReleases reconcile.
   Confirm an approved harmless GitHub commit reaches Forgejo and Flux.
5. Exercise outage writes on a temporary branch first, then recover it to GitHub.
   Check that the sync refuses an older/divergent source before relying on this
   for `main`.

Do not add `dependsOn: forgejo` to the root source/sync: Forgejo is itself managed
through this source, so that would create a recovery dependency cycle.

## Synchronization behavior

`forgejo-sync` fetches GitHub every five minutes and pushes branches/tags without
forcing updates. Only its disposable source cache permits forced updates. A
failed fetch never pushes cached content. Git rejects divergent or older refs
individually while accepting safe updates: a rebased Renovate branch must not
block deployment of a fast-forward `main`. Any rejected ref makes the Job fail;
inspect its logs even if `main` is current. Failed Jobs remain visible for an
hour and are covered by the cluster's existing Job monitoring.

Rebased feature branches and rewritten tags retain their old Forgejo history
until explicitly reconciled. Prefer a new branch name for outage work; before
replacing an obsolete mirror branch, check it contains no Forgejo-only commits.

Remote branch/tag deletions are deliberately not propagated. Clean up obsolete
refs explicitly after checking both sides. The job does not merge history,
delete repositories, synchronize LFS, or synchronize issues/PR discussions/checks.
Metadata and CI continuity are separate from this Git/Flux fallback.

## GitHub outage

1. Push to the Forgejo remote. First set the sync CronJob's `suspend: true` in the
   Forgejo copy of its HelmRelease. Flux will apply it. Wait for any active sync
   Job to finish before further maintenance. Never make simultaneous manual and
   scheduled sync Jobs: CronJob `Forbid` does not lock manually created Jobs.
2. Continue reviewing/merging on Forgejo. Changes to `main` are deployed by Flux.
   Even before suspension reaches the cluster, a stale GitHub `main` cannot
   overwrite the new Forgejo commits because pushes are never forced.
3. A GitHub outage does not stop already-running workloads. Images/charts fetched
   from `ghcr.io` and other external dependencies are not made independent by
   this Git mirror; new installs or restarts can still depend on those services.

## Return to GitHub

1. Leave incoming sync suspended. Fetch both remotes into a local checkout and
   inspect `git log --left-right --graph github/main...forgejo/main`.
2. Transfer outage branches/commits to GitHub using normal pushes and reviews.
   Resolve divergent histories explicitly. Preserve Forgejo's deployed commits
   as ancestors of GitHub `main`: squash-merging replacements will not allow a
   fast-forward return to Forgejo. Never force-push either `main`.
3. Set `suspend: false` in the recovered GitHub history. Run one manual sync Job
   from the still-suspended CronJob, after confirming no other Job is active.
   This carries the resume commit to Forgejo, where Flux enables the schedule.
4. Verify matching `main` hashes, Flux's Ready revision, and a subsequent
   scheduled sync. Resolve any conflicting tags before retrying.

## Recovery if Forgejo is unavailable

Keep a fresh local clone or Git bundle outside this cluster and the SOPS recovery
material. Kopiur restores the Forgejo PVC; the sync cache can start empty.
During a full rebuild, use the existing bootstrap procedure with GitHub (or the
offline clone) until storage and Forgejo are restored. The cutover manifest
must be reverted to GitHub in the recovery checkout before bootstrapping it.

For a running cluster where Forgejo cannot be restored promptly, suspend the
`flux-instance` Kustomization and its HelmRelease before changing the live
FluxInstance sync URL back to GitHub. The operator owns the generated
GitRepository, so patching only that child is insufficient. First make sure
GitHub includes all deployed outage work: switching to stale history could
revert workloads or prune resources. Persist the GitHub URL in Git before
resuming the HelmRelease and Kustomization. These are approved emergency live
operations, not the normal deployment path.

## Verification and upstream references

Local safety tests: `python3 -m unittest discover -s scripts -p test_forgejo_sync.py -v`.
Run the normal repository validation before publishing.

- [Rootless image](https://forgejo.org/docs/latest/admin/installation/docker/)
- [Forgejo CLI](https://forgejo.org/docs/latest/admin/command-line/)
- [Encryption-key initialization](https://codeberg.org/forgejo/forgejo/src/tag/v17.0.1/modules/setting/security.go)
- [Flux source configuration](https://fluxoperator.dev/docs/instance/sync/)
- [Native mirror overwrite behavior](https://forgejo.org/docs/latest/user/repo-mirror/)
