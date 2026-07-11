# AGENTS.md

## Overview

GitOps-based Kubernetes cluster on Talos Linux with FluxCD reconciliation. Make changes through this repository; avoid direct cluster edits.

**Structure:** `kubernetes/` (manifests), `talos/` (machine configuration), `docs/` (runbooks), `.agents/` (task-specific guidance). Tool versions are pinned in `.mise.toml`.

## Required workflow

- Before any work, verify the branch and worktree, run `git status`, then `git pull --ff-only && git status`. Never assume the branch is current.
- Preserve unrelated changes and follow existing patterns; keep changes small and focused.
- Validate the changed scope before declaring work complete or committing; follow the nearest scoped `AGENTS.md` and [common operations](.agents/common-operations.md).
- Shell scripts use `set -euo pipefail` and must pass shellcheck on every touched `*.sh` (`bash .agents/skills/pr-review/scripts/validate-pr.sh` covers this).
- Use Conventional Commit titles, for example `feat(scope): description`.

## Load context on demand

- [Learned preferences](.agents/learned-preferences.md): tool selection, ToolHive workflow, reversions, resources, and confidence
- [Learned workspace](.agents/learned-workspace.md): cluster-specific Kubernetes, ToolHive, database, storage, Talos, and media facts
- [Common operations](.agents/common-operations.md): validation, app operations, SOPS, debugging, and backup/restore
- [ToolHive upgrades](.agents/skills/toolhive-upgrades/SKILL.md): operator or CRD upgrades; includes the required code-reviewer pass
- [Skill catalog](.agents/skills/): add-app-to-cluster, backup-restore, debug-cluster, git-worktree-isolation, k8s-at-home-research, pr-review, prometheus-cluster-health, toolhive-upgrades — one `SKILL.md` per directory
- [Useful commands](docs/useful_commands.md): flux/task/talos/sops command reference and app-specific runbooks
- [LLM hosting](docs/llm-hosting/): sglang/vLLM tuning constraints and benchmark history for `kubernetes/apps/ai/`

Read only references whose trigger keywords match the task. The learned files are authoritative and maintained by continual learning.

## Safety and permissions (three-tier)

- **Always:** Read-only inspection and local validation, formatting, or linting.
- **Ask first:** Git push (always ask before any push), push to main, force push, applying to live cluster (`task reconcile`, `flux reconcile`, `talos apply`), decrypting/editing SOPS secrets, deleting resources.
- **Never:** Commit secrets or age.key (no exceptions).
