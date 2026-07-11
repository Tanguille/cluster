# Common Operations

**When to use:** validation, tooling, add app, new application, upgrade, SOPS, secrets, encrypt, debug, troubleshooting, logs, backup, restore, volsync, snapshot.

Step-by-step procedures for frequent cluster tasks.

## Validation and tooling

- Run `flux`, `helm`, `kubectl`, `kustomize`, `sops`, `age`, `talhelper`, `talosctl`, `yq`, `jq`, and `shellcheck` through `mise exec -- <command>`.
- Kubernetes or mixed changes: `bash .agents/skills/pr-review/scripts/validate-pr.sh` (Kustomize and shellcheck).
- Shell-only changes: `mise exec -- shellcheck` on every touched `*.sh`.
- Documentation-only changes: run `git diff --check` and verify every changed local reference exists.

## Adding a new application

Use [add-app-to-cluster](skills/add-app-to-cluster/SKILL.md) skill for full procedure.

1. For a new namespace, create `kubernetes/apps/<namespace>/kustomization.yaml` with `namespace: <ns>` and component `../../components/common`; existing namespaces need no namespace step
2. Add OCIRepository if external
3. Create app in `kubernetes/apps/<namespace>/<app>/`
4. Add Kustomization in appropriate `ks.yaml`
5. Run validation on the new app (`<namespace>`, `<app-name>`):
   - `mise exec -- kustomize build kubernetes/apps/<namespace>/<app-name>/app/` (build each subdir that contains a kustomization.yaml)
   - Or: `bash .agents/skills/pr-review/scripts/validate-pr.sh`

## Secrets management (SOPS)

1. Create unencrypted file first
2. Encrypt with: `sops --encrypt --in-place <file>`
3. Or create with: `sops <file>.yaml` (edits encrypted)

Never commit plaintext secrets or the age key. Use placeholders so I can add the secrets manually.

Post-quantum age (age1pq1) is supported; see [sops-post-quantum.md](../docs/sops-post-quantum.md) for testing and migration.

## Debugging

Use [debug-cluster](skills/debug-cluster/SKILL.md) skill for structured 5-Whys analysis and troubleshooting.

## Backup & Restore

Use [backup-restore](skills/backup-restore/SKILL.md) skill for VolSync Kopia operations.

## Other skills

See the [skill catalog](../AGENTS.md#load-context-on-demand) for git-worktree-isolation, k8s-at-home-research, pr-review, and prometheus-cluster-health.
