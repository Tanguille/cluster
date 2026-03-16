# AGENTS.md - Agent Coding Guidelines

This file follows the **[AGENTS.md format](https://agents.md)**. Keep it **short** (~50–80 lines). Detailed context lives in **`.agents/`** and is loaded **on demand** by trigger (see table below).

## Overview

**GitOps-based Kubernetes cluster** on Talos Linux; FluxCD for reconciliation. Changes go through this repo, not direct cluster edits.

**Structure:** `kubernetes/` (manifests, HelmReleases), `talos/` (machine configs), `docs/` (useful_commands), `.agents/` (on-demand context). **Tool versions:** `.mise.toml`.

## Commands (run these first)

- **Task:** `mise exec -- task reconcile`, `mise exec -- task talos:generate-config`, `mise exec -- task talos:apply-node IP=...`, `mise exec -- task talos:upgrade-node IP=...`
- **Validate (run before commit):** `mise exec -- kubeconform -strict kubernetes/`, `mise exec -- shellcheck scripts/*.sh`
- **Tools:** flux, helm, kubectl, kustomize, sops, age, talhelper, talosctl, yq, jq, kubeconform, shellcheck — all via `mise exec -- <cmd>`.

## Tool use and context

- **Prefer MCP tools over raw shell** when an MCP tool exists. Use bash only when no suitable MCP tool or for one-off local commands.
- **Use ToolHive MCP servers instead of raw kubectl** for: **flux** (reconcile, Kustomization/HelmRelease), **observability** (Grafana, Prometheus), **homeassistant**, **resources**, **search**. For pod logs use MCP `get_kubernetes_logs` when available.
- **Load only the `.agents/` file(s) whose triggers match the task.** Do not load all `.agents/*.md` upfront.

### .agents/ (load on demand)

| File                     | Trigger keywords                                                                                                            | Purpose                                              |
|--------------------------|-----------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------|
| `learned-preferences.md` | revert, undo, resources, memory, CPU, MCP vs shell                                                                          | User preferences and tool-choice guidance            |
| `learned-workspace.md`   | HTTPRoute, ToolHive, MCPServer, Flux, Talos, Reloader, in-cluster URL, Rook, RBAC, zap                                      | Workspace and CRD facts                              |
| `common-operations.md`   | add app, new application, upgrade, SOPS, secrets, encrypt, debug, troubleshooting, logs, backup, restore, volsync, snapshot | Procedures: add app, upgrade, secrets, debug, backup |
| `worktree-isolation.md`  | worktree, isolated work, parallel agent, experimental branch, feature branch                                                | Git worktree procedure for isolated changes          |

Learned preferences and workspace facts: `.agents/learned-preferences.md`, `.agents/learned-workspace.md`. Update those (or run continual-learning); do not duplicate long lists here.

## Code style

- URLs: use `${SECRET_DOMAIN}` (never hardcode domains). YAML: 2 spaces, LF, DRY with anchors. Secrets: SOPS only; never commit plaintext or age.key. K8s: lowercase-dashes; resources in `kubernetes/apps/<app>/<type>/` with `ks.yaml`. Shell: `set -euo pipefail`, shellcheck.

## Safety and permissions (three-tier)

- **Always:** Read files, list dirs, validation (kubeconform, shellcheck), flux-local test/diff, format/lint.
- **Ask first:** Git push (always ask before any push), push to main, force push, applying to live cluster (`task reconcile`, `flux reconcile`, `talos apply`), decrypting/editing SOPS secrets, deleting resources.
- **Never:** Commit secrets or age.key (no exceptions).

## When stuck

Ask a clarifying question, propose a short plan, or open a draft PR with notes. Do not push large speculative changes without confirmation.

## PR / commit checklist

- **Title:** [Conventional Commits](https://www.conventionalcommits.org/) (e.g. `feat(scope): description`). Run Validate commands above; keep diff small and focused.
