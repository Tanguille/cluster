# AGENTS.md - Agent Coding Guidelines

Follow the **[AGENTS.md format](https://agents.md)**. Keep this file short (~50-80 lines); load detailed context from `.agents/` only when task triggers match.

## Overview

**GitOps-based Kubernetes cluster** on Talos Linux with FluxCD reconciliation. Make changes through this repo; avoid direct cluster edits.

**Structure:** `kubernetes/` (manifests, HelmReleases), `talos/` (machine configs), `docs/` (runbooks), `.agents/` (on-demand context).
**Tool versions:** `.mise.toml`.

## Commands (run these first)

- **Task runner:** `mise exec -- task reconcile`, `mise exec -- task talos:generate-config`, `mise exec -- task talos:apply-node IP=...`, `mise exec -- task talos:upgrade-node IP=...`
- **Validate before commit:** `mise exec -- kubeconform -strict kubernetes/`, `mise exec -- shellcheck scripts/*.sh`
- **Tooling:** run `flux`, `helm`, `kubectl`, `kustomize`, `sops`, `age`, `talhelper`, `talosctl`, `yq`, `jq`, `kubeconform`, `shellcheck` via `mise exec -- <cmd>`.

## Tool use and context

- **Prefer tools over memory:** when a tool can provide current data, use it first. Hedge only on tool errors, ambiguity, or empty results.
- **ToolHive workflow is mandatory when available:** if both `find_tool` and `call_tool` exist, always use this sequence:
  1. Run `find_tool` with a relevant query.
  2. Identify the correct tool name and required parameters from the result.
  3. Run `call_tool` with those parameters.
  4. Interpret the result and respond naturally; never return raw JSON/tool output.
- **Load context on demand:** read only `.agents/` files whose trigger keywords match the task; do not preload all files.

### .agents/ (load on demand)

- `learned-preferences.md`: use for revert/undo, resource usage, MCP vs shell, `find_tool`/`call_tool`, and confidence guidance.
- `learned-workspace.md`: use for HTTPRoute, ToolHive, MCPServer, Flux, Talos, Reloader, in-cluster URLs, Rook, RBAC, and zap context.
- `common-operations.md`: use for app add/upgrade, SOPS/secrets, debugging/logs, and backup/restore/volsync procedures.

`learned-preferences.md` and `learned-workspace.md` are authoritative and updated by continual learning.

**ToolHive operator/CRD upgrades:** follow `.agents/skills/toolhive-upgrades/SKILL.md` (OpenSkills registry: `toolhive-upgrades`). That workflow requires a **code-reviewer subagent** pass before declaring the upgrade complete.

## Learned User Preferences

- Prefer fixing root causes (disk headroom, capacity, correct config) over silencing alerts or only lowering warning thresholds.

## Code style

- URLs: use `${SECRET_DOMAIN}`; never hardcode domains.
- YAML: 2-space indent, LF endings. Use anchors (`&`, `*`) only within one `---` document (Kustomize does not resolve across documents).
- For `*-opt` `MCPServer` objects, keep them in the same file as the primary and fully duplicate `spec` (no cross-document `<<:`/`*spec_anchor`).
- Secrets: SOPS only; never commit plaintext secrets or `age.key`.
- Kubernetes naming: lowercase-dashes; place resources in `kubernetes/apps/<app>/<type>/` with `ks.yaml`.
- Shell scripts: `set -euo pipefail` and pass `shellcheck`.

## Safety and permissions (three-tier)

- **Always:** Read files, list dirs, validation (kubeconform, shellcheck), flux-local test/diff, format/lint.
- **Ask first:** Git push (always ask before any push), push to main, force push, applying to live cluster (`task reconcile`, `flux reconcile`, `talos apply`), decrypting/editing SOPS secrets, deleting resources.
- **Never:** Commit secrets or age.key (no exceptions).

## When stuck

Ask a clarifying question, propose a short plan, or open a draft PR with notes. Avoid large speculative changes without confirmation.

## PR / commit checklist

- **Title:** [Conventional Commits](https://www.conventionalcommits.org/) (for example `feat(scope): description`).
- Run validation commands above and keep diffs small and focused.
