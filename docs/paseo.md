# Paseo operator runbook

## Status and activation gates

**Prepared, detached, not deployed.** No entry has been added to the `ai`
namespace root. The custom image has local smoke evidence, not a published
registry digest. See [image contract](../docker/paseo/README.md) and
[approved design](superpowers/specs/2026-10-24-paseo-design.md).

Before activation, obtain and record all of these separately:

1. Permission to push/publish the image. Publish the smoke-tested source through
   the trusted workflow; verify provenance and anonymous cluster pullability
   (or separately design approved pull credentials). Record the real registry
   digest. A local Docker image ID is **not** a registry digest.
2. Set `PASEO_IMAGE_TAG` in the **paseo app** Kustomization's
   `spec.postBuild.substitute` to the published immutable
   `<source-revision-tag>@sha256:<registry-digest>`. Both seed and daemon use
   `ghcr.io/tanguille/paseo:${PASEO_IMAGE_TAG}` through one YAML anchor. There
   is deliberately no default and no checked-in render-test tag.
3. Obtain permission for SOPS operations and a human-provided, real nonempty
   password. Following the [SOPS runbook](sops.md), create encrypted
   `kubernetes/apps/ai/paseo/app/secret.sops.yaml`: Secret `ai/paseo-secret`,
   key `PASEO_PASSWORD`. Only then add it to the app's Kustomize resources.
   Never use a dummy password, copy sibling credentials, or put plaintext in
   Git, image layers, command arguments, logs, or Flux substitutions.
4. Re-render the real image reference and encrypted-Secret resource graph;
   parent runs full repository validation. Obtain separate push and deployment
   approval before adding `./paseo/ks.yaml` to the namespace resource list.
   Merge may trigger Flux automatically; it is a deployment boundary too.

The upstream `ai/kustomization.yaml` now contains the existing namespace apps;
Paseo is deliberately not listed there. Keep it detached until all activation
gates above are approved. Do not replace or otherwise edit the namespace root
as part of this work.

The required substitution shape is `${PASEO_IMAGE_TAG}` (no default). Validate
locally with strict Flux substitution; a missing variable must fail:

```bash
set -euo pipefail
mise exec -- kustomize build kubernetes/apps/ai/paseo/app |
  PASEO_IMAGE_TAG=render-only-not-published SECRET_DOMAIN=example.invalid \
    mise exec -- flux envsubst --strict
```

This non-pullable tag is **only for local render inspection**, not image
verification or activation. Do not assume the cluster enables strict
substitution, or use shell `${VAR:?error}` syntax as a Flux required-variable
guard. The detached graph and activation review are the deployment gate.
The Secret reference is required even if that gate is bypassed: the Helm values
provide the Secret name and key and omit `optional`, which Kubernetes defaults
to false. A missing Secret/key blocks Pod startup, and the image wrapper rejects
an empty password.

## Runtime and data boundaries

- One amd64 replica, `Recreate`, gVisor, UID/GID/fsGroup 1000. No token mount,
  RBAC grants, host mounts, Docker socket, or kubeconfig. Both containers drop
  all capabilities and disable privilege escalation, using RuntimeDefault
  seccomp. The writable root filesystem does not make agent execution safe:
  each agent can access both mounted volumes and their tokens.
- `paseo-home`: 20Gi ceph-block RWO at `/home/paseo`; persistent Paseo config,
  provider state and GitHub login. `paseo-workspace`: 50Gi at `/workspace`;
  Git metadata, dirty/untracked files and private checkout remain persistent.
- Init runs `/usr/local/bin/paseo-seed` with the same image as the daemon.
  It seeds config only when absent, and public `cluster` and `mesh-organiser`
  main-branch clones only when destinations are absent. Existing directories
  and symlinks are preserved; failures retry without overwriting existing work.
  No private repository is cloned by init.
- Separate kopiur roots generate one PVC, Restore, SnapshotPolicy and hourly
  SnapshotSchedule each, UID/GID 1000, repository `kopia-nas`, retention 24
  hourly / 7 daily. Home excludes only `/lost+found`, `/.cache/*`, `/.npm/*`;
  workspace excludes only `/lost+found`. Tokens and private source are backed
  up: retain the repository's access/encryption protections.

## Approved post-deployment login

These are operator instructions, **not actions already performed**. Confirm
deployment approval and readiness first; never log credentials or dump env.
The verified Helm render names the Deployment `paseo`, app container `app`.

```bash
mise exec -- kubectl -n ai get kustomizations paseo-home paseo-workspace paseo
mise exec -- kubectl -n ai get helmrelease paseo
mise exec -- kubectl -n ai get deployment paseo
mise exec -- kubectl -n ai get pods -l app.kubernetes.io/instance=paseo -o wide
mise exec -- kubectl -n ai exec -it deployment/paseo -c app -- bash
```

Inside that shell:

