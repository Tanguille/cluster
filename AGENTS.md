# AGENTS.md - Agent Coding Guidelines

This file follows the **[AGENTS.md format](https://agents.md)**. Keep this file **short** (~50–80 lines); detailed and learned context lives in **`.agent/`** and is loaded **on demand** by trigger (see table below). Do not paste long bullet lists here—use smaller, task-specific docs so context stays relevant.

## Tool use and context

- **Prefer MCP tools over raw shell** when an MCP tool exists for the task. Use bash only when no suitable MCP tool exists or for one-off local commands.
- **Use ToolHive MCP servers instead of raw kubectl** when the task involves:
  - **flux** — reconcile, Kustomization/HelmRelease state, cluster/operator tasks (use flux tools, not `kubectl`/`flux reconcile` in shell).
  - **observability** — Grafana dashboards, Prometheus/metrics (use observability tools).
  - **homeassistant** — Home Assistant control, entities, automations (use homeassistant tools).
  - **resources** — GitHub, sequential thinking, KaraKeep bookmarks (use resources tools).
  - **search** — web search (use search tools).
  For debugging (e.g. pod logs), use the MCP `get_kubernetes_logs` tool when available rather than `kubectl logs` in the shell.
- **Load only the `.agent/` file(s) whose triggers match the task.** Do not load all `.agent/*.md` upfront.

### .agent/ (load on demand)

| File                     | Trigger keywords                                                                                                            | Purpose                                              |
|--------------------------|-----------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------|
| `learned-preferences.md` | revert, undo, resources, memory, CPU, MCP vs shell                                                                          | User preferences and tool-choice guidance            |
| `learned-workspace.md`   | HTTPRoute, ToolHive, MCPServer, Flux, Talos, Reloader, in-cluster URL, Rook, RBAC, zap                                      | Workspace and CRD facts                              |
| `common-operations.md`   | add app, new application, upgrade, SOPS, secrets, encrypt, debug, troubleshooting, logs, backup, restore, volsync, snapshot | Procedures: add app, upgrade, secrets, debug, backup |
| `worktree-isolation.md`  | worktree, isolated work, parallel agent, experimental branch, feature branch                                                | Git worktree procedure for isolated changes          |

Learned preferences and workspace facts live in `.agent/learned-preferences.md` and `.agent/learned-workspace.md`. Update those files (or run continual-learning); do not duplicate long lists here.

## Overview

**GitOps-based Kubernetes cluster** on Talos Linux; FluxCD for reconciliation. Changes go through this repo, not direct cluster edits.

**Structure:** `kubernetes/` (manifests, HelmReleases), `talos/` (machine configs), `docs/` (useful_commands, common-operations), `.agent/` (on-demand context).

## Commands (summary)

- **Task:** `task`, `task reconcile`, `task talos:generate-config`, `task talos:apply-node IP=...`, `task talos:upgrade-node IP=...`
- **Validate:** `kubeconform -strict -original-location kubernetes/`, `yamlfmt -w kubernetes/`, `shellcheck scripts/*.sh`
- **Flux local (PRs):** `flux-local test --all-namespaces --enable-helm --path kubernetes/flux/cluster`
- **Tools:** mise; run via `mise exec -- <cmd>` (flux, helm, kubectl, kustomize, sops, age, talhelper, talosctl, yq, jq, kubeconform, yamlfmt).

## Code style (short)

- URLs: use `${SECRET_DOMAIN}` (never hardcode domains). YAML: 2 spaces, LF, `yamlfmt`, DRY with anchors. Secrets: SOPS only; never commit plaintext or age.key. K8s: lowercase-dashes; resources in `kubernetes/apps/<app>/<type>/` with `ks.yaml`. Shell: `set -euo pipefail`, shellcheck.

## Safety and permissions

- **Allowed without prompt:** Read files, list dirs, validation (kubeconform, yamlfmt, shellcheck), flux-local test/diff, format/lint.
- **Ask first:** Git push (including force), applying to live cluster (`task reconcile`, `flux reconcile`, `talos apply`), decrypting/editing SOPS secrets, deleting resources.

## When stuck

- Ask a clarifying question, propose a short plan, or open a draft PR with notes. Do not push large speculative changes without confirmation.

## PR / commit checklist

- **Title:** [Conventional Commits](https://www.conventionalcommits.org/) (e.g. `feat(scope): description`). Lint/validate green before commit; diff small and focused. Never commit secrets or age.key; do not force push unless the user asked for it.
