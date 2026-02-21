---
name: git-worktree-isolation
description: Use git worktrees for isolated, parallel work. Creates temporary worktrees for independent agent tasks without affecting the main working directory. Always use this skill for any task that involves making changes to the repository to ensure: (1) Parallel agent work is isolated and doesn't conflict, (2) Experimental changes don't affect the main working directory, (3) The agent can work independently without coordination, (4) Easy cleanup when done.
---

# Git Worktree Isolation

## Create Worktree

```bash
# Ensure base branch is up to date
git fetch origin
git checkout <branch>
git pull origin <branch>

# List existing worktrees
git worktree list

# Create a detached worktree
git worktree add -b <branch> --detach work/isolated-<task>-<timestamp> <branch>

# Navigate to the worktree
cd work/isolated-<task>-<timestamp>
```

## Validate

```bash
# Format YAML (use specific paths per project conventions)
yamlfmt -w kubernetes/
yamlfmt -w .

# Validate Kubernetes manifests
kubeconform -strict kubernetes/

# Test Flux reconciliation
flux-local test --all-namespaces --enable-helm kubernetes/flux/cluster
```

## Cleanup

```bash
# Navigate out of worktree first
cd /path/to/main/repo

# Remove the worktree
git worktree remove work/isolated-<task>-<timestamp>

# Delete the branch
git branch -D <branch>
```

## Permission

Add "git-worktree-isolation" to `opencode.json`'s `"allow"` section to grant all agents automatic access.
