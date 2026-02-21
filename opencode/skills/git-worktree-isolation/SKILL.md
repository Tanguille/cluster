# Skill: git-worktree-isolation

## Description

Use git worktrees for isolated, parallel work. Creates temporary worktrees for independent agent tasks without affecting the main working directory.

## When to use

**Always use this skill for any task that involves making changes to the repository.** This ensures:

- Parallel agent work is isolated and doesn't conflict
- Experimental changes don't affect the main working directory
- The agent can work independently without coordination
- Easy cleanup when done

## How to create a worktree

```bash
# Ensure base branch is up to date
git fetch origin
git checkout <branch>
git pull origin <branch>

# List existing worktrees
git worktree list

# Create a detached worktree and checkout the target branch
git worktree add -b <branch> --detach work/isolated-<task>-<timestamp> <branch>

# Navigate to the worktree
cd work/isolated-<task>-<timestamp>
```

## Validation workflow

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

# Optionally delete the branch
git branch -D <branch>
```

## Permission

To grant all agents access automatically, add "git-worktree-isolation" to `opencode.json`'s `"allow"` section.
