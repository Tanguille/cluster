# Paseo deployment design

## Approved revision — 2026-10-25

Use the existing community image
`ghcr.io/joryirving/paseo:0.13.0@sha256:4f4034e053e45b25b69b6072149267b035062543304e07166c33801d0d8b8805`.
No custom image or image build/publication workflow. OpenCode is bundled;
Hermes is installed manually once into persistent HOME after deployment using
the official installer pinned to `bf0203a794a07aac3ff764972ab9087baa4e4f4e`.
Claude and Codex are deferred and disabled. No startup dependency installation.

Bypass the community `paseo-start` command: it requires LiteLLM credentials and
rewrites config. Explicitly launch tini → ConfigMap password guard → upstream
`paseo-docker-entrypoint` with no arguments. Missing/empty password fails closed;
no LiteLLM credentials are required. Kubernetes Secret `ai/paseo-secret`, key
`PASEO_PASSWORD`, is required (omitted optional defaults false).

## Configuration and persistence

ConfigMap supplies seed script, password guard and version-1 default config.
Seed preserves every existing config/checkout, including symlinks and dirty
work. Only absent public `Tanguille/cluster` and `Tanguille/mesh-organiser`
main branches are cloned through temporary siblings and no-clobber publication.
Private `Tanguille/rsume` master is cloned manually only after interactive gh
login. Config remains writable; defaults never overwrite operator changes.
Hermes ACP command is `/home/paseo/.local/bin/hermes acp`; its absence must not
prevent the daemon starting so the operator can install it.

Keep the approved single 70Gi ceph-block RWO PVC `paseo`. `paseo-storage`
uses the shared kopiur component, UID/GID 1000, repository `kopia-nas`, hourly
schedule and existing retention. Exclude only `/lost+found`, `/home/.cache/*`,
`/home/.npm/*`; credentials and Git state remain backed up together.
Home/workspace share capacity and restore history, not separate quotas.
Non-root preparation mounts the root and creates `home`/`workspace` before
seed/daemon subPath mounts at `/home/paseo` and `/workspace`. Preserve existing
paths; reject symlinks or inaccessible directories, with no root chown init.

One Recreate replica in `ai`, gVisor, UID/GID/fsGroup 1000, RuntimeDefault
seccomp, all capabilities dropped, no privilege escalation, service-account
token, RBAC or host mounts. Requests 250m/1Gi, limits 2 CPU/4Gi. Health probes
use `/api/health:6767`; startup allows 300s, liveness period 30s/3 failures.
Internal-only `paseo.${SECRET_DOMAIN}` through `network/envoy-internal`.
Relay disabled; no changes to sibling applications or network policies.

## Approval and acceptance boundaries

Keep the namespace entry detached until an approved real SOPS password is
included and deployment is approved. No custom-image publication gate remains.
Push, SOPS and deployment require separate permission; merge can trigger Flux.
Verify internal TLS/WebSocket password rejection/success, effective UID and
mount ownership, OpenCode session, manual Hermes install/ACP check/login/session,
then separately approved restart persistence and shared backup success.
Health/installed binaries are not authenticated inference evidence.
Rollback image/config through GitOps without pruning PVC/storage/backup roots;
incompatible state needs an approved shared restore with all writers stopped.
