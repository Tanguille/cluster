# Paseo standalone GitOps deployment

## Status and scope

Approved direction, recorded 2026-10-24; implementation awaits plan approval.
Deploy a standalone `paseo` application **in the existing `ai` namespace**, not
a new namespace. Existing OpenCode and Hermes deployments remain untouched.
This document does not authorize implementation, publication, SOPS operations,
or live changes.

## User-facing behavior

- Serve the bundled UI at `https://paseo.${SECRET_DOMAIN}` through
  `envoy-internal` only. No external route or relay; explicitly set
  `PASEO_RELAY_ENABLED=false`.
- Require a password for daemon control/API/WebSocket access. Upstream static
  UI assets and `/api/health` are public by design; a public login shell is not
  an authentication failure. An authenticated connection must work and an
  unauthenticated control connection must fail.
- Offer OpenCode, Claude Code, Codex, and Hermes ACP from one prebuilt custom
  image. Authenticate providers interactively after deployment, persisting
  their state; never import credentials from the workstation or sibling pods.
- Seed `/workspace/cluster` from `https://github.com/Tanguille/cluster.git`
  branch `main`, and `/workspace/mesh-organiser` from
  `https://github.com/Tanguille/mesh-organiser.git` branch `main`, only when each
  destination is absent. Never pull, reset, clean, overwrite, or change a
  branch in an existing destination, including a non-Git directory or symlink.
- Clone private `Tanguille/rsume` branch `master` only after the operator logs
  into GitHub inside the running pod. It is not an init-container input.

## Image and dependency contract

Publish `ghcr.io/tanguille/paseo` with an immutable source-revision tag and
deploy its digest. GitHub Actions builds and smoke-tests PRs without publishing;
trusted main/explicit dispatch runs may publish only after smoke success.
Publishing and pushing require separate permission; a workflow dispatch that
publishes is not a read-only validation operation.

Verified candidate pins on 2026-10-24:

| Component | Selected version/source | Installation |
| --- | --- | --- |
| Paseo | `ghcr.io/getpaseo/paseo:0.13.0` | Resolve and commit registry digest before build |
| OpenCode | `opencode-ai@1.18.35` | Global npm prefix outside HOME |
| Claude Code | `@anthropic-ai/claude-code@2.1.310` | Global npm prefix outside HOME |
| Codex | `@openai/codex@0.164.0` | Global npm prefix outside HOME |
| Hermes | `v0.22.5`, commit `bf0203a794a07aac3ff764972ab9087baa4e4f4e` | Pinned source and upstream PM lock |
| GitHub CLI | `v2.103.2` | Official release archive with verified checksum |

Versions are research evidence, not a claim that the combined image builds.
Record npm integrity/lock data and source/archive checksums during implementation;
never substitute `main`, `latest`, a floating installer, or an invented digest.
Initial image platform is `linux/amd64`; do not claim multi-platform support.

Keep agent binaries in `/usr/local`, Hermes source/venv/runtime under
`/opt/hermes`, and all downloaded interpreters outside `/home/paseo`.
Use Hermes' pinned `pm/lock.json`, Python 3.14 runtime and frozen `uv.lock` via
`pm.build_env --source /opt/hermes --out /opt/hermes/.venv --sealed
--record-selection --extra acp`; supply the PM-provisioned Python explicitly.
The source must remain available for its source-bound runtime. The current
Hermes Dockerfile is the reference for preparing PM's own runtime; do not copy
its s6 entrypoint or unrelated messaging/browser bundles. Install the ACP extra
at build time, not on pod startup. Its current documentation uses
`pm.sync_venv(['acp'], explicit=True)`, rather than treating an unpinned curl
installer or `pip install -e '.[acp]'` as the full current installation recipe.
Do not replace Paseo's Node globally with Hermes' toolchain.

The upstream Paseo entrypoint supports running directly as UID/GID 1000 and
execs the daemon; retain its setup/exec behavior and `tini`. A small wrapper
must reject an empty/missing `PASEO_PASSWORD` before starting (upstream merely
warns). Run the pod and init container as 1000:1000 with fsGroup 1000,
`runAsNonRoot`, dropped capabilities, no privilege escalation, RuntimeDefault
seccomp, `runtimeClassName: gvisor`, and no service-account token mount.
No host mounts, Docker socket, kubeconfig, RBAC grant, or cluster-admin access.

## Configuration and storage

Set `HOME=/home/paseo`, `PASEO_HOME=/home/paseo/.paseo`,
`PASEO_LISTEN=0.0.0.0:6767`, `PASEO_WEB_UI_ENABLED=true`,
`PASEO_HOSTNAMES=paseo.${SECRET_DOMAIN}`, and `HERMES_HOME=/home/paseo/.hermes`.
Read `PASEO_PASSWORD` from required Secret `ai/paseo-secret`, key
`PASEO_PASSWORD`. Omit `optional`; Kubernetes defaults it to false. No secret
value is specified here.

Seed a version-1 Paseo config into `$PASEO_HOME/config.json` only if absent;
the Hermes entry is `agents.providers.hermes` with `extends: "acp"`,
`label: "Hermes"`, and `command: ["hermes", "acp"]`. Config remains writable
in the PVC's home subdirectory; later default changes do not silently replace user config.
Operators merge intentional provider changes explicitly. Built-in providers
use installed executables; no runtime `npx` downloads.

Storage simplification approved 2026-10-25: one shared PVC, following the
home/workspace subPath layout of joryirving/home-ops and aumer-amr/labv2 Paseo.
This replaces detached draft resources; there is no deployed data migration.

