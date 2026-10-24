# Paseo preinstalled-agent image

This amd64-only child image pins the Paseo `0.13.0` multi-arch base index
(`sha256:9832cb6bcc904bb7fd6c0b22f1880cde38b592a26ecd38ba66c7fcc76fa0d43e`)
and preinstalls OpenCode, Claude Code, Codex, Hermes ACP, and GitHub CLI. It
keeps npm-installed clients outside the mounted home under `/opt/paseo-npm`,
Hermes source/runtime under `/opt/hermes`, and does not replace Paseo's Node.

| Component | Pin |
| --- | --- |
| OpenCode | `opencode-ai@1.18.35` |
| Claude Code | `@anthropic-ai/claude-code@2.1.310` |
| Codex | `@openai/codex@0.164.0` |
| Hermes | `v0.22.5`, commit `bf0203a794a07aac3ff764972ab9087baa4e4f4e`; codeload SHA-256 `0dc2cca54657d162845a77f634e107c61c3fb0bd6c8505212aa2e200591b7cc9` |
| GitHub CLI | `2.103.2`; archive SHA-256 `62ccf747115ddc61237efffdde66a3c8f70ba249433dc64f70801089b59c59c8` |

All three npm versions and integrity values are fixed in `package-lock.json`.
Hermes is assembled as a sealed ACP runtime from its pinned PM/uv locks. The
build checks SQLite `>=3.51.3`; it does not include Hermes' desktop, messaging,
or s6 services.

## Runtime contract

- Run the daemon and `/usr/local/bin/paseo-seed` as UID/GID `1000:1000` with
  writable mounts at `/home/paseo` and `/workspace`.
- `PASEO_PASSWORD` is mandatory and nonempty. The wrapper runs under upstream
  `tini`, rejects unexpected daemon commands, and then executes the upstream
  Paseo entrypoint.
- The version-1 config seeds the Hermes provider as ACP
  (`command: ["hermes", "acp"]`) only when no config exists. Workspace seeding
  is create-only for the public `main` branches of `Tanguille/cluster` and
  `Tanguille/mesh-organiser`; it never clones private `rsume`.
- No provider credentials are included. The ACP initialize check only proves
  local protocol startup with networking disabled; it does not prove provider
  authentication or inference. gVisor compatibility is unverified.

## Local verification

The single image smoke command is:

```bash
bash scripts/paseo-smoke.sh paseo:check
```

It accepts one local image reference, uses disposable test state, never prints
the generated daemon test password, and checks executable versions/paths, ACP
initialize, seeding/non-overwrite and concurrent publication races, and daemon
health/host/auth behavior on fresh root-start and UID-1000 mounts. Its results
are recorded below after an actual focused build and run; no registry digest is
claimed for a local image.

### Evidence log

- `shellcheck`, `hadolint`, `actionlint`, shell syntax, and `git diff --check`:
  see `../../.superpowers/sdd/2026-10-24-paseo/task-1-report.md` for current
  results.
- Focused build passed:
  `docker build --platform linux/amd64 -t paseo:round1-check docker/paseo`.
  Local image ID: `sha256:0e2484a9c0d45dc48ecc5fe4faea96d47cab198745af74a8a11f07ac8df16d01`
  (6,903,687,359 bytes; not a registry digest).
- Focused smoke passed:
  `TMPDIR=/tmp/opencode bash scripts/paseo-smoke.sh paseo:round1-check`
  on host UID 1000 (exit 0; 66 assertions). The same smoke also passed from a
  disposable caller container running as UID 1001 with local Docker access,
  read-only source, and dedicated temporary storage (exit 0; 66 assertions).
  Pinned CLI checks, Node preservation, SQLite 3.53.1,
  no-network ACP initialize, PM drift check, seed preservation/failure/retry and
  concurrent directory/dangling-symlink races, daemon health/host/auth, and both
  UID modes passed. The generated test password was not logged.
- Registry publication/digest and GitHub provenance: not performed; the trusted
  workflow publishes only after its smoke step and attests the pushed digest.
