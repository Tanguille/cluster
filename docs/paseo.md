# Paseo operations

## Architecture and activation

**Detached, not deployed.** Use the digest-pinned `ghcr.io/joryirving/paseo`
community image in the HelmRelease; no custom image or publishing workflow.
OpenCode and GitHub CLI are bundled. Hermes is installed manually once after
deployment; Claude/Codex and other unused built-ins are disabled in the seeded
config. An unavailable Hermes executable must not prevent daemon startup.

The explicit command runs tini → ConfigMap password guard → upstream
`paseo-docker-entrypoint` without arguments. It deliberately bypasses the image's
`paseo-start` CMD, which requires LiteLLM credentials and overwrites config.
No LiteLLM key is required. Missing or empty `PASEO_PASSWORD` fails closed.

Before activation, obtain separate approval for:

- A real nonempty human-supplied password and [SOPS operations](sops.md).
  Create encrypted `app/secret.sops.yaml` for Secret `ai/paseo-secret`, key
  `PASEO_PASSWORD`, then include it in the app Kustomization. No dummy value,
  plaintext in Git/arguments/logs, or sibling/workstation credential imports.
- Final validation/review, push and deployment. Only then add
  `./paseo/ks.yaml` to the existing ai namespace root. Merge can trigger Flux;
  it is also a deployment boundary. The public image already has a digest;
  no custom-image build/publication or image-tag substitution gate remains.

One Recreate replica runs with gVisor, UID/GID/fsGroup 1000, dropped capabilities,
RuntimeDefault seccomp, no privilege escalation/token/RBAC/host mounts. Agents
can access all mounted data and credentials; gVisor is not isolation between
agents. The only route is `https://paseo.${SECRET_DOMAIN}` through internal
`network/envoy-internal`, port 6767. Relay disabled; existing policies unchanged.

## Persistence and seeding

`paseo-storage` provides one `paseo` 70Gi ceph-block RWO PVC. `home` subPath
mounts at `/home/paseo`; `workspace` mounts at `/workspace`. Capacity is shared,
so workspace growth can exhaust home space. Non-root `prepare-storage` first
mounts the root at `/data`, creates missing subdirectories with umask 077, and
rejects symlinks/inaccessible paths without chown/chmod of existing data.
fsGroup must make the Ceph volume root writable; confirm this on first live use.

The read-only `paseo-runtime` ConfigMap supplies guard, seed and default config.
Its substitution-disabled annotation protects shell expressions from Flux.
Seed copies default config only when absent; clones public `Tanguille/cluster`
and `Tanguille/mesh-organiser` main branches only into absent destinations.
Temporary sibling clones and no-clobber publication preserve dirty files,
existing directories and dangling symlinks. No startup dependency installer or
private clone runs. Later defaults require an intentional manual config merge;
never delete persistent config just to force reseeding.

One kopiur policy/schedule `paseo` backs up both subdirectories hourly to
`kopia-nas`, retaining 24 hourly/7 daily snapshots. Exclude only `/lost+found`,
`/home/.cache/*`, `/home/.npm/*`. Git metadata, credentials and installed Hermes
runtime remain backed up. Keep repository encryption/access protections.
Home/workspace have one restore boundary; active snapshots are not necessarily
application-consistent.

## Operator login after approved deployment

Commands below are instructions, not actions already performed:

```bash
mise exec -- kubectl -n ai get kustomizations paseo-storage paseo
mise exec -- kubectl -n ai get helmrelease paseo
mise exec -- kubectl -n ai get pods -l app.kubernetes.io/instance=paseo -o wide
mise exec -- kubectl -n ai exec -it deployment/paseo -c app -- bash
```

Inside the pod, verify identity/mounts, then authenticate GitHub:

```bash
id
test "$(id -u)" = 1000 && test "$(id -g)" = 1000
stat -c '%u:%g %a %n' /home/paseo /workspace
test -w /home/paseo && test -w /workspace
gh auth login --hostname github.com --git-protocol https --web
gh auth setup-git
gh auth status --hostname github.com
```

Use least required account/repository access. Without a keyring, gh may store
its token in `~/.config/gh/hosts.yml`; HOME and backups then hold a sensitive
application credential. Never print tokens (`gh auth token`, `--show-token`) or
copy workstation credentials. Only after authentication, without concurrent
clones, create the private checkout:

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

Inspect failed/partial manual clones with the operator; never auto-delete/reset
an existing destination. Authenticate OpenCode with `opencode auth login`.
Its default state under `~/.local/share/opencode` persists on HOME.

## One-time manual Hermes installation

Run **inside the UID1000 pod shell after deployment**, not an init container.
The official pinned installer uses PM to provision Python/dependencies under
persistent HOME. First installation requires outbound network access and disk
space; git/curl are included in the community image. Do not install system
packages, escalate privileges or replace the image's Node/PATH shims.

Download the pinned installer to a temporary file and inspect it first:

```bash
set -euo pipefail
HERMES_COMMIT=bf0203a794a07aac3ff764972ab9087baa4e4f4e
installer=$(mktemp /tmp/hermes-install.XXXXXX.sh)
curl --fail --show-error --location \
  "https://raw.githubusercontent.com/NousResearch/hermes-agent/$HERMES_COMMIT/scripts/install.sh" \
  --output "$installer"
cat "$installer"
```

After inspection, run that downloaded file (not mutable curl-to-shell):

```bash
bash "$installer" --non-interactive \
  --hermes-home /home/paseo/.hermes --commit "$HERMES_COMMIT"
rm -- "$installer"
/home/paseo/.local/bin/hermes acp --check
/home/paseo/.local/bin/hermes setup
```

The installer's standard dependency selection includes ACP. If install/check
fails, retain the error for diagnosis; do not add startup pip/uv installs or
invent a fallback packaging path. Source/runtime/state under `~/.hermes` and
the launcher under `~/.local/bin` persist; do not move them to `/tmp` or excluded
caches. Paseo config uses absolute command
`["/home/paseo/.local/bin/hermes", "acp"]`, preserving the image's existing PATH.
Complete Hermes provider authentication interactively. Refresh provider
availability; any required daemon restart needs approval. Installation/ACP
checks do not prove authenticated inference or gVisor compatibility.

## Acceptance and rollback

After deployment approval, verify Flux/Helm readiness, UID1000/CSI ownership,
internal TLS and WebSocket upgrades. Wrong/missing-password **control** must
fail and authenticated control succeed; public UI assets and `/api/health`
are intentional. Run one benign session each for OpenCode and installed Hermes
in the intended checkout. Health alone is not agent usability evidence.

With separate restart approval verify auth, Hermes installation, mutable config
and dirty/untracked workspace survive. Verify shared backup status:

```bash
mise exec -- kubectl -n ai get snapshotpolicy,snapshotschedule
mise exec -- kubectl -n ai describe snapshotpolicy paseo
mise exec -- kubectl kopiur snapshots list --policy paseo -n ai
```

Require Ready and recent successful snapshots. Manual snapshots/restores need
permission; snapshot success is not restore evidence.

Upgrade the community image by reviewed digest changes. Treat Hermes updates
as separate explicit manual changes with a recorded source pin; no silent
startup updates. Merge provider defaults into existing config deliberately.
Rollback workload image/config in Git while retaining storage Kustomization,
PVC and backups. Do not remove the namespace entry or prune storage as rollback.
Image rollback does not undo persisted Hermes/state migrations; incompatible
state needs an approved [kopiur restore](kopiur-restore.md) of the shared volume
with all writers suspended.