| PVC | SubPath mounts | Initial size | Backup |
| --- | --- | --- | --- |
| `paseo` | `home` → `/home/paseo`, `workspace` → `/workspace` | 70Gi shared | Existing kopiur component |

Use one `paseo-storage` Flux Kustomization with `APP=paseo` and UID/GID 1000,
producing one PVC, Restore, SnapshotPolicy and SnapshotSchedule.
Use `ceph-block`, ReadWriteOnce, `kopia-nas` and existing hourly retention
defaults. Exclude only `/lost+found`, `/home/.cache/*` and `/home/.npm/*`;
preserve Git metadata, untracked work and credentials. Backups therefore contain
sensitive provider/GitHub tokens and must retain existing repository protections.
The workload depends on `paseo-storage`. Capacity and backup/restore history
are shared: neither home capacity isolation nor independent restores remain.
Do not alter shared kopiur components. Single replica, `Recreate`; no HPA for
shared mutable sessions/RWO.

Before seed, a non-root `prepare-storage` init container using the same image
mounts the full PVC at `/data`, and creates `home`/`workspace` with UID/GID 1000
and umask 077. `fsGroup: 1000` supplies group write access to the Ceph volume
root. Existing directories are preserved and checked for write/search access;
symlinks or inaccessible paths fail closed, without chown/chmod. Only this
preparation container mounts the root; seed and daemon mount both subPaths.
Do not rely on kubelet-created directories having UID-1000 ownership and do
not add a root/privileged permission fixer. Verify CSI/fsGroup on first live use.

Run seeding in a non-root init container using the same image. Disable Git
terminal prompts for public clones; clone into a temporary sibling, then rename
only after success and a second absence check. Fail clearly on network failure,
clean only the temporary directory owned by that invocation, and retry on the
next init run. Existing destinations remain byte-for-byte unchanged.

Start with requests 250m CPU / 1Gi memory, limits 2 CPU / 4Gi memory. Startup
probe allows 300 seconds (5-second period, 60 failures); readiness and liveness
use `/api/health:6767`, with liveness less aggressive (30-second period, three
failures). Health is not proof of provider authentication or agent usability.
Use existing namespace ingress policy; no widening of ingress or new public
exposure. Existing default-allow egress supports GitHub/provider authentication;
agents remain a trust boundary with access to all mounted workspace and tokens.

## Activation, login and rollback

Keep the app absent from `ai/kustomization.yaml` until a tested image digest
exists and the separately approved real SOPS password Secret is included in the
app resources. No placeholder password and no broken active Secret reference.
Secret absence/empty content must fail closed even if this gate is bypassed.
Obtain separate approval for SOPS creation/edit, git push/image publication,
and live reconcile. Merge can trigger Flux automatically: it is an activation
boundary, not a substitute for deployment approval.

The implementation runbook will use an interactive non-root pod shell:
`gh auth login --hostname github.com --git-protocol https --web`,
`gh auth setup-git`, then `gh auth status`. Do not print tokens. If no keyring
exists, gh may persist a token under `~/.config/gh`; document that protected
home/backups hold it. Clone rsume with `gh repo clone Tanguille/rsume
/workspace/rsume -- --branch master` only after testing destination absence.
No automatic private clone or stored GitHub credential in YAML/image layers.
Run `opencode auth login`, `claude` login flow, `codex login` and `hermes setup`
interactively; confirm the chosen provider's headless/device flow and persistent
paths. Never bypass provider policy or require a workstation token copy.

After approved deployment, verify internal TLS/WebSocket login, one authorized
session per provider, persistent auth and workspace state across a controlled
restart, plus the shared backup policy. Live restart/snapshot/restore operations
need explicit authorization. Roll back by reverting the workload image digest
and config change through GitOps, retaining the PVC. Do not delete/prune storage
to roll back. If a release migrates state incompatibly, stop and use the
approved kopiur restore workflow for both subdirectories together; image
rollback alone is not data rollback. Suspend all writers before shared restore.

## Evidence and remaining gates

Inspected repository peers: `kubernetes/apps/ai/opencode`,
`kubernetes/apps/ai/hermes`, `kubernetes/apps/ai/networkpolicy.yaml`,
`kubernetes/components/kopiur`, `docker/talos-kernel/Dockerfile`,
`.github/workflows/talos-kernel.yaml`, `.github/workflows/docker-lint.yaml`.
Upstream sources: [Paseo Docker](https://github.com/getpaseo/paseo/blob/v0.13.0/docs/docker.md),
[entrypoint](https://github.com/getpaseo/paseo/blob/v0.13.0/docker/base/rootfs/usr/local/bin/paseo-docker-entrypoint),
[custom providers](https://github.com/getpaseo/paseo/blob/v0.13.0/docs/custom-providers.md),
[Hermes ACP](https://hermes-agent.nousresearch.com/docs/user-guide/features/acp),
[Hermes image](https://github.com/NousResearch/hermes-agent/blob/v0.22.5/Dockerfile),
[PM build interface](https://github.com/NousResearch/hermes-agent/blob/v0.22.5/pm/build_env.py).
Release APIs and npm metadata confirmed the candidate versions above; the
annotated Hermes release tag was peeled to its commit.

Open implementation gates: resolve registry/archive integrity pins; prove Hermes
PM/source runtime works on the Paseo Debian base with fresh mounted HOME and no
startup downloads; verify Python SQLite is at least 3.51.3 (upstream warns about
older WAL corruption); prove auth protocol behavior and gVisor compatibility;
confirm GHCR package pull visibility. If any gate fails, report it rather than
silently changing the design. No image build or live validation was performed
while preparing these documents.
