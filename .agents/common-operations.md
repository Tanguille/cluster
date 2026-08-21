# Common Operations

**When to use:** validation, tooling, add app, new application, upgrade, SOPS, secrets, encrypt, debug, troubleshooting, logs, backup, restore, kopiur, snapshot.

Also serves as the authoritative command reference linked from `docs/useful_commands.md`.

Step-by-step procedures for frequent cluster tasks.

## Validation and tooling

- Run `flux`, `helm`, `kubectl`, `kustomize`, `flate`, `sops`, `age`, `talosctl`, `minijinja-cli`, `yq`, `jq`, and `shellcheck` through `mise exec -- <command>`.
- Kubernetes or mixed changes: `bash .agents/skills/pr-review/scripts/validate-pr.sh` (flate — renders Helm charts, not just Kustomization YAML — and shellcheck).
- Shell-only changes: `mise exec -- shellcheck` on every touched `*.sh`.
- Documentation-only changes: run `git diff --check` and verify every changed local reference exists.

## Ceph: `crash ls-new` is a window, not a history

`ceph crash ls-new` only retains entries newer than `mgr/crash/warn_recent_interval` (86400s here),
so its oldest entry is the floor of that window, not when the problem started. Reading a first-seen
date off it under-reports age, in one case by eight months. Use `ceph crash ls` for the real start.

## Ceph: `crash ls-new` is a window, not a history

`ceph crash ls-new` only retains entries newer than `mgr/crash/warn_recent_interval` (86400s here),
so its oldest entry is the floor of that window, not when the problem started. Reading a first-seen
date off it under-reports age, in one case by eight months. Use `ceph crash ls` for the real start.

## Shell: statuses `set -e` does not see

`set -euo pipefail` does **not** catch a failure in a process substitution, or in a command
substitution used as an argument. The producer's exit status is discarded and the consumer runs on
partial or empty input, so the script succeeds and emits a plausible-looking artifact.

Four instances of this were found in one evening, in two people's code:

| Shape | What it produced |
| --- | --- |
| `talosctl machineconfig patch <(render ...)` | a machine config with `machine.type` missing, ready to `apply-node` |
| `minijinja-cli ... <(sops -d ...)` | a config rendered against an empty secret context |
| `installer/$(yq '.id' ...)` | the literal string `null` baked into an install image |
| `mapfile -t args < <(yq ...)` | an installer published with no kernel args and no extensions |

Write it as a bare assignment instead, so `set -e` aborts before anything downstream runs:

```bash
value="$(producer ...)"          # aborts here on failure
[ -n "$value" ] || { echo "producer returned nothing" >&2; exit 1; }
consumer <(printf '%s' "$value")
```

Assigning first also means a failure emits **nothing** to stdout. That matters when a caller pipes
the output somewhere consequential: `wait $!` on the process substitution recovers the status, but
only after the consumer has already streamed a partial result to whoever was reading.

It bites **verification code** too, and there it is worse: a check that reads the wrong status
reports a false PASS, laundering the bug as verified. `rc=$?` after a pipe reads the last stage,
not the one that failed — use `${PIPESTATUS[0]}`, or do not pipe the command under test.

Two related traps in the same family:

- A tool that exits 0 while producing nothing useful. `yq` prints `null` and exits 0 for a missing
  key; use `yq -e`. `jq -r '.id'` prints `null`; use `jq -er '.id | strings | select(length > 0)'`.
- `eval "$(cmd)"` reports **eval's** status, not `cmd`'s. Capture, check, then eval.

## Adding a new application

Use [add-app-to-cluster](skills/add-app-to-cluster/SKILL.md) skill for full procedure.

1. For a new namespace, create `kubernetes/apps/<namespace>/kustomization.yaml` with `namespace: <ns>` and component `../../components/common`; existing namespaces need no namespace step
2. Add OCIRepository if external
3. Create app in `kubernetes/apps/<namespace>/<app>/`
4. Add Kustomization in appropriate `ks.yaml`
5. Run validation on the new app: `bash .agents/skills/pr-review/scripts/validate-pr.sh` (or `mise exec -- flate test all` directly, which renders the HelmRelease too — `kustomize build` alone doesn't)

## Secrets management (SOPS)

1. Create unencrypted file first
2. Encrypt with: `sops --encrypt --in-place <file>`
3. Or create with: `sops <file>.yaml` (edits encrypted)

Never commit plaintext secrets or the age key. Use placeholders so I can add the secrets manually.

Post-quantum age (age1pq1) is supported; see [sops-post-quantum.md](../docs/sops-post-quantum.md) for testing and migration.

## Debugging

Use [debug-cluster](skills/debug-cluster/SKILL.md) skill for structured 5-Whys analysis and troubleshooting.

## Backup & Restore

Use [backup-restore](skills/backup-restore/SKILL.md) skill for kopiur Kopia operations.

## Other skills

See the [skill catalog](../AGENTS.md#load-context-on-demand) for git-worktree-isolation, k8s-at-home-research, pr-review, and prometheus-cluster-health.
