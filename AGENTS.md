# AGENTS.md

## Overview

GitOps-based Kubernetes cluster on Talos Linux with FluxCD reconciliation. Make changes through this repository; avoid direct cluster edits.

- `kubernetes/`: manifests and HelmReleases
- `talos/`: machine configuration
- `docs/`: runbooks and reference documentation
- `.agents/`: task-specific agent guidance
- `.mise.toml`: tool versions

## Required workflow

- Before any work, verify the branch and worktree, run `git status`, then `git pull --ff-only && git status`. Never assume the branch is current.
- Preserve unrelated changes and follow existing patterns; keep changes small and focused.
- Validate the changed scope before declaring work complete or committing; follow the nearest scoped `AGENTS.md` and [common operations](.agents/common-operations.md).
- Use Conventional Commit titles, for example `feat(scope): description`.

## Load context on demand

- [Learned preferences](.agents/learned-preferences.md): tool selection, ToolHive workflow, reversions, resources, and confidence
- [Workspace context index](.agents/learned-workspace.md): routes cluster-specific topics to focused references
- [Common operations](.agents/common-operations.md): validation, app operations, SOPS, debugging, and backup/restore
- [ToolHive upgrades](.agents/skills/toolhive-upgrades/SKILL.md): operator or CRD upgrades; includes the required code-reviewer pass

Read only references whose trigger keywords match the task. These references are authoritative and maintained by continual learning.

## Safety and permissions (three-tier)

- **Always:** Read files, list dirs, validation (shellcheck), flux-local test/diff, format/lint.
- **Ask first:** Git push (always ask before any push), push to main, force push, applying to live cluster (`task reconcile`, `flux reconcile`, `talos apply`), decrypting/editing SOPS secrets, deleting resources.
- **Never:** Commit secrets or age.key (no exceptions).