```bash
id
test "$(id -u)" = 1000 && test "$(id -g)" = 1000
printf 'HOME=%s\nPASEO_HOME=%s\n' "$HOME" "$PASEO_HOME"
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh auth status --hostname github.com
```

Use the displayed browser/device flow, least required account/repository
access, and do not run `gh auth token` or `--show-token`. Without a keyring,
`gh` may store its token in `~/.config/gh/hosts.yml`; the home PVC and its
backups then hold a sensitive plaintext-at-rest application credential.
Do not print/copy it into manifests or transfer workstation credentials.

Only after successful GitHub authentication, clone private `rsume`:

```bash
if gh auth status --hostname github.com >/dev/null 2>&1; then
  if [ ! -e /workspace/rsume ] && [ ! -L /workspace/rsume ]; then
    gh repo clone Tanguille/rsume /workspace/rsume -- --branch master
  else
    printf '%s\n' 'Existing /workspace/rsume left untouched.'
  fi
else
  printf '%s\n' 'Authenticate to GitHub first; nothing cloned.' >&2
fi
```

Do not run concurrent clones into the same destination. A failed manual clone
may leave a directory: inspect it with the operator, do not automatically
delete/reset/pull it. The `-L` check also protects dangling symlinks.

Authenticate each provider interactively, using supported headless/device
flows as offered by its CLI:

```bash
opencode auth login
claude
codex login
hermes setup
```

For Claude, complete its interactive sign-in flow. For Codex, use its supported
device-auth option if the browser callback cannot reach the pod. Do not bypass
provider policy or copy workstation tokens. Verify chosen versions' prompts
and credential locations without printing credential contents: OpenCode's
default state is under `~/.local/share/opencode`, Claude under HOME
(`~/.claude`/`~/.claude.json`), Codex under `~/.codex`, Hermes under
`$HERMES_HOME` (`/home/paseo/.hermes`). If a selected auth method stores state
elsewhere, stop and arrange persistence explicitly before relying on restart
survival. Interactive authentication and inference remain live acceptance
gates, not consequences of a successful CLI version check.

## Internal connection and acceptance

From the internal network, open `https://paseo.${SECRET_DOMAIN}` with the
operator's real domain. Connect directly to that internal HTTPS daemon and
enter the password in the client, not a URL. Relay is disabled. The only
HTTPRoute parent is `network/envoy-internal`; service port is 6767. Existing
namespace policies remain unchanged; no new public route/ingress allowance.

After approved deployment, check:

1. Both storage Kustomizations, app Kustomization and HelmRelease are Ready;
   gVisor init/daemon startup succeeds and the pod's effective `id` is 1000.
2. Internal DNS/TLS and WebSocket upgrades work. Unauthenticated and wrong-
   password **control** requests fail, authenticated control succeeds.
   Static UI assets and `/api/health` intentionally do not require login.
3. Each provider completes one benign session in the intended checkout;
   verify the session working directory before allowing changes. Health
   probes do not establish provider authentication or successful inference.
4. With separate restart permission, record a benign dirty/untracked workspace
   fixture and config/auth state, restart the workload, and verify they survive.
   Existing public/private repositories must not be overwritten. Do not treat
   local Docker smoke results as gVisor or live persistence evidence.
5. Inspect both backup roots:

   ```bash
   mise exec -- kubectl -n ai get snapshotpolicy paseo-home paseo-workspace
   mise exec -- kubectl -n ai get snapshotschedule paseo-home paseo-workspace
   mise exec -- kubectl -n ai describe snapshotpolicy paseo-home
   mise exec -- kubectl -n ai describe snapshotpolicy paseo-workspace
   mise exec -- kubectl kopiur snapshots list --policy paseo-home -n ai
   mise exec -- kubectl kopiur snapshots list --policy paseo-workspace -n ai
   ```

   Require Ready policies and recent successful snapshots for both PVCs.
   Request permission before manual snapshots or restore exercises. Snapshot
   success alone does not prove a restore works.

## Upgrades and rollback

Publish and test each new image through the approved workflow, then update
the digest-pinned `PASEO_IMAGE_TAG` in Git with normal validation and approval.
Config defaults do not replace existing `$PASEO_HOME/config.json`. Merge
intentional provider changes manually into the persistent version-1 config;
retain `agents.providers.hermes` extending `acp`, label `Hermes`, command
`["hermes", "acp"]`. Do not delete config merely to force reseeding.

Rollback by reverting the workload image/config change through GitOps,
retaining **both storage Kustomizations, PVCs and backup resources**. Do not
remove the namespace resource entry or prune storage as a casual rollback.
Image rollback is not data rollback: if a version migrated state incompatibly,
stop and request the [kopiur restore workflow](kopiur-restore.md), coordinating
both roots and suspending all writers. Deletion, restore and live reconciliation
each need their own authorization.
